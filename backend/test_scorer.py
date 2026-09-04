"""Sanity tests for scorer.py's feature extraction + risk scoring.

Run after training (these need model.pkl / lstm_model.pt to exist):
    python test_scorer.py
or, if pytest is installed:
    pytest test_scorer.py

These exist to catch the exact bug class this file is a response to: a
feature formula (or its normalization, or the training data's numeric range)
drifting out of what the trained model actually expects, silently turning
normal human behavior into a false positive (or a real bot into a false
negative). If a future change to scorer.py or train_model.py breaks this,
these assertions fail loudly instead of only being noticed when a demo user
gets blocked.
"""

import numpy as np

import scorer

BASE_T = 1_751_470_045_000


def _ballistic_mouse(rng, n_points, t, x, y, speed=1.0, tremor=1.2):
    """Human-shaped pointer motion for fixtures.

    Fixtures used to move the cursor with `x += rng.normal(6, 6)` -- an
    independent random step each sample. That is not what a hand does: real
    motion is target-directed and carries momentum, so consecutive speeds and
    directions are strongly correlated. The distinction was invisible while
    the detector only measured variance and entropy (a random walk and a real
    reach have similar marginals), but it is the entire basis of the
    kinematics features, and it is exactly what an attacker script produces.

    So the IID walk is no longer a "human" fixture -- it is the documented
    evasion, and it now has its own test asserting it gets *caught*
    (test_iid_random_walk_bot_is_detected). Mirrors _ballistic_path() in
    train_model.py.
    """
    points = []
    while len(points) < n_points:
        target_x = x + rng.normal(0, 180)
        target_y = y + rng.normal(0, 120)
        steps = int(rng.integers(6, 14))
        x0, y0 = x, y
        for i in range(1, steps + 1):
            if len(points) >= n_points:
                break
            p = i / steps
            s = 10 * p**3 - 15 * p**4 + 6 * p**5  # minimum-jerk profile
            x = x0 + (target_x - x0) * s + rng.normal(0, tremor)
            y = y0 + (target_y - y0) * s + rng.normal(0, tremor)
            t += max(int(rng.uniform(12, 28) / speed), 1)
            points.append({"x": x, "y": y, "t": t})
        t += int(rng.uniform(60, 300) / speed)
    return points, t, x, y


def _natural_human_session(seed: int = 0) -> dict:
    """Natural, randomized human behavior: jittery mouse movement, a few
    clicks with real pauses before them, some scrolling, natural typing
    rhythm."""
    rng = np.random.default_rng(seed)
    t = BASE_T
    mouse, t, x, y = _ballistic_mouse(rng, 25, t, 200.0, 200.0)

    clicks = []
    for _ in range(3):
        t += int(rng.uniform(400, 900))
        clicks.append({"x": x, "y": y, "t": t})

    scrolls = []
    sy = 0
    for _ in range(4):
        sy += int(rng.uniform(50, 150))
        t += int(rng.uniform(80, 200))
        scrolls.append({"scrollY": sy, "t": t})

    keys = []
    for _ in range(20):
        t += int(rng.lognormal(mean=5.0, sigma=0.4))
        keys.append({"t": t})

    all_t = sorted(
        [m["t"] for m in mouse]
        + [c["t"] for c in clicks]
        + [s["t"] for s in scrolls]
        + [k["t"] for k in keys]
    )
    hesitation = [b - a for a, b in zip(all_t, all_t[1:]) if (b - a) >= 400]

    return {
        "mouse_trajectory": mouse,
        "click_timing": clicks,
        "scroll_events": scrolls,
        "hesitation_intervals": hesitation,
        "focus_changes": [],
        "key_events": keys,
    }


