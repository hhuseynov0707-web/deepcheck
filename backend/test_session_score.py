"""Tests for how a session's official risk score evolves across flushes.

Regression cover for the 2026-08-19 adversarial finding: every bot profile was
correctly flagged at its peak (72-82) and then decayed below the intervention
thresholds before submitting the payment. Median smoothing is symmetric, but
risk is not -- being slow to flag protects a human from one odd window, while
being equally slow to clear lets a bot wait out its own score. See AUDIT.md.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_session_score.db")

import main


def test_one_odd_window_does_not_flag_a_human():
    """The original reason smoothing exists. Must still hold."""
    calm = [8.0, 11.0, 9.0, 10.0]
    score = main.blend_session_score(previous=10.0, recent=calm, current=88.0)
    assert score < 40.0, "a single anomalous reading must not flip a calm session to bot"


def test_a_session_that_peaked_high_does_not_clear_immediately():
    """A bot that trips the detector must not clear itself by carrying on.

    linear_mover peaked at 81.9 -- squarely in 'Bot Tespit Edildi' -- and was
    at 47.6 by the time it submitted, so it only ever saw the verification
    modal. Two quiet flushes should not undo that.
    """
    score = main.blend_session_score(previous=81.9, recent=[81.9, 12.0], current=12.0)
    assert score >= 60.0, (
        "a session that reached 81.9 should still be above the 60 threshold "
        "one flush later; otherwise a bot clears its own record by waiting"
    )


def test_risk_still_decays_so_a_misflagged_user_recovers():
    """Held risk must not be permanent -- guard against over-correcting."""
    score = 85.0
    for _ in range(30):  # 30 flushes at 2s = one minute of calm behaviour
        score = main.blend_session_score(previous=score, recent=[5.0] * 4, current=5.0)
    assert score < 40.0, (
        "after a minute of consistently calm behaviour a session must fall "
        "back to 'genuine user'; risk decays, it does not brand"
    )


def test_sustained_bot_behaviour_keeps_the_score_up():
    """Evidence should accumulate, not average away."""
    score = 0.0
    for _ in range(6):
        score = main.blend_session_score(previous=score, recent=[70.0] * 4, current=72.0)
    assert score >= 70.0, "sustained high readings must hold the session high"
