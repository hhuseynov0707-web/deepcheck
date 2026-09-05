import hashlib
import hmac
import json
import logging
import math
import os
import statistics
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import scorer
from database import get_db, init_db
from lstm_model import FEATURE_NAMES, SEQUENCE_LENGTH
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
SMOOTHING_WINDOW = 5

# How many *previous* flushes the LSTM sees as its time-series context. The
# model reads SEQUENCE_LENGTH steps, the last of which is the current flush.
# Before this, every step of the sequence was a copy of the current flush, so
# the "temporal" model carried 30% of the ensemble weight while being fed no
# time information at all.
LSTM_HISTORY_ROWS = SEQUENCE_LENGTH - 1

# One query serves both the median smoothing window and the LSTM context.
HISTORY_FETCH_ROWS = max(SMOOTHING_WINDOW - 1, LSTM_HISTORY_ROWS)

# The dashboard re-fetches both of these every 3s per open viewer, and neither
# table is ever pruned. Unbounded reads meant the payload grew for the whole
# life of the deployment -- fine on a laptop for ten minutes, not fine for a
# demo stand running all day in front of visitors.
SESSIONS_PAGE_LIMIT = 200
HISTORY_LIMIT = 200

# --- Replay protection for /api/analyze -------------------------------------
#
# Telemetry timestamps used to be checked only for being between 1970 and
# 2100. That let an attacker record one genuine human session once and replay
# its flushes, byte for byte, under a freshly minted token before every
# fraudulent checkout: the score came out human, the token was valid, and
# /api/decision said "allow". No ML evasion was needed at all.
#
# Three checks close the cheap versions of that attack:
#   1. The newest event in a flush must be within MAX_CLOCK_SKEW_MS of the
#      server clock. A recording is, by definition, old.
#   2. Time must move forward within a session: the newest event of each
#      flush must not be older than the previous flush's newest event.
#   3. A clock-independent fingerprint of the telemetry (timestamps rebased to
#      the flush's first event before hashing) must not already exist in the
#      database, in ANY session. Rewriting a recording's timestamps to "now"
#      defeats check 1; it does not change this hash.
#
# A recording that is perturbed as well as re-timed gets past the hash. That
# is the point where replay stops being a transport problem and becomes a
# model problem (is the perturbed behaviour still human-shaped?), which the
# LSTM history and the real-session evaluation are there to answer.
#
# 15 s is generous on purpose: phones and laptops drift by seconds, and the
# SDK's window can legitimately end up to ~10 s before the flush when the user
# is idle. Log the observed skew for a while before tightening.
MAX_CLOCK_SKEW_MS = 15_000

# --- Evidence and freshness for /api/decision -------------------------------
#
# One 2-second flush is not enough behaviour to trust: a script can produce
# a single plausible window far more easily than it can sustain one. Three
# flushes is six seconds of observed behaviour and also the point at which
# the 5-flush median smoothing starts to mean something.
MIN_FLUSHES_FOR_DECISION = 3

# A verdict is about the behaviour that produced it, and that behaviour must
# be current. Without this, a token lifted from a shared machine (or via XSS
# on the merchant page) could be cashed in an hour later on the strength of
# the real customer's earlier browsing.
DECISION_MAX_AGE_S = 30

# How long a successful step-up verification keeps upgrading "verify" to
# "allow". Long enough to finish the checkout, short enough not to become a
# standing bypass.
VERIFICATION_VALID_S = 300

# Fixed demo step-up code. The real integration replaces /api/demo/verify with
# the merchant's SMS / 3-D Secure provider; the demo shows the *pattern*
# (verification recorded on the server, never asserted by the browser) and
# prints this code in the modal so a jury can see it is a deliberate demo
# value, not an "any six digits" bypass.
DEMO_VERIFY_CODE = os.getenv("DEMO_VERIFY_CODE", "482913").strip()

logger = logging.getLogger("deepcheck")

# DEBUG=1 is the local `docker-compose up` / laptop-demo mode. It is the ONLY
# mode in which the process is allowed to fall back to a hard-coded signing
# secret, and it says so loudly in the log on every boot.
DEBUG = os.getenv("DEBUG", "0").strip() == "1"

# Deliberately fixed rather than randomly generated per process: entrypoint.sh
# runs 4 uvicorn workers, each with its own interpreter. A per-process random
# secret would mean a token minted by worker 1 fails verification on worker 2,
# i.e. random 401s under exactly the concurrency a demo produces.
_DEV_SECRET = "deepcheck-dev-secret-yalnizca-yerel-kullanim"
_DEV_DASHBOARD_KEY = "deepcheck-dev-dashboard-key"


