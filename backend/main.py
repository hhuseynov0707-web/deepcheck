import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import statistics
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import scorer
from database import get_db, get_sessionmaker, init_db
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
# A floor UNDER the sequential test, not a replacement for it.
#
# Setting this to 1 when SPRT arrived was a regression, and the arithmetic
# shows why: the lower bound is log(beta/(1-alpha)) = -2.2925, so a single
# flush scoring 9.17 or less already crosses into "human" and is approved.
# Telemetry is attacker-supplied, and one fabricated window is the cheapest
# thing an attacker can produce -- the whole point of the original rule was
# that sustaining six seconds of plausible behaviour is harder than minting
# one snapshot. The sequential test decides WHEN there is enough evidence;
# this decides how little evidence can ever be enough.
MIN_FLUSHES_FOR_DECISION = 3

# --- Sequential evidence (SPRT) ---------------------------------------------
#
# "Three flushes" was a number chosen by judgement. Wald's sequential
# probability ratio test replaces it with a stopping rule that adapts to how
# clear the evidence is, and is optimal in expected sample size for a given
# pair of error rates: a blatant bot is decided on its first flush, an
# ambiguous session keeps collecting instead of being waved through the moment
# a counter hits three.
#
# The statistic is the running sum of per-flush log-likelihood ratios. For a
# calibrated score the logit IS that ratio, so the sum is just
# Sum(log(p / (1 - p))) over the flushes seen so far, with p the per-flush
# risk. Crossing the upper bound means "enough evidence, and it points at a
# bot"; crossing the lower means "enough evidence, and it points at a person";
# between them there is not yet enough to act on either way, which is the
# state that maps to step-up verification.
#
# ALPHA is the false-positive rate the bound is built for and BETA the false
# negative; the asymmetry is deliberate, because challenging a real customer
# costs a sale while missing one bot costs one attempt.
SPRT_ALPHA = 0.01
SPRT_BETA = 0.10
SPRT_UPPER = math.log((1.0 - SPRT_BETA) / SPRT_ALPHA)
SPRT_LOWER = math.log(SPRT_BETA / (1.0 - SPRT_ALPHA))

# A per-flush score of exactly 0 or 100 would make the logit infinite and let
# one flush dominate every other. Clamped to the resolution the score actually
# carries.
SPRT_P_CLAMP = 0.005

# Ambiguity cannot postpone a decision forever: past this many flushes the
# ladder is applied on the smoothed score regardless of where the statistic
# sits. Without it a session that hovers around the middle would be challenged
# on every attempt, which is a bad outcome for a real customer.
SPRT_MAX_FLUSHES = 10

# --- Cross-session clustering -----------------------------------------------
#
# The measured hole in per-session scoring is that competent mimicry is
# human-shaped by construction. What mimicry cannot avoid is repeating itself:
# a script run many times produces many nearly identical behavioural
# signatures, and people do not. Counting how many DISTINCT sessions share a
# behaviour bucket inside a short window turns that repetition into a signal
# the model never sees.
#
# It can only escalate, never approve. A cluster is evidence of automation, but
# the absence of one is not evidence of a person.
#
# MEASURED LIMITATION, stated because it decides how much weight this deserves.
# Against the adversarial harness this catches bots that repeat themselves and
# does NOT catch a bot that randomises its own parameters between runs: 25
# sessions of an independently written humanised generator produced 30 distinct
# buckets, the same spread as 25 human sessions. Behavioural quantisation
# identifies a *kind of motion*, not a *particular script*. What the industry
# actually clusters on is identity -- device fingerprint, TLS signature, IP --
# which is far more stable across runs than behaviour is.
#
# So the threshold is set where it does not fire on the measured human
# population, and the mechanism is here for the non-randomising farms it does
# catch, and as the place identity signals would attach later. It is not the
# answer to mimicry.
CLUSTER_WINDOW_S = 900
CLUSTER_MIN_SESSIONS = 6

