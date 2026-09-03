import logging
import math
import os
import secrets
import statistics
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import scorer
import security
from database import get_db, init_db
from lstm_model import FEATURE_NAMES
from models import BehaviorData, Session

# The session's *official* risk_score (the badge, the 40/60/80 gating
# thresholds in the demo, the sessions list) is the median of the last
# SMOOTHING_WINDOW flushes, not the instantaneous per-flush value. A single
# anomalous reading -- e.g. one incidental pause during otherwise fully
# robotic activity -- could previously flip the verdict from "Bot Tespit
# Edildi" straight to "Gercek Kullanici" in one flush. Median-smoothing means
# an anomaly has to persist across multiple flushes to move the session's
# verdict. BehaviorData.risk_score (used by the dashboard's history chart)
# stays the raw, unsmoothed per-flush value so the underlying signal is still
# visible for analysis.
#
# Smoothing only means anything now that a session can have exactly one
# writer: it previously averaged over rows that *any* unauthenticated caller
# could contribute, which let a flagged session be walked back down to
# "Supheli" by flooding it with human-shaped payloads. See security.py.
SMOOTHING_WINDOW = 5

# Server-side decision thresholds. These deliberately mirror the labels in
# scorer.LABELS, but they live here because this is where they are *enforced*
# -- the browser's copy of these numbers is a display detail, not a control.
BLOCK_THRESHOLD = 80.0
STEP_UP_THRESHOLD = 60.0

# Below this, the feature vector is mostly neutral fallbacks rather than
# measurement (see scorer.signal_sufficiency). A near-empty payload scores
# mid-scale by construction, which is correct for a *score* but must not read
# as "cleared" for a payment: a script that submits a form without ever
# moving, clicking or typing produced 44.3 ("Supheli") during the audit and
# sailed under both gates. Too little evidence is a reason to step up, not to
# allow.
MIN_SIGNAL_FOR_AUTO_APPROVE = 0.35

# There is no SMS provider in this MVP, so the step-up code has to be visible
# somewhere for the demo to be operable. When demo mode is on it is returned
# in the response and clearly labelled as such. Turn it off (set to "0") and
# the code is only ever logged server-side.
DEMO_MODE = os.getenv("DEEPCHECK_DEMO_MODE", "1") == "1"

# Raw telemetry (mouse coordinates, click positions, keydown timestamps) is
# behavioural biometric data. Nothing in the product reads it back -- the
# dashboard renders features and scores -- so storing it by default is
# unbounded growth plus a standing privacy liability for no benefit. Opt in
# explicitly when you actually want forensic captures.
STORE_RAW_TELEMETRY = os.getenv("DEEPCHECK_STORE_RAW_TELEMETRY", "0") == "1"

# Browsers enforce the origin allowlist, so "*" meant any site could drive and
# read this API from a visitor's browser. Pairing it with allow_credentials
# was additionally incoherent (the Fetch spec forbids that combination) and
# would have become an any-origin credentialed hole the moment cookie auth was
# added. Configure real origins; default to the local dev pair.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DEEPCHECK_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]

logger = logging.getLogger("deepcheck")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        scorer.get_bundle()
    except FileNotFoundError as exc:
        logger.warning("UYARI: %s", exc)
    yield


app = FastAPI(title="DeepCheck API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Operator-Key"],
)