def _load_secret(env_name: str, dev_fallback: str, purpose: str) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    if DEBUG:
        logger.warning(
            "UYARI: %s tanimlanmamis, DEBUG modunda sabit gelistirme degeri "
            "kullaniliyor (%s). Uretimde bu deger MUTLAKA ayarlanmalidir.",
            env_name,
            purpose,
        )
        return dev_fallback
    # Refusing to start is the point: a missing secret must never degrade into
    # "authentication is effectively off", which is what a silent default
    # would do. The process dies here rather than serving unsigned sessions.
    raise RuntimeError(
        f"{env_name} ortam degiskeni tanimli degil. Uretimde zorunludur "
        f"({purpose}). Yerel demo icin DEBUG=1 ayarlayin."
    )


SECRET = _load_secret("DEEPCHECK_SECRET", _DEV_SECRET, "oturum jetonu imzalama")
DASHBOARD_KEY = _load_secret("DASHBOARD_KEY", _DEV_DASHBOARD_KEY, "SOC panosu erisimi")


def sign_session(session_id: str) -> str:
    """HMAC-SHA256 over the session id. The client can hold this token but can
    never mint one for an id it was not given, which is what stops a bot from
    posting telemetry under another customer's session id."""
    return hmac.new(SECRET.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _require_session_token(session_id: str, token: str | None) -> None:
    # compare_digest, not ==: a plain comparison short-circuits on the first
    # differing byte and leaks the prefix length through timing.
    if not token or not hmac.compare_digest(sign_session(session_id), token):
        raise HTTPException(status_code=401, detail="Gecersiz oturum jetonu")


async def require_dashboard_key(
    x_dashboard_key: Annotated[str | None, Header()] = None,
) -> None:
    """Guards the SOC endpoints. Without this, `GET /api/sessions` let anyone
    on the network read every customer's live risk score and session id."""
    if not x_dashboard_key or not hmac.compare_digest(x_dashboard_key, DASHBOARD_KEY):
        raise HTTPException(status_code=401, detail="Yetkisiz erisim")


# The single place the 40/60/80 ladder turns into an enforcement decision.
# It used to live in Demo.jsx, i.e. inside the attacker's own browser.
ACTION_LADDER = (
    (40, "allow"),
    (60, "warn"),
    (80, "verify"),
    (101, "block"),
)

# Turkish labels for the four actions, so the client does not have to map them.
ACTION_MESSAGES = {
    "allow": "Islem onaylandi",
    "warn": "Davranisiniz normal disi gorunuyor, lutfen dikkatli devam edin",
    "verify": "Ek dogrulama gerekli",
    "block": "Islem Reddedildi - Supheli Davranis Tespit Edildi",
}

# Why a decision came out the way it did. `reason` lets the host page tell
# "not enough behaviour yet, try again in a moment" apart from "the score
# itself is high" without the ladder leaving the server.
REASON_MESSAGES = {
    "score": None,  # ACTION_MESSAGES[action] already says it
    "unknown_session": "Oturum bulunamadi - davranis analizi yapilamadi",
    "insufficient_evidence": "Karar icin yeterli davranis verisi yok, lutfen birkac saniye sonra tekrar deneyin",
    "stale": "Oturumun davranis verisi guncel degil, ek dogrulama gerekli",
    "verified": "Ek dogrulama basariyla tamamlandi, islem onaylandi",
}


def get_action(risk_score: float | None) -> str:
    """Fail closed. A missing or non-finite score is not evidence of
    innocence -- it is the absence of evidence, which is exactly what a client
    that never ran the SDK produces. Those sessions go to step-up
    verification, never straight through."""
    if risk_score is None or not math.isfinite(risk_score):
        return "verify"
    for threshold, action in ACTION_LADDER:
        if risk_score < threshold:
            return action
    return ACTION_LADDER[-1][1]


def _as_utc(value: datetime | None) -> datetime | None:
    """Postgres returns tz-aware datetimes for timestamptz columns; a stub or
    an old row may hand back a naive one. Treat naive as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


_STAMPED_CHANNELS = ("mouse_trajectory", "click_timing", "scroll_events", "key_events")


def _all_timestamps(raw: dict) -> list[float]:
    stamps = [e["t"] for key in _STAMPED_CHANNELS for e in raw[key]]
    stamps.extend(raw["focus_changes"])
    return stamps


def _newest_event_ms(raw: dict) -> int | None:
    """The most recent timestamp anywhere in a flush, or None if the flush
    carries no timestamped events at all (a hesitation-only window after the
    user has been idle long enough for everything else to roll out)."""
    stamps = _all_timestamps(raw)
    return int(max(stamps)) if stamps else None


def _payload_fingerprint(raw: dict) -> str:
    """Clock-independent SHA-256 of a flush's telemetry.

    Every timestamp is rebased to the flush's earliest event before hashing,
    so the same recording replayed with its clock shifted to "now" produces
    the same fingerprint. Coordinates are rounded to 0.1 px so float
    formatting differences between clients do not defeat the match.
    """
    stamps = _all_timestamps(raw)
    base = min(stamps) if stamps else 0
    # float() before round(): an integer 300 and a float 300.0 must hash the
    # same, and JSON renders them differently otherwise.
    canonical = {
        "m": [[round(float(p["x"]), 1), round(float(p["y"]), 1), int(p["t"] - base)] for p in raw["mouse_trajectory"]],
        "c": [[round(float(c["x"]), 1), round(float(c["y"]), 1), int(c["t"] - base)] for c in raw["click_timing"]],
        "s": [[round(float(e["scrollY"]), 1), int(e["t"] - base)] for e in raw["scroll_events"]],
        "k": [int(k["t"] - base) for k in raw["key_events"]],
        "f": [int(round(f - base)) for f in raw["focus_changes"]],
        "h": [int(round(h)) for h in raw["hesitation_intervals"]],
    }
    encoded = json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utcnow() -> datetime:
    """Timezone-aware UTC.

    The columns are DateTime(timezone=True) and their server_default is
    Postgres' now(), so writing a naive datetime.utcnow() mixed an
    offset-less application clock into a tz-aware column. datetime.utcnow()
    is also deprecated from Python 3.12 on.
    """
    return datetime.now(timezone.utc)


# One dummy scoring pass at boot. get_bundle() alone only unpickles the
# models; the first *real* call still pays sklearn's and torch's lazy
# per-operation warm-up (measured ~3x the steady-state latency). Doing it here
# means the first genuine flush of a demo is not the slow one.
_WARMUP_PAYLOAD = {
    "mouse_trajectory": [
        {"x": 100.0 + i, "y": 100.0 + i, "t": 1_700_000_000_000 + i * 90} for i in range(6)
    ],
    "click_timing": [{"x": 120.0, "y": 140.0, "t": 1_700_000_000_600}],
    "scroll_events": [{"scrollY": 50.0 * i, "t": 1_700_000_000_000 + i * 120} for i in range(4)],
    "hesitation_intervals": [420.0, 610.0],
    "focus_changes": [],
    "key_events": [{"t": 1_700_000_000_000 + i * 160} for i in range(5)],
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        scorer.get_bundle()
        await run_in_threadpool(scorer.compute_risk, _WARMUP_PAYLOAD)
        logger.info("Model yuklendi ve isitildi.")
    except FileNotFoundError as exc:
        logger.warning("UYARI: %s", exc)
    except Exception:
        # A failed warm-up must not stop the app from serving; the real
        # request path has its own error handling.
        logger.exception("Model isitma denemesi basarisiz oldu")
    yield


app = FastAPI(title="DeepCheck API", lifespan=lifespan)

# Comma-separated origin allowlist, e.g.
# CORS_ORIGINS="https://demo.example.com,https://soc.example.com".
# Defaults to "*" so the local docker-compose demo keeps working untouched.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # Wildcard origin and credentials are mutually exclusive under the CORS
    # spec: a browser refuses a credentialed response carrying
    # `Access-Control-Allow-Origin: *`. The previous combination happened to
    # be harmless only because nothing sends cookies yet -- it would have
    # broken silently the day auth was added. Credentials are enabled only
    # once a real origin allowlist is configured.
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    # No longer optional and no longer minted server-side when missing: an id
    # only becomes usable once POST /api/session has signed it, so there is
    # nothing sensible to do with a flush that carries no id.
    session_id: str = Field(min_length=1, max_length=128)
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


class SessionCreateResponse(BaseModel):
    session_id: str
    token: str


class DecisionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)


class DecisionResponse(BaseModel):
    action: str
    risk_score: float | None
    label: str
    message: str
    reason: str


class VerifyRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=16)


class VerifyResponse(BaseModel):
    verified: bool
    message: str


class ChargeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    amount: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)


class ChargeResponse(BaseModel):
    status: str  # "charged" | "declined"
    charge_id: str | None
    amount: float
    decision: DecisionResponse


@app.post("/api/session", response_model=SessionCreateResponse, status_code=201)
async def create_session():
    """Mints a session id and its signing token.

    The id is generated here, never accepted from the caller: if a client
    could name its own id, anyone who learned a victim's id could request a
    token for it and then post telemetry -- or ask for a decision -- under it.

    Deliberately writes NO database row. The row is created by the first
    /api/analyze flush instead, so a page that is opened and never used
    leaves nothing behind: React's StrictMode alone double-mounts the demo in
    development, and a row per mint would fill the SOC dashboard with empty
    ghost sessions. A minted-but-unused id is also exactly the case
    /api/decision must answer with "verify", which it does by finding no row.
    """
    session_id = str(uuid.uuid4())
    return SessionCreateResponse(session_id=session_id, token=sign_session(session_id))


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    session_id = payload.session_id
    _require_session_token(session_id, x_deepcheck_token)

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

    # Read this session's recent flushes BEFORE scoring: the LSTM needs them
    # as its input sequence, the median smoothing below needs their scores,
    # and the replay checks need the previous flush's newest timestamp.
    # Newest first; consumers re-order as they need.
    recent_result = await db.execute(
        select(BehaviorData)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.desc())
        .limit(HISTORY_FETCH_ROWS)
    )
    recent_rows = list(recent_result.scalars().all())

    # Replay protection. See the MAX_CLOCK_SKEW_MS comment for the attack.
    newest_event_at = _newest_event_ms(raw)
    if newest_event_at is not None:
        skew_ms = int(time.time() * 1000) - newest_event_at
        if abs(skew_ms) > MAX_CLOCK_SKEW_MS:
            logger.warning("replay/skew rejected for session %s: skew=%dms", session_id, skew_ms)
            raise HTTPException(
                status_code=422,
                detail="Telemetri zaman damgasi sunucu saatiyle uyumsuz (tekrar oynatma suphesi)",
            )
        previous_newest = getattr(recent_rows[0], "newest_event_at", None) if recent_rows else None
        if previous_newest is not None and newest_event_at < previous_newest:
            logger.warning("replay/backwards-time rejected for session %s", session_id)
            raise HTTPException(
                status_code=422,
                detail="Telemetri zamani geriye gidiyor (tekrar oynatma suphesi)",
            )

    payload_hash = _payload_fingerprint(raw)
    duplicate = await db.execute(
        select(BehaviorData.id).where(BehaviorData.payload_hash == payload_hash).limit(1)
    )
    if duplicate.scalars().first() is not None:
        logger.warning("replay/duplicate rejected for session %s: hash=%s", session_id, payload_hash[:12])
        raise HTTPException(
            status_code=422,
            detail="Bu davranis penceresi daha once gonderilmis (tekrar oynatma suphesi)",
        )

    # Oldest -> newest, so the sequence handed to the LSTM runs forward in
    # time and the current flush lands on the last timestep.
    feature_history = [
        [getattr(row, name) for name in FEATURE_NAMES]
        for row in reversed(recent_rows[:LSTM_HISTORY_ROWS])
    ]

    try:
        # compute_risk is ~50ms of pure CPU (sklearn + SHAP + torch). Called
        # directly in this async handler it blocks the single event-loop
        # thread, stalling every other in-flight request including
        # /api/health -- measured event-loop stalls up to 1.5s at 20
        # concurrent flushes, capping a worker at ~13 req/s. Running it in the
        # threadpool keeps the loop free to accept and finish other work.
        result = await run_in_threadpool(scorer.compute_risk, raw, feature_history)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        # Never surface a traceback to an unauthenticated caller: it is both an
        # error-log flood and a fingerprinting oracle. Log server-side instead.
        logger.exception("compute_risk failed for session %s", session_id)
        raise HTTPException(
            status_code=500, detail="Davranış analizi tamamlanamadı"
        ) from None

    # Atomic get-or-create. The previous read-then-add pattern raised an
    # unhandled IntegrityError (-> HTTP 500) whenever two flushes for the same
    # brand-new session arrived concurrently: both SELECTs returned None, both
    # INSERTs ran, the second violated the primary key. That is not
    # hypothetical -- the SDK fires every 2s and the first request pays model
    # warm-up, so overlap at session start is the common case, not the rare one.
    await db.execute(
        pg_insert(Session).values(id=session_id).on_conflict_do_nothing(index_elements=["id"])
    )
    session = await db.get(Session, session_id)

    # Skip any non-finite history: statistics.median() over a list containing
    # NaN returns a meaningless value rather than raising (NaN breaks the sort
    # ordering it relies on), which would corrupt this session's smoothing for
    # good. Rows like that can only pre-date the boundary validation above,
    # but they may already exist in a running database.
    recent_scores = [
        row.risk_score
        for row in recent_rows[: SMOOTHING_WINDOW - 1]
        if row.risk_score is not None and math.isfinite(row.risk_score)
    ]
    smoothed_score = round(statistics.median(recent_scores + [result["risk_score"]]), 1)
    smoothed_label = scorer.get_label(smoothed_score)

    session.risk_score = smoothed_score
    session.label = smoothed_label
    session.confidence = result["confidence"]
    session.shap_explanation = result["shap_explanation"]
    session.response_time_ms = result["response_time_ms"]
    session.last_seen_at = utcnow()

    features = result["features"]
    behavior_row = BehaviorData(
        session_id=session_id,
        mouse_trajectory=raw["mouse_trajectory"],
        click_timing=raw["click_timing"],
        scroll_rhythm=raw["scroll_events"],
        hesitation_intervals=raw["hesitation_intervals"],
        focus_changes=raw["focus_changes"],
        key_events=raw["key_events"],
        scroll_hizi_varyansi=features["scroll_hizi_varyansi"],
        tereddut_skoru=features["tereddut_skoru"],
        etkilesim_entropisi=features["etkilesim_entropisi"],
        ivme_degisimi=features["ivme_degisimi"],
        tiklama_yogunlugu=features["tiklama_yogunlugu"],
        odak_degisimi=features["odak_degisimi"],
        risk_score=result["risk_score"],
        payload_hash=payload_hash,
        newest_event_at=newest_event_at,
    )
    db.add(behavior_row)

    await db.commit()

    return AnalyzeResponse(
        session_id=session_id,
        risk_score=smoothed_score,
        label=smoothed_label,
        confidence=result["confidence"],
        shap_explanation=result["shap_explanation"],
        response_time_ms=result["response_time_ms"],
    )


def _verify_response(reason: str, risk_score: float | None = None, label: str = "Degerlendirilemedi") -> DecisionResponse:
    return DecisionResponse(
        action="verify",
        risk_score=risk_score,
        label=label,
        message=REASON_MESSAGES[reason] or ACTION_MESSAGES["verify"],
        reason=reason,
    )


async def _decide(db: AsyncSession, session_id: str) -> DecisionResponse:
    """The enforcement logic, shared by /api/decision and /api/demo/charge.

    Every failure mode resolves to step-up verification rather than to
    "allow": an unknown session, too little observed behaviour, behaviour
    that is not current, or a broken score. The 40/60/80 ladder is applied
    here and nowhere else.
    """
    session = await db.get(Session, session_id)
    if session is None:
        return _verify_response("unknown_session")

    # A row can exist with the default risk_score of 0.0 -- which would read
    # as "Gercek Kullanici" for a client that never sent usable telemetry.
    # And one flush is not enough: require MIN_FLUSHES_FOR_DECISION analyzed
    # windows before any score is trusted.
    flush_count = await db.scalar(
        select(func.count()).select_from(BehaviorData).where(BehaviorData.session_id == session_id)
    )
    if (flush_count or 0) < MIN_FLUSHES_FOR_DECISION:
        return _verify_response("insufficient_evidence")

    now = utcnow()
    last_seen = _as_utc(session.last_seen_at)
    if last_seen is None or now - last_seen > timedelta(seconds=DECISION_MAX_AGE_S):
        return _verify_response("stale", session.risk_score, session.label or "Degerlendirilemedi")

    risk_score = session.risk_score
    action = get_action(risk_score)
    label = session.label or scorer.get_label(risk_score)

    # A completed step-up upgrades "verify" to "allow" while it is fresh. It
    # never touches "block": verification is for uncertainty, not for
    # overriding a confident bot verdict.
    verified_at = _as_utc(getattr(session, "verified_at", None))
    if (
        action == "verify"
        and verified_at is not None
        and now - verified_at <= timedelta(seconds=VERIFICATION_VALID_S)
    ):
        return DecisionResponse(
            action="allow",
            risk_score=risk_score,
            label=label,
            message=REASON_MESSAGES["verified"],
            reason="verified",
        )

    return DecisionResponse(
        action=action, risk_score=risk_score, label=label, message=ACTION_MESSAGES[action], reason="score"
    )


@app.post("/api/decision", response_model=DecisionResponse)
async def decision(
    payload: DecisionRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """The enforcement point. A merchant backend calls this at checkout and
    obeys `action`. The previous design put this decision in the browser,
    where anyone could edit it away."""
    _require_session_token(payload.session_id, x_deepcheck_token)
    return await _decide(db, payload.session_id)


@app.post("/api/demo/verify", response_model=VerifyResponse)
async def demo_verify(
    payload: VerifyRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Records a successful step-up on the SERVER.

    Stands in for the merchant's SMS / 3-D Secure provider. What matters for
    the pattern is where the result lives: the browser used to decide for
    itself that verification had succeeded and then run the payment. Now the
    only thing it can do is submit a code; whether that unlocks anything is
    decided here and read back by /api/demo/charge.
    """
    _require_session_token(payload.session_id, x_deepcheck_token)

    if not hmac.compare_digest(payload.code.strip(), DEMO_VERIFY_CODE):
        raise HTTPException(status_code=400, detail="Dogrulama kodu hatali")

    session = await db.get(Session, payload.session_id)
    if session is None:
        # Nothing to attach the verification to: this client never sent a
        # single flush. Verifying an unobserved session would be exactly the
        # bypass the whole design exists to prevent.
        raise HTTPException(status_code=404, detail="Oturum bulunamadi")

    session.verified_at = utcnow()
    await db.commit()
    return VerifyResponse(verified=True, message=REASON_MESSAGES["verified"])


@app.post("/api/demo/charge", response_model=ChargeResponse)
async def demo_charge(
    payload: ChargeRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """The merchant side of the pattern, in miniature.

    A real merchant backend would call /api/decision and then its payment
    provider. Here both live in one endpoint so the demo proves the property
    a jury will test for: no sequence of browser actions produces a
    "charged" response for a session the server would not allow. Deleting
    every check in Demo.jsx changes nothing, because Demo.jsx has no checks.
    """
    _require_session_token(payload.session_id, x_deepcheck_token)
    verdict = await _decide(db, payload.session_id)

    if verdict.action in ("allow", "warn"):
        return ChargeResponse(
            status="charged", charge_id=str(uuid.uuid4()), amount=payload.amount, decision=verdict
        )
    return ChargeResponse(status="declined", charge_id=None, amount=payload.amount, decision=verdict)


@app.get("/api/score/{session_id}", dependencies=[Depends(require_dashboard_key)])
async def get_score(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Oturum bulunamadı")

    # Newest HISTORY_LIMIT rows, then flipped back to chronological order for
    # the chart. Selecting ascending without a limit re-sent the session's
    # entire history on every 3s dashboard poll.
    result = await db.execute(
        select(BehaviorData)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history = list(reversed(result.scalars().all()))

    return {
        "session_id": session.id,
        "risk_score": session.risk_score,
        "label": session.label,
        "confidence": session.confidence,
        "shap_explanation": session.shap_explanation,
        "response_time_ms": session.response_time_ms,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
        "history": [
            {
                "timestamp": row.created_at,
                "risk_score": row.risk_score,
                "scroll_hizi_varyansi": row.scroll_hizi_varyansi,
                "tereddut_skoru": row.tereddut_skoru,
                "etkilesim_entropisi": row.etkilesim_entropisi,
                "ivme_degisimi": row.ivme_degisimi,
                "tiklama_yogunlugu": row.tiklama_yogunlugu,
                "odak_degisimi": row.odak_degisimi,
            }
            for row in history
        ],
    }


@app.get("/api/sessions", dependencies=[Depends(require_dashboard_key)])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Session).order_by(Session.last_seen_at.desc()).limit(SESSIONS_PAGE_LIMIT)
    )
    sessions = result.scalars().all()
    return [
        {
            "session_id": s.id,
            "risk_score": s.risk_score,
            "label": s.label,
            "confidence": s.confidence,
            "response_time_ms": s.response_time_ms,
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
        "timestamp": utcnow().isoformat(),
    }