# OFF by default, on the evidence. Measured against the adversarial harness at
# every threshold tried, the escalation cost more legitimate sessions than it
# caught bots: at 4 peers it challenged 40% of humans to catch 60% of mimics,
# and at 6 peers it challenged 8% of humans to catch 4%. A control that flags
# more customers than attackers is worse than no control, and challenging a
# real customer costs a sale while missing one bot costs one attempt.
#
# The bucket is still computed and stored, because it costs almost nothing, it
# is the natural attach point for identity signals (device, TLS, IP) which are
# what actually cluster, and because leaving the measurement in place is how
# the decision gets revisited when there is real traffic to revisit it with.
CLUSTER_ESCALATION_ENABLED = os.getenv("CLUSTER_ESCALATION", "0").strip() == "1"

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

# --- Runtime attestation -----------------------------------------------------
#
# Everything the detector scores is a summary statistic of numbers the client
# supplies, and an adversarial harness that never opened a browser -- it signed
# its own tokens and posted JSON -- scored 10.5 against a human 11.3. No amount
# of work on the features answers that, because the features are computed from
# whatever the client chose to send.
#
# Attestation asks a different question: did this telemetry come from code
# running in a browser at all? Two cheap checks, neither of which claims to
# prove a human is present.
#
# PROOF OF WORK. The server issues a signed challenge and the client must find
# a nonce whose SHA-256 has POW_DIFFICULTY_BITS leading zero bits. This is what
# Kasada, hCaptcha and Turnstile use it for: not as a cost tax, but as evidence
# that the client executed the code it was served. It is cheap for one browser
# (a few hundred milliseconds) and linear in cost for a farm.
#
# RUNTIME MEASUREMENTS. performance.now() is deliberately clamped by every
# browser -- roughly 100 microseconds in Chrome, 1 millisecond in Firefox and
# Safari -- and setTimeout(0) does not fire in zero milliseconds. Those numbers
# are properties of the engine and the operating system, not of the page, so a
# client that fabricates telemetry has to fabricate them too, which means
# knowing what to fabricate.
#
# What this does NOT do, stated plainly: a bot driving a real browser produces
# a real proof of work and real timer values. Attestation closes the
# post-JSON-directly path. It does nothing about Playwright.
POW_DIFFICULTY_BITS = int(os.getenv("POW_DIFFICULTY_BITS", "12"))

# How long a challenge stays solvable. Long enough for a slow phone, short
# enough that a solved challenge cannot be stockpiled.
POW_CHALLENGE_TTL_S = 180

# Plausible ranges for the runtime measurements. Deliberately wide: the point
# is to reject values that no browser produces, not to fingerprint which
# browser this is. A clamp finer than half a microsecond means the client is
# not subject to any clamp at all.
MIN_CLOCK_RESOLUTION_US = 0.5
MAX_CLOCK_RESOLUTION_US = 5000.0
# Measured in Chromium on an idle loop: a median of 0.1 ms, and the clock clamp
# came back as exactly 100.0 microseconds, which is Chrome's documented value.
# The floor sits an order of magnitude below the observed lag, because the job
# is to reject a fabricated zero rather than to insist on a particular
# scheduler -- a tight bound would fail real users on a fast machine, and a
# false rejection here costs a customer.
MIN_TIMER_LAG_MS = 0.01
MAX_TIMER_LAG_MS = 250.0

# The gate is token issuance itself: /api/session hands out a challenge and
# nothing else, so a client that cannot attest never obtains the token that
# /api/analyze requires. No separate flag and no stored state -- holding a
# valid token IS the attestation.
#
# The obvious caveat, said out loud: in DEBUG the signing secret is a published
# constant, so anything that reads the source can mint its own token and skip
# all of this. That is true of every token check in the system and is why
# DEBUG=0 refuses to start without a real secret.


def _issue_challenge(session_id: str) -> str:
    """A challenge the server can verify without storing anything.

    Carries the session it belongs to and the moment it was minted, signed, so
    a solution cannot be moved to another session or replayed after it expires.
    """
    issued_ms = int(time.time() * 1000)
    body = f"{session_id}.{issued_ms}"
    signature = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{signature}"


