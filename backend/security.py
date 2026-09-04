"""Session-token issuance, rate limiting and operator authentication.

Why this module exists
----------------------
The API previously accepted a client-chosen `session_id` on every
/api/analyze call, with no authentication anywhere. Three things followed
directly from that, all reproduced against a running instance:

  * Anyone could write behaviour under *any* session id, including one
    belonging to somebody else.
  * A session already flagged "Bot Tespit Edildi" (86.7) could be walked
    back down to "Supheli" (50.6) simply by posting human-shaped payloads
    under the same id -- the median smoothing in main.py assumes a single
    writer per session, which nothing enforced.
  * Every session's verdict and feature history was world-readable via
    /api/sessions and /api/score/{id}.

So: session ids become server-issued and HMAC-signed (a client can only
write to a session the server gave it), the dashboard read endpoints move
behind an operator key, and both are rate limited.

Scope note: this is authentication of the *session channel*, not of the
user. It stops forgery and cross-session tampering. It cannot make
client-submitted telemetry trustworthy -- an attacker still controls what
their own browser reports about itself, which is why the risk score must
stay one input to a server-side decision rather than the decision itself.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time

logger = logging.getLogger("deepcheck.security")

SESSION_TTL_SECONDS = 12 * 60 * 60  # a session token outlives a long checkout

_DEV_SECRET_ENV = "DEEPCHECK_SECRET"
_OPERATOR_KEY_ENV = "DEEPCHECK_OPERATOR_KEY"


def _load_or_generate(env_name: str, purpose: str) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    # Generating a per-boot secret keeps `docker-compose up` working with no
    # configuration, which matters for the demo. It is explicitly *not* safe
    # for a real deployment: with more than one worker process each gets a
    # different secret (so tokens fail to validate across workers), and every
    # restart invalidates every issued token. Warn loudly rather than
    # pretending this is a configured system.
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "%s is not set -- generated an ephemeral %s for this process. "
        "Set %s in the environment for any real deployment "
        "(multi-worker and restart-stable).",
        env_name,
        purpose,
        env_name,
    )
    return generated


SESSION_SECRET = _load_or_generate(_DEV_SECRET_ENV, "session signing key")
OPERATOR_API_KEY = _load_or_generate(_OPERATOR_KEY_ENV, "operator API key")


# --------------------------------------------------------------------------
# Session tokens
# --------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(session_id: str, issued_at: int) -> str:
    message = f"{session_id}.{issued_at}".encode()
    return _b64(hmac.new(SESSION_SECRET.encode(), message, hashlib.sha256).digest())


def issue_session() -> tuple[str, str, int]:
    """Creates a fresh session id and its signed token.

    The id is generated here, never accepted from the client -- that is the
    whole point. token_urlsafe(18) is unguessable, so a third party cannot
    target an existing session even by brute force.
    """
    session_id = secrets.token_urlsafe(18)
    issued_at = int(time.time())
    token = f"{session_id}.{issued_at}.{_sign(session_id, issued_at)}"
    return session_id, token, SESSION_TTL_SECONDS


def verify_session_token(token: str | None) -> str | None:
    """Returns the session id a token authorises, or None if it is invalid.

    Rejects: malformed tokens, bad signatures, and expired ones. Signature
    comparison is constant-time so the check cannot be turned into an oracle
    by timing it.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    session_id, issued_at_raw, signature = parts

    try:
        issued_at = int(issued_at_raw)
    except ValueError:
        return None

    expected = _sign(session_id, issued_at)
    if not hmac.compare_digest(expected, signature):
        return None

    age = int(time.time()) - issued_at
    # Reject future-dated tokens too: a negative age means a forged or
    # clock-skewed timestamp, and allowing it would extend the TTL freely.
    if age < -60 or age > SESSION_TTL_SECONDS:
        return None

    return session_id


def verify_operator_key(provided: str | None) -> bool:
    """Constant-time check of the SOC dashboard's operator key."""
    if not provided:
        return False
    return hmac.compare_digest(provided, OPERATOR_API_KEY)


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

