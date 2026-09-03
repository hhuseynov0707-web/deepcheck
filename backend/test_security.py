"""Tests for the session-token, rate-limiting and operator-auth controls.

Each test here corresponds to an attack that was reproduced against a running
instance during the security audit. They exist so a refactor cannot quietly
reopen one:

  * A client-chosen session_id let anyone write behaviour under any session,
    including walking a flagged session (86.7, "Bot Tespit Edildi") back down
    to 50.6 by flooding it with human-shaped payloads.
  * /api/sessions and /api/score/{id} exposed every session's verdict and
    feature history unauthenticated.
  * 60 unauthenticated writes in 1.5s created 60 sessions with no throttle.
  * A JSON NaN token turned a correctly-rejected request into a 500 with a
    full traceback, on demand.

Run:
    pytest test_security.py
"""

import time

import pytest

import security


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def test_issued_token_verifies_to_its_own_session():
    session_id, token, ttl = security.issue_session()
    assert security.verify_session_token(token) == session_id
    assert ttl > 0


def test_session_ids_are_unpredictable_and_unique():
    ids = {security.issue_session()[0] for _ in range(200)}
    assert len(ids) == 200
    # Long enough that a third party cannot enumerate their way onto someone
    # else's session, which is what made poisoning possible when ids were
    # client-supplied.
    assert all(len(i) >= 20 for i in ids)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "garbage",
        "a.b.c",
        "victim-user-0001.1700000000.forgedsignature",
        "too.many.parts.here",
    ],
)
def test_forged_or_malformed_tokens_are_rejected(token):
    assert security.verify_session_token(token) is None


def test_tampering_with_the_session_id_invalidates_the_signature():
    """The exact poisoning attempt: keep a valid signature, swap the target."""
    _, token, _ = security.issue_session()
    _, issued_at, signature = token.split(".")
    tampered = f"victim-user-0001.{issued_at}.{signature}"
    assert security.verify_session_token(tampered) is None


def test_expired_and_future_dated_tokens_are_rejected():
    session_id = "abc123"
    stale_at = int(time.time()) - security.SESSION_TTL_SECONDS - 60
    stale = f"{session_id}.{stale_at}.{security._sign(session_id, stale_at)}"
    assert security.verify_session_token(stale) is None

    # A future timestamp would otherwise extend the TTL arbitrarily.
    future_at = int(time.time()) + 3600
    future = f"{session_id}.{future_at}.{security._sign(session_id, future_at)}"
    assert security.verify_session_token(future) is None


def test_tokens_signed_with_another_key_are_rejected():
    session_id, token, _ = security.issue_session()
    original = security.SESSION_SECRET
    try:
        security.SESSION_SECRET = "a-different-secret"
        assert security.verify_session_token(token) is None
    finally:
        security.SESSION_SECRET = original
    assert security.verify_session_token(token) == session_id


# ---------------------------------------------------------------------------
# Operator key
# ---------------------------------------------------------------------------


def test_operator_key_accepts_only_the_configured_value():
    assert security.verify_operator_key(security.OPERATOR_API_KEY) is True
    for wrong in (None, "", "wrong", security.OPERATOR_API_KEY + "x"):
        assert security.verify_operator_key(wrong) is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_limiter_allows_burst_then_throttles():
    limiter = security.RateLimiter(rate_per_second=1.0, burst=5)
    allowed = [limiter.allow("1.2.3.4") for _ in range(20)]
    assert allowed[:5] == [True] * 5
    assert allowed.count(True) == 5, "burst budget was not enforced"
    assert False in allowed


def test_limiter_is_per_caller():
    """One noisy client must not throttle everyone else."""
    limiter = security.RateLimiter(rate_per_second=1.0, burst=3)
    for _ in range(10):
        limiter.allow("attacker")
    assert limiter.allow("innocent-user") is True


def test_limiter_refills_over_time():
    limiter = security.RateLimiter(rate_per_second=100.0, burst=2)
    assert limiter.allow("x") is True
    assert limiter.allow("x") is True
    assert limiter.allow("x") is False
    time.sleep(0.05)  # 100/s refills the bucket well within this
    assert limiter.allow("x") is True


def test_limiter_bucket_table_is_bounded():
    """The limiter must not become a memory-exhaustion vector itself.

    Rotating the source address on every request would otherwise grow the
    bucket table without limit.
    """
    limiter = security.RateLimiter(rate_per_second=1.0, burst=1)
    for i in range(limiter._MAX_TRACKED + 500):
        limiter.allow(f"ip-{i}")
    assert len(limiter._buckets) <= limiter._MAX_TRACKED + 500