def _sanitize_for_json(value):
    """Replaces non-finite floats so an error body can actually be serialized.

    FastAPI's default RequestValidationError handler echoes the offending
    input back to the caller. When that input is a JSON NaN or Infinity token
    (which Python's json module accepts even though RFC 8259 forbids them),
    serializing the 422 body raises "Out of range float values are not JSON
    compliant" *inside the error handler* -- so a request that validation
    correctly rejected came back as a 500 with a full traceback in the logs,
    on demand, for free. The validation was never the problem; reporting it
    was.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": _sanitize_for_json(exc.errors()),
            "message": "Geçersiz davranış verisi.",
        },
    )


def _client_key(request: Request) -> str:
    """Rate-limit key for a caller.

    Uses the socket peer address. X-Forwarded-For is deliberately *not*
    trusted: it is caller-controlled, so honouring it without a vetted proxy
    in front would let anyone reset their own limit by rotating the header.
    Behind a real load balancer, terminate the header at the proxy and pass
    the verified address through instead.
    """
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, limiter: security.RateLimiter) -> None:
    if not limiter.allow(_client_key(request)):
        raise HTTPException(
            status_code=429, detail="Çok fazla istek gönderildi, lütfen bekleyin."
        )


def require_session(authorization: Annotated[str | None, Header()] = None) -> str:
    """Resolves the caller's session id from their signed bearer token.

    This is the fix for the audit's most damaging finding. The session id used
    to come from the request body, so any caller could write behaviour under
    any id -- poisoning a stranger's verdict, laundering their own flagged
    session back down, or forging unlimited fake sessions into the SOC
    dashboard. Now the id is only ever derived from a token this server
    signed, and a client cannot name a session it was not given.
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    session_id = security.verify_session_token(token)
    if session_id is None:
        raise HTTPException(
            status_code=401, detail="Geçersiz veya süresi dolmuş oturum belirteci."
        )
    return session_id


def require_operator(x_operator_key: Annotated[str | None, Header()] = None) -> None:
    """Gate for the SOC dashboard's read endpoints.

    /api/sessions enumerated every session's verdict and /api/score/{id}
    exposed each session's confidence, SHAP attribution and per-flush feature
    history -- to anyone, unauthenticated.
    """
    if not security.verify_operator_key(x_operator_key):
        raise HTTPException(status_code=401, detail="Operatör anahtarı geçersiz.")


# Behavioral telemetry is attacker-controlled input and must be typed and
# bounded at the boundary, not deep inside NumPy math.
#
# `list[dict]` accepted literally anything, which produced two distinct
# failures. Hard crashes: {"t": "abc"} reached a subtraction and raised
# TypeError -> unhandled 500. And silent corruption: JSON's NaN/Infinity
# literals (which Python's json module accepts even though RFC 8259 forbids
# them) flowed into np.mean -> np.clip -> a NaN risk_score, which /api/analyze
# COMMITTED to Postgres before failing to serialize the response -- leaving a
# row that broke every later GET /api/sessions for every user, permanently.
# Physically impossible values (negative durations, 1e300 hesitations,
# infinite coordinates) were likewise accepted and used to steer the score.
#
# allow_inf_nan=False is what rejects the NaN/Infinity literals; the ge/le
# bounds reject the physically impossible ones; max_length caps the CPU and
# storage cost of a single request (a 200k-point trajectory measured 254ms of
# blocking feature extraction).
COORD_LIMIT = 1e5
MAX_TIMESTAMP_MS = 4_102_444_800_000  # year 2100, in epoch ms
MAX_HESITATION_MS = 3_600_000  # 1 hour

Timestamp = Annotated[int, Field(ge=0, le=MAX_TIMESTAMP_MS)]
Coordinate = Annotated[float, Field(ge=-COORD_LIMIT, le=COORD_LIMIT, allow_inf_nan=False)]
HesitationMs = Annotated[float, Field(ge=0, le=MAX_HESITATION_MS, allow_inf_nan=False)]
FocusTimestamp = Annotated[float, Field(ge=0, le=MAX_TIMESTAMP_MS, allow_inf_nan=False)]


class _TelemetryEvent(BaseModel):
    # "ignore" rather than "forbid": a browser may hold a cached older SDK
    # build that sends an extra field, and rejecting the whole flush over it
    # would blind us to that session entirely. Unknown keys are dropped; the
    # keys we actually read are all strictly typed below.
    model_config = ConfigDict(extra="ignore")


class MousePoint(_TelemetryEvent):
    x: Coordinate
    y: Coordinate
    t: Timestamp


class ClickEvent(_TelemetryEvent):
    x: Coordinate
    y: Coordinate
    t: Timestamp


class ScrollEvent(_TelemetryEvent):
    scrollY: Annotated[float, Field(ge=-1e7, le=1e7, allow_inf_nan=False)]
    t: Timestamp


class KeyEvent(_TelemetryEvent):
    t: Timestamp