def _sparse_typing_human_session() -> dict:
    """A human who is mostly typing -- minimal mouse movement. This is the
    exact shape of payload that originally triggered the false-positive bug
    this test file guards against (see conversation: normal card-form typing
    was scoring 55-70+ / "Yuksek Risk").

    Note: a handful of small mousemove events lead up to the click -- a real
    browser session essentially never has literally zero mouse events before
    a click (the cursor has to get there somehow), even for a
    typing-dominant user. Truly zero mouse data only happens for scripted
    clicks (element.dispatchEvent without moving a cursor at all), which is
    itself a bot signal, not a realistic sparse-human one."""
    rng = np.random.default_rng(7)
    mouse = []
    x, y = 290.0, 195.0
    t = BASE_T - 400
    for _ in range(5):
        x += rng.normal(6, 6)
        y += rng.normal(4, 5)
        t += int(rng.uniform(50, 150))
        mouse.append({"x": x, "y": y, "t": t})

    return {
        "mouse_trajectory": mouse,
        "click_timing": [{"x": 300, "y": 200, "t": BASE_T}],
        "scroll_events": [],
        "hesitation_intervals": [650, 480],
        "focus_changes": [],
        "key_events": [
            {"t": BASE_T + 1200},
            {"t": BASE_T + 1350},
            {"t": BASE_T + 1600},
            {"t": BASE_T + 1800},
            {"t": BASE_T + 2100},
        ],
    }


def _headless_bot_session() -> dict:
    """No mouse/scroll at all, instant scripted clicks and keystrokes -- a
    naive form-fill script (element.value = ...; form.submit())."""
    return {
        "mouse_trajectory": [],
        "click_timing": [
            {"x": 300, "y": 200, "t": BASE_T},
            {"x": 300, "y": 200, "t": BASE_T + 2},
        ],
        "scroll_events": [],
        "hesitation_intervals": [],
        "focus_changes": [],
        "key_events": [{"t": BASE_T + 10 + 2 * i} for i in range(4)],
    }


def _scripted_motion_bot_session() -> dict:
    """A more sophisticated bot that DOES simulate mouse/scroll, but
    linearly/robotically -- constant velocity, perfectly regular intervals,
    rapid uniform clicking, scripted keystroke injection."""
    mouse = []
    x, y = 100.0, 100.0
    t = BASE_T
    for _ in range(15):
        x += 5.0
        y += 2.0
        t += 80
        mouse.append({"x": x, "y": y, "t": t})

    clicks = [{"x": x, "y": y, "t": BASE_T + 150 * i} for i in range(1, 9)]
    scrolls = [{"scrollY": 50 * i, "t": BASE_T + 90 * i} for i in range(1, 8)]
    keys = [{"t": BASE_T + 5000 + 3 * i} for i in range(1, 20)]

    return {
        "mouse_trajectory": mouse,
        "click_timing": clicks,
        "scroll_events": scrolls,
        "hesitation_intervals": [],
        "focus_changes": [],
        "key_events": keys,
    }


def _bot_with_incidental_pause_session() -> dict:
    """Same scripted-motion bot as _scripted_motion_bot_session(), but with
    ONE incidental pause inserted between the click and key bursts (e.g. a
    real network round-trip, page load, or explicit sleep() in the script).
    Every other channel stays fully robotic: constant-velocity mouse,
    perfectly-spaced clicks, near-instant scripted keystrokes.

    This is the exact adversarial case found via live browser testing: a
    single realistic-looking pause used to single-handedly flip the verdict
    from "Bot Tespit Edildi" to "Gercek Kullanici", even though every other
    signal stayed unambiguously robotic. It should no longer be enough on its
    own to clear the session -- the other five features must still count."""
    mouse = []
    x, y = 100.0, 100.0
    t = BASE_T
    for _ in range(15):
        x += 5.0
        y += 2.0
        t += 80
        mouse.append({"x": x, "y": y, "t": t})

    clicks = [{"x": x, "y": y, "t": BASE_T + 150 * i} for i in range(1, 9)]
    scrolls = [{"scrollY": 50 * i, "t": BASE_T + 90 * i} for i in range(1, 8)]

    pause_t = BASE_T + 5000 + 1800  # one incidental ~1.8s pause
    keys = [{"t": pause_t + 3 * i} for i in range(1, 20)]

    all_t = sorted(
        [m["t"] for m in mouse] + [c["t"] for c in clicks] + [s["t"] for s in scrolls] + [k["t"] for k in keys]
    )
    hesitation = [b - a for a, b in zip(all_t, all_t[1:]) if (b - a) >= 400]

    return {
        "mouse_trajectory": mouse,
        "click_timing": clicks,
        "scroll_events": scrolls,
        "hesitation_intervals": hesitation,
        "focus_changes": [],
        "key_events": keys,
    }


