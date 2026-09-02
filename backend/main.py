import logging
import math
import statistics
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import scorer
from lstm_model import SEQUENCE_LENGTH
from database import get_db, init_db
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

# Median smoothing is symmetric; risk is not. Being slow to flag protects a
# human from one odd window -- that is why the median is here and it still
# applies. But applied in both directions it also lets a bot clear its own
# record simply by continuing: in the 2026-08-19 adversarial audit every bot
# profile was correctly flagged at its peak (72-82) and had decayed below the
# intervention thresholds by the time it submitted the payment. One reached
# 81.9, squarely "Bot Tespit Edildi", and paid at 47.6.
#
# So the session's score is floored by its own decayed peak: evidence
# accumulates rather than averaging away, and a session that has demonstrated
# bot behaviour has to genuinely behave for a while to be cleared, not merely
# carry on. 0.93 per flush at the SDK's 2s cadence halves a score in roughly
# 19s -- long enough to cover a payment attempt, short enough that a
# misflagged human recovers inside a minute.
RISK_DECAY_PER_FLUSH = 0.93

# How many prior flushes the LSTM's window looks back over. One less than the
# model's SEQUENCE_LENGTH, because the flush being scored fills the last slot.
LSTM_HISTORY_WINDOW = SEQUENCE_LENGTH - 1


def blend_session_score(previous, recent, current):
    """The session's official risk score for this flush.

    previous -- the session's last official score, or None for a new session
    recent   -- the last few raw per-flush scores
    current  -- this flush's raw score
    """
    smoothed = statistics.median(list(recent) + [current])
    decayed_peak = (previous or 0.0) * RISK_DECAY_PER_FLUSH
    return round(max(smoothed, decayed_peak), 1)

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
    allow_origins=["*"],
    allow_credentials=True,
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
    session_id: str | None = Field(default=None, max_length=128)
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


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(payload: AnalyzeRequest, db: AsyncSession = Depends(get_db)):
    session_id = payload.session_id or str(uuid.uuid4())

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

    # The LSTM scores a window of the session's recent flushes. BehaviorData
    # already persists each flush's six features, so the real window is a
    # query away -- previously the current flush was tiled across every
    # timestep, leaving the model no temporal signal to read. Oldest first,
    # so the sequence runs forward in time.
    history_result = await db.execute(
        select(*(getattr(BehaviorData, name) for name in scorer.FEATURE_NAMES))
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.desc())
        .limit(LSTM_HISTORY_WINDOW)
    )
    feature_history = [list(row) for row in reversed(history_result.all())]

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

    session = await db.get(Session, session_id)
    if session is None:
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
    smoothed_score = blend_session_score(
        previous=session.risk_score,
        recent=recent_scores,
        current=result["risk_score"],
    )
    smoothed_label = scorer.get_label(smoothed_score)

    session.risk_score = smoothed_score
    session.label = smoothed_label
    session.confidence = result["confidence"]
    session.shap_explanation = result["shap_explanation"]
    session.response_time_ms = result["response_time_ms"]
    session.last_seen_at = datetime.utcnow()

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


@app.get("/api/score/{session_id}")
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


@app.get("/api/sessions")
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