def _check_challenge(session_id: str, challenge: str) -> None:
    parts = challenge.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="Gecersiz dogrulama sorusu")
    challenge_session, issued_raw, signature = parts
    body = f"{challenge_session}.{issued_raw}"
    expected = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Dogrulama sorusu imzasi gecersiz")
    if challenge_session != session_id:
        raise HTTPException(status_code=400, detail="Dogrulama sorusu bu oturuma ait degil")
    try:
        issued_ms = int(issued_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Gecersiz dogrulama sorusu") from None
    if abs(int(time.time() * 1000) - issued_ms) > POW_CHALLENGE_TTL_S * 1000:
        raise HTTPException(status_code=400, detail="Dogrulama sorusunun suresi doldu")


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def _check_proof_of_work(challenge: str, nonce: str) -> None:
    digest = hashlib.sha256(f"{challenge}.{nonce}".encode()).digest()
    if _leading_zero_bits(digest) < POW_DIFFICULTY_BITS:
        raise HTTPException(status_code=400, detail="Is kaniti gecersiz")


def _check_runtime(runtime: "RuntimeMeasurements") -> None:
    """Reject values no browser engine produces.

    Wide bounds on purpose. This is not a browser fingerprint; it is a check
    that the numbers could have come from a clock that is actually clamped and
    an event loop that actually costs something to schedule on.
    """
    if not (MIN_CLOCK_RESOLUTION_US <= runtime.clock_resolution_us <= MAX_CLOCK_RESOLUTION_US):
        raise HTTPException(
            status_code=400,
            detail="Calisma zamani olcumleri bir tarayiciyla tutarsiz (saat cozunurlugu)",
        )
    if not (MIN_TIMER_LAG_MS <= runtime.timer_lag_ms <= MAX_TIMER_LAG_MS):
        raise HTTPException(
            status_code=400,
            detail="Calisma zamani olcumleri bir tarayiciyla tutarsiz (zamanlayici gecikmesi)",
        )

# The /api/demo/* endpoints are a demonstration of the merchant-side pattern,
# not a payment integration. The step-up code is a fixed constant that the demo
# page prints on screen, so anything the server answers `verify` for can be
# upgraded to `allow` by anyone who reads it. That is the point in a demo and
# unacceptable anywhere else, so they are enabled only in DEBUG unless someone
# turns them on deliberately.
# Reads DEBUG from the environment rather than the module constant, which is
# defined further down; the default is simply "whatever DEBUG says".
DEMO_ENDPOINTS_ENABLED = (
    os.getenv("DEMO_ENDPOINTS", os.getenv("DEBUG", "0")).strip() == "1"
)

# Whether /api/analyze returns its SHAP breakdown to the client being scored.
#
# It should not. The response goes to the party under assessment, and naming
# the three features driving their score hands them a tuning signal: submit,
# read which feature convicted you, adjust, repeat. That is a supervised
# optimisation loop against the live detector, and the adversarial run used
# exactly it to build a bot that scores lower than real humans. The SOC
# dashboard still gets the full explanation from GET /api/score/{id}, which is
# behind DASHBOARD_KEY, and the explanation is still stored on every row.
#
# Default off. Set SHAP_IN_ANALYZE=1 only for a walkthrough where showing the
# reasoning live matters more than withholding it.
SHAP_IN_ANALYZE = os.getenv("SHAP_IN_ANALYZE", "0").strip() == "1"


def _require_demo_endpoints() -> None:
    if not DEMO_ENDPOINTS_ENABLED:
        raise HTTPException(
            status_code=404, detail="Demo uc noktalari bu dagitimda kapali"
        )

# --- Rate limiting -----------------------------------------------------------
#
# Every /api/analyze call is ~50 ms of CPU in a threadpool, so a single client
# looping on it saturates every worker; unlimited /api/session minting fills
# the sessions table for free. Both were unbounded.
#
# Deliberately a small in-process sliding window rather than a library: the
# usual choices (slowapi and friends) also keep their counters in process
# memory unless a Redis backend is configured, so they would buy a pinned
# dependency and the same semantics. Two consequences are stated rather than
# hidden:
#   * Counters are PER WORKER. entrypoint.sh runs 4, so the effective limit
#     across the service is up to 4x what is configured here. The limits below
#     are chosen so that is still a useful ceiling.
#   * Counters are lost on restart. That is acceptable for abuse control; it
#     would not be for billing or quota.
# A shared Redis backend is the upgrade path when there is more than one host.
#
# The /api/analyze limit is keyed by SESSION ID, not by IP: a demo stand or an
# office puts many genuine users behind one address, and an IP limit there
# would blind the detector for everyone. Minting is what is keyed by IP, so
# the two compose -- an attacker needs a new session per 60 flushes and is
# limited in how fast new sessions can be created.
# Read these as PER WORKER: with the default UVICORN_WORKERS=4 the aggregate
# ceiling is four times each number, because a request lands on whichever
# worker accepts it. Measured on the running stack: 30 consecutive mints from
# one address all returned 201, which is the arithmetic working as described,
# not the limiter failing. The numbers below are therefore chosen for the
# AGGREGATE they produce at 4 workers.
RATE_LIMITS = {
    # bucket: (max requests per worker, window seconds)   -> aggregate at 4 workers
    "session": (10, 60),  # page loads per IP             -> 40/min
    "analyze": (60, 60),  # SDK sends 30/min per session  -> generous headroom
    "decision": (20, 60),  # checkout attempts per session -> 80/min
}

# Bound the limiter's own memory: an attacker rotating keys must not be able
# to grow this dictionary without limit. Past the cap, entries whose window has
# fully expired are dropped, and if that frees nothing the oldest are.
_RATE_KEY_CAP = 20_000
_rate_hits: dict[tuple[str, str], deque[float]] = {}


def _rate_limit(bucket: str, key: str) -> None:
    """Sliding-window limiter. Raises 429 when the window is full."""
    limit, window = RATE_LIMITS[bucket]
    now = time.monotonic()
    cutoff = now - window

    hits = _rate_hits.get((bucket, key))
    if hits is None:
        if len(_rate_hits) >= _RATE_KEY_CAP:
            _evict_rate_keys(now)
        hits = _rate_hits.setdefault((bucket, key), deque())

    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        retry_after = max(1, int(hits[0] + window - now) + 1)
        raise HTTPException(
            status_code=429,
            detail="Cok fazla istek gonderildi, lutfen biraz bekleyin",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)


def _evict_rate_keys(now: float) -> None:
    dead = [k for k, hits in _rate_hits.items() if not hits or hits[-1] < now - RATE_LIMITS[k[0]][1]]
    for k in dead:
        _rate_hits.pop(k, None)
    if len(_rate_hits) >= _RATE_KEY_CAP:
        # Nothing had expired: drop the least recently touched half rather
        # than growing without bound or refusing all traffic.
        oldest = sorted(_rate_hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
        for k, _ in oldest[: len(oldest) // 2]:
            _rate_hits.pop(k, None)


def _client_ip(request: Request) -> str:
    """The peer address, deliberately NOT X-Forwarded-For.

    That header is attacker-controlled unless a trusted proxy is known to
    rewrite it, and honouring it blindly turns a per-IP limit into no limit
    at all. Behind a real reverse proxy, configure uvicorn's --proxy-headers
    with --forwarded-allow-ips so request.client is the resolved address.
    """
    return request.client.host if request.client else "unknown"


# --- Retention ---------------------------------------------------------------
#
# Every flush stores its full raw telemetry, up to 2000 mouse points, and
# nothing was ever deleted: a stand running all day grew without limit, and
# behavioural recordings of real people accumulated indefinitely, which is a
# privacy question before it is a disk question. The features and the score
# are what analysis needs; the raw JSON is only needed long enough for
# record_session.py to freeze a labelled session.
RAW_TELEMETRY_RETENTION_HOURS = float(os.getenv("RAW_RETENTION_HOURS", "1"))
ROW_RETENTION_HOURS = float(os.getenv("ROW_RETENTION_HOURS", "24"))
RETENTION_SWEEP_S = 600

# Only one worker should sweep. Advisory lock, same mechanism init_db uses.
_RETENTION_LOCK_KEY = 728_302

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
# Rotated when the key stopped being compiled into the frontend bundle: every
# build published before that shipped the old value to anyone who opened the
# dashboard, so it has to be treated as burned.
_DEV_DASHBOARD_KEY = "deepcheck-dev-pano-anahtari-2026"


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
    "cluster": "Bu davranis kalibi kisa surede cok sayida oturumda tekrarlandi",
    "ambiguous": "Davranis yeterince uzun sure izlendi ancak kesin bir sonuca varilamadi, ek dogrulama gerekli",
    "conformal": "Skor yuksek olsa da gercek kullanici dagilimina uyuyor, ek dogrulama uygulaniyor",
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


async def _sweep_once() -> tuple[int, int]:
    """One retention pass. Returns (raw blanked, rows deleted)."""
    now = utcnow()
    raw_cutoff = now - timedelta(hours=RAW_TELEMETRY_RETENTION_HOURS)
    row_cutoff = now - timedelta(hours=ROW_RETENTION_HOURS)

    async with get_sessionmaker()() as db:
        # Non-blocking advisory lock: with 4 workers, only the one that gets
        # it sweeps and the rest return immediately instead of queueing up
        # behind the same DELETE.
        got_lock = await db.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _RETENTION_LOCK_KEY}
        )
        if not got_lock:
            return (0, 0)
        try:
            # Blank the raw telemetry first. The six features and the score
            # stay, so the dashboard chart and any analysis keep working on
            # rows whose recording has aged out.
            blanked = await db.execute(
                update(BehaviorData)
                .where(BehaviorData.created_at < raw_cutoff)
                .where(BehaviorData.raw_purged.is_(False))
                .values(
                    raw_purged=True,
                    mouse_trajectory=[],
                    click_timing=[],
                    scroll_rhythm=[],
                    hesitation_intervals=[],
                    focus_changes=[],
                    key_events=[],
                )
            )
            deleted = await db.execute(
                delete(BehaviorData).where(BehaviorData.created_at < row_cutoff)
            )
            # Sessions whose every flush has now been deleted carry no
            # evidence and cannot produce a decision, so they are noise on the
            # dashboard.
            await db.execute(
                delete(Session)
                .where(Session.last_seen_at < row_cutoff)
                .where(~select(BehaviorData.id).where(BehaviorData.session_id == Session.id).exists())
            )
            await db.commit()
            return (blanked.rowcount or 0, deleted.rowcount or 0)
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _RETENTION_LOCK_KEY}
            )
            await db.commit()