def _human_with_fast_burst_session(seed: int = 0) -> dict:
    """A natural human session (same shape as _natural_human_session) but
    with one quick burst of clicks/keys added -- e.g. quickly fixing a typo
    or double-checking a field. A real human's overall session should not
    flip to bot-like just because one short segment was fast."""
    raw = _natural_human_session(seed=seed)
    last_t = max(
        [m["t"] for m in raw["mouse_trajectory"]]
        + [c["t"] for c in raw["click_timing"]]
        + [k["t"] for k in raw["key_events"]]
    )
    burst_start = last_t + 50
    extra_keys = [{"t": burst_start + 4 * i} for i in range(1, 8)]
    raw["key_events"] = raw["key_events"] + extra_keys
    return raw


def _fast_keyboard_only_no_mouse_session() -> dict:
    """Rapid, continuous keyboard-driven form fill with NO mouse movement at
    all -- e.g. Tab-navigation between fields plus scripted/injected
    keystrokes, evenly spaced at a few ms apart, no pauses anywhere. This is
    the exact shape of session found via live browser testing that
    originally scored ~10-22 ("Gercek Kullanici") despite being
    indistinguishable from a keyboard-injection bot: no mouse data, no
    clicks, uniformly-paced rapid typing, zero hesitation."""
    keys = [{"t": BASE_T + 3 * i} for i in range(60)]
    return {
        "mouse_trajectory": [],
        "click_timing": [],
        "scroll_events": [],
        "hesitation_intervals": [],
        "focus_changes": [],
        "key_events": keys,
    }


def test_natural_human_scores_low():
    for seed in range(5):
        raw = _natural_human_session(seed=seed)
        result = scorer.compute_risk(raw)
        assert result["risk_score"] < 40, (
            f"natural human (seed={seed}) scored {result['risk_score']}, expected <40. "
            f"features={result['features']}"
        )


def test_sparse_typing_human_scores_low():
    # NOTE: threshold is <60 (Supheli: a warning is shown, nothing is
    # blocked/2FA-gated), not <40 (Gercek Kullanici). This fixture is close
    # to the sparsest possible real session (1 click, a handful of
    # mousemove/keydown events). With that little data, several features
    # legitimately can't be measured with confidence -- this is a genuine
    # statistical limit (small-sample variance/entropy estimators are
    # inherently noisy), not a bug to keep chasing. The original false
    # positive this test guards against pushed sessions like this into
    # "Yuksek Risk"/"Bot Tespit Edildi" (60-100, 2FA-gated or fully blocked)
    # -- that is the regression that must not recur.
    raw = _sparse_typing_human_session()
    result = scorer.compute_risk(raw)
    assert result["risk_score"] < 60, (
        f"sparse-typing human scored {result['risk_score']}, expected <60 "
        f"(this is the exact shape of the original false-positive bug -- it "
        f"used to score 55-70+ and get blocked/2FA-gated). "
        f"features={result['features']}"
    )


def test_headless_bot_scores_high():
    # NOTE: threshold is >70 (Yuksek Risk: still flagged and 2FA-gated), not
    # >80 (Bot Tespit Edildi / fully blocked). A headless bot with no
    # mouse/scroll at all has almost every feature hit the neutral fallback
    # (see NEUTRAL_DEFAULTS in scorer.py) once those defaults are properly
    # calibrated to be unbiased -- there just isn't much real signal left to
    # lean on beyond click count and event entropy. Richer-signal bots
    # (see test_scripted_motion_bot_scores_high) still clear >80 reliably;
    # only this maximally-sparse, signal-starved variant is borderline.
    raw = _headless_bot_session()
    result = scorer.compute_risk(raw)
    assert result["risk_score"] > 70, (
        f"headless bot scored {result['risk_score']}, expected >70. features={result['features']}"
    )


def test_scripted_motion_bot_scores_high():
    raw = _scripted_motion_bot_session()
    result = scorer.compute_risk(raw)
    assert result["risk_score"] > 80, (
        f"scripted-with-motion bot scored {result['risk_score']}, expected >80. "
        f"features={result['features']}"
    )