class AnalyzeRequest(BaseModel):
    # NOTE: no session_id field. The session is whatever the bearer token
    # says it is; accepting a client-supplied id here is the vulnerability
    # this endpoint used to have.
    mouse_trajectory: list[MousePoint] = Field(default_factory=list, max_length=2000)
    click_timing: list[ClickEvent] = Field(default_factory=list, max_length=500)
    scroll_events: list[ScrollEvent] = Field(default_factory=list, max_length=1000)
    hesitation_intervals: list[HesitationMs] = Field(default_factory=list, max_length=500)
    focus_changes: list[FocusTimestamp] = Field(default_factory=list, max_length=200)
    key_events: list[KeyEvent] = Field(default_factory=list, max_length=1000)


class AnalyzeResponse(BaseModel):
    session_id: str
    risk_score: float
    label: str
    confidence: float
    shap_explanation: list[dict]
    response_time_ms: float
    signal_sufficiency: float


class SessionResponse(BaseModel):
    session_id: str
    token: str
    expires_in: int


class TransactionRequest(BaseModel):
    amount: Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)] = 0.0


class VerifyRequest(BaseModel):
    code: Annotated[str, Field(min_length=6, max_length=6, pattern=r"^\d{6}$")]


# --------------------------------------------------------------------------
# Step-up challenge store
# --------------------------------------------------------------------------
# In-process and therefore per-worker: a challenge issued by one uvicorn
# worker cannot be verified by another. Acceptable for the demo, but a real
# deployment needs shared storage (Redis/Postgres) -- the same caveat that
# applies to the rate limiter.
_challenges: dict[str, dict] = {}
_challenges_lock = threading.Lock()

CHALLENGE_TTL_SECONDS = 300
MAX_CHALLENGE_ATTEMPTS = 5


def _issue_challenge(session_id: str) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    with _challenges_lock:
        _challenges[session_id] = {
            "code": code,
            "expires_at": time.time() + CHALLENGE_TTL_SECONDS,
            "attempts": 0,
        }
    logger.info("Step-up challenge issued for session %s", session_id)
    return code