class RateLimiter:
    """Token-bucket limiter keyed by caller (usually client IP).

    Bounded in two ways on purpose: buckets are pruned once the table grows
    past _MAX_TRACKED so the limiter itself cannot be turned into a memory
    exhaustion vector by rotating source addresses, and the lock keeps it
    correct across uvicorn's threadpool.

    Deployment caveat: state is per process. With the default 4 uvicorn
    workers the effective limit is 4x what is configured here. That is a
    deliberate trade for the MVP (no Redis dependency); a real deployment
    wants a shared store or a limiter at the edge/ingress.
    """

    _MAX_TRACKED = 20_000

    def __init__(self, rate_per_second: float, burst: int):
        self.rate = rate_per_second
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > self._MAX_TRACKED:
                cutoff = now - 60
                self._buckets = {
                    k: v for k, v in self._buckets.items() if v[1] > cutoff
                }

            tokens, last_seen = self._buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last_seen) * self.rate)

            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False

            self._buckets[key] = (tokens - 1.0, now)
            return True


# /api/analyze is the hot path: the SDK flushes every 2s, so ~0.5 req/s per
# real user. 5/s sustained with a burst of 20 leaves generous headroom for a
# few tabs while making the 60-writes-in-1.5s flood measured during the audit
# impossible.
analyze_limiter = RateLimiter(rate_per_second=5.0, burst=20)

# Session creation is once per page load; anything faster is enumeration or
# an attempt to fill the sessions table.
session_limiter = RateLimiter(rate_per_second=0.5, burst=10)

# Transactions are deliberately tight -- this is the money path.
transaction_limiter = RateLimiter(rate_per_second=1.0, burst=5)


# --------------------------------------------------------------------------
# Signed decision artifacts
# --------------------------------------------------------------------------
# A transaction decision that travels as plain JSON is only trustworthy while
# it stays inside this process. The moment a real deployment puts a separate
# payment backend behind DeepCheck, that backend needs to verify the verdict
# it was handed rather than believing whatever the caller forwarded --
# otherwise the enforcement boundary moves back to the client, which is the
# vulnerability this whole hardening effort removed.
#
# So the decision is emitted as a signed artifact: the deciding fields plus an
# HMAC over their canonical serialization. Any holder of the shared key can
# verify that DeepCheck issued exactly this verdict, for exactly this session,
# at this time. The signature covers the decision, session, risk score,
# amount, policy version and timestamp together, so no field can be swapped
# for another decision's.

DECISION_TTL_SECONDS = 300


def _canonical(fields: dict) -> bytes:
    """Stable serialization for signing: sorted keys, no incidental whitespace.

    Signing a dict's repr or an unsorted dump would make the signature depend
    on key ordering, so a semantically identical artifact could fail to verify.
    """
    import json

    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def sign_decision(fields: dict) -> dict:
    """Returns `fields` plus decision_id, issued_at and an HMAC signature."""
    artifact = dict(fields)
    artifact["decision_id"] = secrets.token_urlsafe(12)
    artifact["issued_at"] = int(time.time())
    artifact["signature"] = _b64(
        hmac.new(SESSION_SECRET.encode(), _canonical(artifact), hashlib.sha256).digest()
    )
    return artifact


def verify_decision(artifact: dict) -> tuple[bool, str]:
    """Checks a decision artifact's signature and freshness.

    Returns (valid, reason). Rejects a tampered field, a forged signature, and
    an artifact old enough to be a replay of a previous verdict.
    """
    if not isinstance(artifact, dict) or "signature" not in artifact:
        return False, "İmza bulunamadı."

    provided = artifact["signature"]
    unsigned = {k: v for k, v in artifact.items() if k != "signature"}
    expected = _b64(
        hmac.new(SESSION_SECRET.encode(), _canonical(unsigned), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, provided):
        return False, "İmza geçersiz."

    issued_at = unsigned.get("issued_at")
    if not isinstance(issued_at, int):
        return False, "Zaman damgası geçersiz."
    age = int(time.time()) - issued_at
    if age < -60 or age > DECISION_TTL_SECONDS:
        return False, "Karar belgesinin süresi doldu."

    return True, "Karar doğrulandı."