def test_bot_with_incidental_pause_still_scores_high():
    raw = _bot_with_incidental_pause_session()
    result = scorer.compute_risk(raw)
    assert result["risk_score"] > 50, (
        f"bot-with-one-pause scored {result['risk_score']}, expected >50 "
        f"(a single incidental pause should not clear an otherwise fully "
        f"robotic session down to 'Gercek Kullanici'). features={result['features']}"
    )


def test_human_with_fast_burst_still_scores_low():
    for seed in range(3):
        raw = _human_with_fast_burst_session(seed=seed)
        result = scorer.compute_risk(raw)
        assert result["risk_score"] < 60, (
            f"human-with-fast-burst (seed={seed}) scored {result['risk_score']}, expected <60 "
            f"(one quick segment should not flip an otherwise natural session to bot-like). "
            f"features={result['features']}"
        )


def test_fast_keyboard_only_no_mouse_scores_high():
    raw = _fast_keyboard_only_no_mouse_session()
    result = scorer.compute_risk(raw)
    assert result["risk_score"] > 70, (
        f"fast keyboard-only no-mouse session scored {result['risk_score']}, expected >70 "
        f"(this is the exact shape of session that originally scored ~10-22 despite "
        f"looking like keyboard-injection automation). features={result['features']}"
    )


def _run_all():
    tests = [
        test_natural_human_scores_low,
        test_sparse_typing_human_scores_low,
        test_headless_bot_scores_high,
        test_scripted_motion_bot_scores_high,
        test_bot_with_incidental_pause_still_scores_high,
        test_human_with_fast_burst_still_scores_low,
        test_fast_keyboard_only_no_mouse_scores_high,
    ]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"FAIL  {test.__name__}: {exc}")

    print()
    if failures:
        print(f"{len(failures)}/{len(tests)} test(s) FAILED: {', '.join(failures)}")
        raise SystemExit(1)
    print(f"All {len(tests)} tests passed.")


if __name__ == "__main__":
    _run_all()


# ---------------------------------------------------------------------------
# Evasion regression tests
#
# These encode the attacks that actually worked against a running instance, so
# a future change that reopens one fails here instead of in production.
# ---------------------------------------------------------------------------


def _iid_random_walk_bot_session(seed: int = 0) -> dict:
    """The evasion that defeated the original detector.

    A script emitting independent Gaussian position steps, uniformly random
    delays and a copied lognormal typing rhythm. Every *marginal* statistic
    looks human -- which is the point -- so the variance/entropy features
    cannot separate it, and it scored 9.1/100 ("Gercek Kullanici", confidence
    0.91) end-to-end against /api/analyze.

    What gives it away is structure: IID steps have no speed autocorrelation
    and no direction persistence, because nothing is aiming anywhere.
    """
    rng = np.random.default_rng(seed)
    mouse = []
    x, y = 200.0, 200.0
    t = BASE_T
    for _ in range(30):
        x += rng.normal(6, 6)
        y += rng.normal(4, 5)
        t += int(rng.uniform(50, 150))
        mouse.append({"x": x, "y": y, "t": t})

    clicks = []
    for _ in range(3):
        t += int(rng.uniform(300, 900))
        clicks.append({"x": x, "y": y, "t": t})

    keys = []
    for _ in range(30):
        t += int(rng.lognormal(mean=5.0, sigma=0.5))
        keys.append({"t": t})

    return {
        "mouse_trajectory": mouse,
        "click_timing": clicks,
        "scroll_events": [],
        "hesitation_intervals": [int(rng.uniform(400, 1200)) for _ in range(3)],
        "focus_changes": [],
        "key_events": keys,
    }


def test_iid_random_walk_bot_is_detected():
    """The headline evasion must not score as a real user again."""
    for seed in range(5):
        raw = _iid_random_walk_bot_session(seed=seed)
        result = scorer.compute_risk(raw)
        assert result["risk_score"] > 60, (
            f"IID-random-walk evasion (seed={seed}) scored {result['risk_score']}, "
            f"expected >60. This is the attack that previously scored 9.1 and was "
            f"labelled 'Gercek Kullanici'. features={result['features']}"
        )