def _verify_challenge(session_id: str, provided: str) -> tuple[bool, str]:
    with _challenges_lock:
        entry = _challenges.get(session_id)
        if entry is None:
            return False, "Doğrulama talebi bulunamadı."
        if time.time() > entry["expires_at"]:
            del _challenges[session_id]
            return False, "Doğrulama kodunun süresi doldu."

        entry["attempts"] += 1
        # Bounded attempts: a 6-digit code is only 10^6 wide, so an unbounded
        # verify endpoint is brute-forceable in minutes.
        if entry["attempts"] > MAX_CHALLENGE_ATTEMPTS:
            del _challenges[session_id]
            return False, "Çok fazla hatalı deneme, doğrulama iptal edildi."

        if not secrets.compare_digest(entry["code"], provided):
            return False, "Doğrulama kodu hatalı."

        del _challenges[session_id]
        return True, "Doğrulama başarılı."


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.post("/api/session", response_model=SessionResponse)
async def create_session(request: Request, db: AsyncSession = Depends(get_db)):
    """Handshake: the server mints the session id and signs a token for it."""
    _rate_limit(request, security.session_limiter)

    session_id, token, ttl = security.issue_session()
    db.add(Session(id=session_id))
    await db.commit()

    return SessionResponse(session_id=session_id, token=token, expires_in=ttl)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    session_id: Annotated[str, Depends(require_session)],
    db: AsyncSession = Depends(get_db),
):
    _rate_limit(request, security.analyze_limiter)

    # Back to plain dicts: scorer.extract_features() reads these with .get(),
    # and keeping that dict interface means train_model.py can keep feeding it
    # simulated payloads directly (the shared extraction path that keeps
    # training and serving in sync).
    raw = {
        "mouse_trajectory": [p.model_dump() for p in payload.mouse_trajectory],
        "click_timing": [c.model_dump() for c in payload.click_timing],
        "scroll_events": [s.model_dump() for s in payload.scroll_events],
        "hesitation_intervals": payload.hesitation_intervals,
        "focus_changes": payload.focus_changes,
        "key_events": [k.model_dump() for k in payload.key_events],
    }

    try:
        # compute_risk is ~50ms of pure CPU (sklearn + SHAP + torch). Called
        # directly in this async handler it blocks the single event-loop
        # thread, stalling every other in-flight request including
        # /api/health -- measured event-loop stalls up to 1.5s at 20
        # concurrent flushes, capping a worker at ~13 req/s. Running it in the
        # threadpool keeps the loop free to accept and finish other work.
        result = await run_in_threadpool(scorer.compute_risk, raw)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        # Never surface a traceback to an unauthenticated caller: it is both an
        # error-log flood and a fingerprinting oracle. Log server-side instead.
        logger.exception("compute_risk failed for session %s", session_id)
        raise HTTPException(
            status_code=500, detail="Davranış analizi tamamlanamadı"
        ) from None

    session = await db.get(Session, session_id)
    if session is None:
        # The token was validly signed but its row is gone (database reset, or
        # a token older than the data). Recreate rather than 500.
        session = Session(id=session_id)
        db.add(session)

    recent_result = await db.execute(
        select(BehaviorData.risk_score)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.desc())
        .limit(SMOOTHING_WINDOW - 1)
    )
    # Skip any non-finite history: statistics.median() over a list containing
    # NaN returns a meaningless value rather than raising (NaN breaks the sort
    # ordering it relies on), which would corrupt this session's smoothing for
    # good. Rows like that can only pre-date the boundary validation above,
    # but they may already exist in a running database.
    recent_scores = [
        row[0] for row in recent_result.all() if row[0] is not None and math.isfinite(row[0])
    ]
    smoothed_score = round(statistics.median(recent_scores + [result["risk_score"]]), 1)
    smoothed_label = scorer.get_label(smoothed_score)

    session.risk_score = smoothed_score
    session.label = smoothed_label
    session.confidence = result["confidence"]
    session.shap_explanation = result["shap_explanation"]
    session.response_time_ms = result["response_time_ms"]
    session.signal_sufficiency = result["signal_sufficiency"]
    session.last_seen_at = datetime.utcnow()

    features = result["features"]
    behavior_row = BehaviorData(
        session_id=session_id,
        risk_score=result["risk_score"],
        **{name: features[name] for name in FEATURE_NAMES},
    )
    if STORE_RAW_TELEMETRY:
        behavior_row.mouse_trajectory = raw["mouse_trajectory"]
        behavior_row.click_timing = raw["click_timing"]
        behavior_row.scroll_rhythm = raw["scroll_events"]
        behavior_row.hesitation_intervals = raw["hesitation_intervals"]
        behavior_row.focus_changes = raw["focus_changes"]
        behavior_row.key_events = raw["key_events"]
    db.add(behavior_row)

    await db.commit()

    return AnalyzeResponse(
        session_id=session_id,
        risk_score=smoothed_score,
        label=smoothed_label,
        confidence=result["confidence"],
        shap_explanation=result["shap_explanation"],
        response_time_ms=result["response_time_ms"],
        signal_sufficiency=result["signal_sufficiency"],
    )


def _decide(session: Session | None) -> tuple[str, str, bool]:
    """Server-side authorization decision. Returns (decision, message, step_up).

    This is the control the product was missing. Blocking used to exist only
    in the browser -- `isBlocked = riskScore >= 80` disabled a button -- so
    the risk score was advisory and an attacker simply did not run, or did not
    obey, the frontend. The score is computed here, stored here, and read here
    when it matters; the browser's copy is presentation only.
    """
    if session is None:
        return "reddedildi", "Oturum bulunamadı.", False

    score = session.risk_score if session.risk_score is not None else 0.0
    signal = session.signal_sufficiency if session.signal_sufficiency is not None else 0.0

    if score >= BLOCK_THRESHOLD:
        return "reddedildi", "İşlem reddedildi — şüpheli davranış tespit edildi.", False
    if score >= STEP_UP_THRESHOLD:
        return "dogrulama_gerekli", "Bu işlem için ek doğrulama gerekiyor.", True
    if signal < MIN_SIGNAL_FOR_AUTO_APPROVE:
        # Not "suspicious" -- unobserved. Step up rather than wave it through.
        return (
            "dogrulama_gerekli",
            "Davranış verisi yetersiz — güvenlik için ek doğrulama gerekiyor.",
            True,
        )
    return "onaylandi", "Ödeme başarıyla alındı (demo).", False