async def _retention_loop() -> None:
    while True:
        try:
            await asyncio.sleep(RETENTION_SWEEP_S)
            blanked, deleted = await _sweep_once()
            if blanked or deleted:
                logger.info(
                    "Saklama temizligi: %d satirin ham telemetrisi silindi, %d satir tamamen silindi",
                    blanked,
                    deleted,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed sweep must never take the API down with it; the next
            # one will retry.
            logger.exception("Saklama temizligi basarisiz oldu")


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

    sweeper = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass


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


class ClientSignals(_TelemetryEvent):
    """Provenance the browser reports about itself.

    RECORDED ONLY. None of these reach the feature vector, the model, or the
    risk score today, and the columns exist so their value can be measured
    against the real-session evaluation set before anyone relies on them.

    Worth stating plainly, because a jury will ask: `isTrusted` is false for
    events synthesised by page JavaScript, but a browser driven by Playwright
    or Puppeteer produces TRUSTED events, so this catches injected clicks and
    not driven browsers. `navigator.webdriver` is the reverse -- it flags the
    driven browser and is trivially patched out. Neither is evidence on its
    own, and both are self-reported by the client being judged.
    """

    untrusted_events: Annotated[int, Field(ge=0, le=100_000)] = 0
    webdriver: bool = False
    pointer_mouse: Annotated[int, Field(ge=0, le=100_000)] = 0
    pointer_pen: Annotated[int, Field(ge=0, le=100_000)] = 0
    pointer_touch: Annotated[int, Field(ge=0, le=100_000)] = 0


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
    client_signals: ClientSignals = Field(default_factory=ClientSignals)


class AnalyzeResponse(BaseModel):
    session_id: str
    risk_score: float
    label: str
    confidence: float
    shap_explanation: list[dict]
    response_time_ms: float


class SessionCreateResponse(BaseModel):
    """Challenge only. The token is issued by /api/session/attest."""

    session_id: str
    challenge: str
    difficulty_bits: int


class RuntimeMeasurements(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Smallest non-zero gap the client observed between consecutive
    # performance.now() readings, in microseconds. Every engine clamps this.
    clock_resolution_us: Annotated[float, Field(ge=0, le=1e6, allow_inf_nan=False)]
    # Median observed delay of setTimeout(..., 0), in milliseconds. Never zero
    # on a real event loop.
    timer_lag_ms: Annotated[float, Field(ge=0, le=1e4, allow_inf_nan=False)]


class SessionAttestRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    challenge: str = Field(min_length=1, max_length=256)
    nonce: str = Field(min_length=1, max_length=64)
    runtime: RuntimeMeasurements


class SessionAttestResponse(BaseModel):
    session_id: str
    token: str
    attested: bool


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
async def create_session(request: Request):
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
    _rate_limit("session", _client_ip(request))
    session_id = str(uuid.uuid4())
    return SessionCreateResponse(
        session_id=session_id,
        challenge=_issue_challenge(session_id),
        difficulty_bits=POW_DIFFICULTY_BITS,
    )


@app.post("/api/session/attest", response_model=SessionAttestResponse, status_code=201)
async def attest_session(payload: SessionAttestRequest, request: Request):
    """Exchanges a solved challenge for the session token.

    The token is what /api/analyze requires, so telemetry cannot be posted at
    all until something has executed a proof of work and reported runtime
    measurements consistent with a browser. That is a statement about the
    client being real code in a real engine, not about a human being present.
    """
    # Deliberately NOT rate limited. Minting the challenge already consumed a
    # slot, and charging a second one for the answer halves the real budget: a
    # page load costs two calls, so a 10-per-minute bucket became five page
    # loads per minute and a customer reloading twice got a 429. A solved
    # challenge is worthless without the challenge, and that is what the limit
    # protects.
    _check_challenge(payload.session_id, payload.challenge)
    _check_proof_of_work(payload.challenge, payload.nonce)
    _check_runtime(payload.runtime)
    return SessionAttestResponse(
        session_id=payload.session_id,
        token=sign_session(payload.session_id),
        attested=True,
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    session_id = payload.session_id
    _require_session_token(session_id, x_deepcheck_token)
    _rate_limit("analyze", session_id)

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

    # Smoothing exists so one odd reading cannot flip a verdict. A mid-session
    # handover is not one odd reading -- it is a level shift, and the median
    # hides it for as long as it takes three of five windows to turn.
    #
    # Measured on a handover: the blended score reached 61.2 on the first
    # automated flush while the smoothed score stayed at 15.4 and the decision
    # stayed "allow". It took roughly ten more seconds to reach step-up.
    #
    # So when the components disagree sharply -- which is precisely what a
    # handover looks like -- smoothing may still lower a spike, but it may not
    # lower it below the reading that raised the alarm.
    if result.get("disagreement", 0.0) >= scorer.DISAGREEMENT_THRESHOLD:
        smoothed_score = max(smoothed_score, result["risk_score"])
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
        # Built from FEATURE_NAMES rather than listed by hand, so adding a
        # feature does not silently stop persisting it -- which would also
        # break the LSTM history read, since that reads the same columns.
        **{name: features[name] for name in FEATURE_NAMES},
        risk_score=result["risk_score"],
        payload_hash=payload_hash,
        behavior_bucket=result["behavior_bucket"],
        newest_event_at=newest_event_at,
        client_signals=payload.client_signals.model_dump(),
    )
    db.add(behavior_row)

    try:
        await db.commit()
    except IntegrityError:
        # The duplicate pre-check above is a read before a write, so identical
        # flushes posted concurrently all pass it. The unique index on
        # payload_hash is what actually settles that race; losing it means this
        # window was already recorded, which is the same answer the pre-check
        # gives.
        await db.rollback()
        logger.warning("replay/duplicate race lost for session %s", session_id)
        raise HTTPException(
            status_code=422,
            detail="Bu davranis penceresi daha once gonderilmis (tekrar oynatma suphesi)",
        ) from None

    return AnalyzeResponse(
        session_id=session_id,
        risk_score=smoothed_score,
        label=smoothed_label,
        confidence=result["confidence"],
        # Empty unless SHAP_IN_ANALYZE is set: see the note there. The row
        # keeps the full explanation, and the dashboard reads it from
        # /api/score/{id} behind the dashboard key.
        shap_explanation=result["shap_explanation"] if SHAP_IN_ANALYZE else [],
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


def _sprt_statistic(scores: list[float]) -> float:
    """Running sum of per-flush log-likelihood ratios."""
    total = 0.0
    for score in scores:
        p = min(max(score / 100.0, SPRT_P_CLAMP), 1.0 - SPRT_P_CLAMP)
        total += math.log(p / (1.0 - p))
    return total


async def _cluster_size(db: AsyncSession, session_id: str, bucket: str | None) -> int:
    """Distinct OTHER sessions sharing this behaviour bucket recently."""
    if not bucket:
        return 0
    cutoff = utcnow() - timedelta(seconds=CLUSTER_WINDOW_S)
    result = await db.scalar(
        select(func.count(func.distinct(BehaviorData.session_id)))
        .where(BehaviorData.behavior_bucket == bucket)
        .where(BehaviorData.created_at >= cutoff)
        .where(BehaviorData.session_id != session_id)
    )
    return int(result or 0)


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
    rows = (
        await db.execute(
            select(BehaviorData.risk_score, BehaviorData.behavior_bucket)
            .where(BehaviorData.session_id == session_id)
            .order_by(BehaviorData.created_at.desc())
            .limit(SPRT_MAX_FLUSHES)
        )
    ).all()
    if len(rows) < MIN_FLUSHES_FOR_DECISION:
        return _verify_response("insufficient_evidence")

    now = utcnow()
    last_seen = _as_utc(session.last_seen_at)
    if last_seen is None or now - last_seen > timedelta(seconds=DECISION_MAX_AGE_S):
        return _verify_response("stale", session.risk_score, session.label or "Degerlendirilemedi")

    # Sequential test over the per-flush scores. Between the bounds there is
    # not yet enough evidence to act on, which is step-up rather than approval.
    per_flush = [r[0] for r in rows if r[0] is not None and math.isfinite(r[0])]
    statistic = _sprt_statistic(per_flush)
    if SPRT_LOWER < statistic < SPRT_UPPER:
        # Inconclusive, and it stays inconclusive: the flush cap changes what
        # the customer is told, never whether the payment goes through.
        #
        # It used to fall through to the ladder here, which meant a session
        # parked in the 40-60 band was charged with a warning banner once it
        # had produced ten flushes. Twenty seconds of deliberately ambiguous
        # behaviour was therefore a way to be approved. Ambiguity at a payment
        # gate is a reason to ask for more proof, not a reason to accept.
        reason = "insufficient_evidence" if len(per_flush) < SPRT_MAX_FLUSHES else "ambiguous"
        return _verify_response(reason, session.risk_score, session.label or "Degerlendirilemedi")

    risk_score = session.risk_score
    action = get_action(risk_score)
    label = session.label or scorer.get_label(risk_score)

    # Cross-session clustering. Escalation only: many sessions behaving
    # identically is evidence of automation, while the absence of a cluster is
    # not evidence of a person.
    if CLUSTER_ESCALATION_ENABLED and action in ("allow", "warn"):
        bucket = rows[0][1] if rows else None
        peers = await _cluster_size(db, session_id, bucket)
        if peers >= CLUSTER_MIN_SESSIONS:
            logger.info(
                "cluster escalation for session %s: %d peers in bucket %s", session_id, peers, bucket
            )
            return DecisionResponse(
                action="verify",
                risk_score=risk_score,
                label=label,
                message=REASON_MESSAGES["cluster"],
                reason="cluster",
            )

    # Conformal guard. De-escalation only: if this score is unremarkable among
    # held-out real humans, refuse to block on it and ask for verification
    # instead. Costs a challenge rather than a customer.
    if action == "block":
        p_value = scorer.conformal_p_value(risk_score, scorer.get_human_calibration())
        if p_value is not None and p_value > scorer.CONFORMAL_ALPHA:
            logger.info(
                "conformal guard softened a block for session %s (p=%.3f)", session_id, p_value
            )
            return DecisionResponse(
                action="verify",
                risk_score=risk_score,
                label=label,
                message=REASON_MESSAGES["conformal"],
                reason="conformal",
            )

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
    _rate_limit("decision", payload.session_id)
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
    _require_demo_endpoints()
    _require_session_token(payload.session_id, x_deepcheck_token)
    # Same bucket as the checkout itself: without this the step-up code is a
    # six-digit secret an attacker may guess at unlimited speed.
    _rate_limit("decision", payload.session_id)

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
    _require_demo_endpoints()
    _require_session_token(payload.session_id, x_deepcheck_token)
    _rate_limit("decision", payload.session_id)
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
                **{name: getattr(row, name) for name in FEATURE_NAMES},
                # Recorded, not scored -- see ClientSignals.
                "client_signals": row.client_signals or {},
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