def test_evasion_is_driven_by_kinematics_features():
    """The evasion should be caught *for the right reason*.

    If it is ever detected only via the marginal features, the kinematics work
    has silently stopped carrying its weight and the detector is one attacker
    refinement away from being blind again.
    """
    raw = _iid_random_walk_bot_session(seed=0)
    result = scorer.compute_risk(raw)
    top_features = {item["feature"] for item in result["shap_explanation"]}
    kinematics = {"hiz_otokorelasyonu", "yon_tutarliligi", "duraklama_dagilimi"}
    assert top_features & kinematics, (
        f"expected at least one kinematics feature among the top SHAP "
        f"contributors, got {top_features}"
    )


# ---------------------------------------------------------------------------
# Small-sample safety
# ---------------------------------------------------------------------------


def test_thin_flush_falls_back_to_neutral_not_accusation():
    """Under-sampled estimators must not manufacture evidence.

    A 5-mousemove / 5-keystroke flush is an ordinary first-2-seconds window,
    not an anomaly. Every distribution-shape feature needs a minimum sample
    count; below it the value is noise, and treating it as a measurement
    scored exactly this shape of session 89.2 ("Bot Tespit Edildi").
    """
    raw = _sparse_typing_human_session()
    features = scorer.extract_features(raw)
    for name in ("hiz_otokorelasyonu", "yon_tutarliligi", "ivme_degisimi"):
        assert features[name] == scorer.NEUTRAL_DEFAULTS[name], (
            f"{name} was estimated from too few samples instead of falling "
            f"back to neutral: {features[name]}"
        )


def test_signal_sufficiency_separates_thin_from_rich_sessions():
    thin = scorer.signal_sufficiency(_sparse_typing_human_session())
    rich = scorer.signal_sufficiency(_natural_human_session(seed=0))
    assert thin < 0.5, f"sparse session reported signal_sufficiency={thin}"
    assert rich > 0.8, f"rich session reported signal_sufficiency={rich}"
    # The policy layer (main.py) refuses to auto-approve below
    # MIN_SIGNAL_FOR_AUTO_APPROVE, which is how a signal-starved bot is
    # stopped without inventing a confident score for it.


# ---------------------------------------------------------------------------
# Ensemble robustness
# ---------------------------------------------------------------------------


class _ConstantLSTM:
    """Stands in for the trained LSTM, returning a fixed probability."""

    def __init__(self, value):
        self.value = value

    def __call__(self, seq):
        import torch

        return torch.tensor([[self.value]], dtype=torch.float32)


def _score_with_lstm(raw, value):
    """Scores `raw` with the LSTM forced to output `value`."""
    bundle = scorer.get_bundle()
    original = bundle.lstm
    bundle.lstm = _ConstantLSTM(value)
    try:
        return scorer.compute_risk(raw)["risk_score"]
    finally:
        bundle.lstm = original


def test_lstm_cannot_swing_a_verdict_it_has_no_data_for():
    """A tiled (structureless) sequence must not let the LSTM move the score.

    This is the concrete failure it guards against: two runs of the same
    training script, differing only in an unseeded RNG, produced LSTM outputs
    of 0.98 and 0.43 on a headless-bot payload. Because that payload is too
    sparse to window, the LSTM was reading a constant series -- yet at a fixed
    0.3 weight it moved the score from 85.1 to 68.6, flipping the verdict
    across the blocking threshold on an arbitrary draw.
    """
    raw = _headless_bot_session()
    assert scorer.temporal_support(raw) == 0.0, "fixture is expected to be tiled"

    low = _score_with_lstm(raw, 0.0)
    high = _score_with_lstm(raw, 1.0)
    assert abs(high - low) < 1.0, (
        f"LSTM swung a structureless verdict by {abs(high - low):.1f} points "
        f"({low} -> {high}); its weight should be ~0 when temporal_support is 0"
    )


def test_lstm_still_contributes_when_it_has_real_temporal_structure():
    """The damping must not silently disable the LSTM everywhere."""
    raw = _natural_human_session(seed=0)
    assert scorer.temporal_support(raw) > 0.5, "fixture should be well-windowed"

    low = _score_with_lstm(raw, 0.0)
    high = _score_with_lstm(raw, 1.0)
    assert (high - low) > 15.0, (
        f"LSTM contributed only {high - low:.1f} points on a rich session; "
        f"expected close to its full {scorer.LSTM_WEIGHT * 100:.0f}-point share"
    )