class TransactionResponse(BaseModel):
    decision: str
    message: str
    risk_score: float
    label: str
    signal_sufficiency: float
    demo_code: str | None = None


@app.post("/api/transaction", response_model=TransactionResponse)
async def transaction(
    payload: TransactionRequest,
    request: Request,
    session_id: Annotated[str, Depends(require_session)],
    db: AsyncSession = Depends(get_db),
):
    _rate_limit(request, security.transaction_limiter)

    session = await db.get(Session, session_id)
    decision, message, step_up = _decide(session)

    demo_code = None
    if step_up:
        code = _issue_challenge(session_id)
        if DEMO_MODE:
            demo_code = code

    return TransactionResponse(
        decision=decision,
        message=message,
        risk_score=session.risk_score if session else 0.0,
        label=session.label if session else "Bilinmiyor",
        signal_sufficiency=session.signal_sufficiency if session else 0.0,
        demo_code=demo_code,
    )


@app.post("/api/transaction/verify", response_model=TransactionResponse)
async def transaction_verify(
    payload: VerifyRequest,
    request: Request,
    session_id: Annotated[str, Depends(require_session)],
    db: AsyncSession = Depends(get_db),
):
    _rate_limit(request, security.transaction_limiter)

    session = await db.get(Session, session_id)

    # Re-check the risk gate *after* verification too. Otherwise a session that
    # crossed the blocking threshold while the user was typing their code
    # would be approved on the strength of a challenge issued when it was
    # merely suspicious.
    decision, message, _ = _decide(session)
    if decision == "reddedildi":
        return TransactionResponse(
            decision=decision,
            message=message,
            risk_score=session.risk_score if session else 0.0,
            label=session.label if session else "Bilinmiyor",
            signal_sufficiency=session.signal_sufficiency if session else 0.0,
        )

    ok, verify_message = _verify_challenge(session_id, payload.code)
    if not ok:
        raise HTTPException(status_code=403, detail=verify_message)

    return TransactionResponse(
        decision="onaylandi",
        message="Doğrulama başarılı — ödeme alındı (demo).",
        risk_score=session.risk_score if session else 0.0,
        label=session.label if session else "Bilinmiyor",
        signal_sufficiency=session.signal_sufficiency if session else 0.0,
    )


@app.get("/api/score/{session_id}", dependencies=[Depends(require_operator)])
async def get_score(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Oturum bulunamadı")

    result = await db.execute(
        select(BehaviorData)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.asc())
    )
    history = result.scalars().all()

    return {
        "session_id": session.id,
        "risk_score": session.risk_score,
        "label": session.label,
        "confidence": session.confidence,
        "shap_explanation": session.shap_explanation,
        "response_time_ms": session.response_time_ms,
        "signal_sufficiency": session.signal_sufficiency,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
        "history": [
            {
                "timestamp": row.created_at,
                "risk_score": row.risk_score,
                **{name: getattr(row, name) for name in FEATURE_NAMES},
            }
            for row in history
        ],
    }


@app.get("/api/sessions", dependencies=[Depends(require_operator)])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Session).order_by(Session.last_seen_at.desc()))
    sessions = result.scalars().all()
    return [
        {
            "session_id": s.id,
            "risk_score": s.risk_score,
            "label": s.label,
            "confidence": s.confidence,
            "response_time_ms": s.response_time_ms,
            "signal_sufficiency": s.signal_sufficiency,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
        }
        for s in sessions
    ]


@app.get("/api/health")
async def health():
    model_loaded = True
    try:
        scorer.get_bundle()
    except FileNotFoundError:
        model_loaded = False

    return {
        "status": "sağlıklı" if model_loaded else "model yüklenmedi",
        "model_loaded": model_loaded,
        "timestamp": datetime.utcnow().isoformat(),
    }