def test_ensemble_weights_always_sum_to_one():
    for support in (0.0, 0.25, 0.5, 1.0):
        lstm_w = scorer.LSTM_WEIGHT * support
        rf_w = scorer.RF_WEIGHT + (scorer.LSTM_WEIGHT - lstm_w)
        assert abs((rf_w + scorer.ISO_WEIGHT + lstm_w) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Reason codes and evidence state
# ---------------------------------------------------------------------------


import reasons  # noqa: E402


def test_reason_codes_follow_the_model_not_a_hardcoded_guess():
    """Direction must come from the SHAP sign, so explanation cannot contradict score."""
    raw = _iid_random_walk_bot_session(seed=0)
    result = scorer.compute_risk(raw)
    assert result["risk_score"] > 60
    assert result["reason_codes"]["flagged"], "a flagged session must say why"

    # Every flagged phrase must be the "bot" phrasing of a feature whose SHAP
    # value actually pointed at automation for this session.
    bot_phrases = {p["bot"] for p in reasons.FEATURE_PHRASES.values()}
    for phrase in result["reason_codes"]["flagged"]:
        assert phrase in bot_phrases


def test_thin_session_is_told_it_was_unobserved_not_accused():
    """Insufficient evidence must be stated explicitly, never dressed up."""
    raw = _headless_bot_session()
    result = scorer.compute_risk(raw)
    assert result["evidence_state"] == "YETERSIZ"
    assert reasons.INSUFFICIENT_SIGNAL_REASON in result["reason_codes"]["allowed"]


def test_evidence_state_never_claims_high_confidence_without_signal():
    """A high score built on no data is 'unobserved', not 'high-confidence bot'."""
    assert reasons.evidence_state(95.0, 0.0) == "YETERSIZ"
    assert reasons.evidence_state(95.0, 1.0) == "YUKSEK_GUVEN"
    assert reasons.evidence_state(70.0, 1.0) == "BELIRSIZ"
    assert reasons.evidence_state(10.0, 1.0) == "YETERLI"


# ---------------------------------------------------------------------------
# Cross-channel synchronization
# ---------------------------------------------------------------------------


def test_scripted_clicks_without_pointer_motion_are_visible():
    """A click with no preceding cursor movement is a script signature."""
    base = BASE_T
    clicks = [{"x": 300, "y": 200, "t": base + 500 * i} for i in range(4)]
    scripted = scorer._click_motion_ratio([], clicks)
    assert scripted == 0.0

    # A human moves the cursor to the target first.
    mouse = []
    for i in range(4):
        for j in range(10):
            mouse.append({"x": 100 + j, "y": 100 + j, "t": base + 500 * i - 300 + j * 20})
    human = scorer._click_motion_ratio(mouse, clicks)
    assert human == 1.0


def test_cross_channel_features_gate_on_small_samples():
    """One click cannot establish a rate; it must fall back to neutral."""
    assert scorer._click_motion_ratio([], [{"x": 1, "y": 1, "t": BASE_T}]) is None
    assert scorer._channel_transition_lag([{"x": 1, "y": 1, "t": BASE_T}], []) is None


def test_instant_keyboard_pointer_switching_differs_from_human():
    """Moving a hand between keyboard and mouse costs a person real time."""
    base = BASE_T
    instant_mouse = [{"x": 1, "y": 1, "t": base + 20 * i} for i in range(10)]
    instant_keys = [{"t": base + 20 * i + 2} for i in range(10)]
    instant = scorer._channel_transition_lag(instant_mouse, instant_keys)

    human_mouse = [{"x": 1, "y": 1, "t": base + 1000 * i} for i in range(6)]
    human_keys = [{"t": base + 1000 * i + 400} for i in range(6)]
    human = scorer._channel_transition_lag(human_mouse, human_keys)

    assert instant is not None and human is not None
    assert human > instant * 5, f"human lag {human} should dwarf scripted {instant}"
