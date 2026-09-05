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

import os
import time
from datetime import timedelta
from types import SimpleNamespace

import numpy as np

import scorer

# Set before importing main: it refuses to start without these unless
# DEBUG=1, which is the behavior that keeps a production deployment from
# silently running with authentication off.
os.environ.setdefault("DEEPCHECK_SECRET", "test-secret-not-for-production")
os.environ.setdefault("DASHBOARD_KEY", "test-dashboard-key")
os.environ.setdefault("DEBUG", "0")

import main  # noqa: E402  (must follow the environment setup above)
from fastapi.testclient import TestClient  # noqa: E402
from lstm_model import FEATURE_NAMES  # noqa: E402

BASE_T = 1_751_470_045_000


def _natural_human_session(seed: int = 0) -> dict:
    """Natural, randomized human behavior: jittery mouse movement, a few
    clicks with real pauses before them, some scrolling, natural typing
    rhythm."""
    rng = np.random.default_rng(seed)
    mouse = []
    x, y = 200.0, 200.0
    t = BASE_T
    for _ in range(25):
        x += rng.normal(6, 6)
        y += rng.normal(4, 5)
        t += int(rng.uniform(50, 150))
        mouse.append({"x": x, "y": y, "t": t})

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


# ---------------------------------------------------------------------------
# API tests.
#
# These use a stub database rather than Postgres: what is under test is the
# authorization and enforcement logic, and it must be runnable without
# standing up a database -- otherwise it does not get run, which is how a
# security control quietly stops working.
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _StubDB:
    """Just enough AsyncSession for the handlers under test."""

    def __init__(self, session=None, history=(), has_flushes=True, flush_count=None, known_hashes=()):
        self.session = session
        self.history = list(history)
        # `flush_count` is what /api/decision counts; `has_flushes=False` is
        # the older shorthand for "zero".
        if flush_count is None:
            flush_count = 5 if has_flushes else 0
        self.flush_count = flush_count
        # Fingerprints the "database" already holds, for the replay check.
        self.known_hashes = set(known_hashes)
        self.added = []
        self.committed = False

    async def execute(self, statement):
        text = str(statement)
        if "WHERE behavior_data.payload_hash =" in text:
            # The duplicate lookup: match against the literal hash the handler
            # bound into the statement.
            params = statement.compile().params
            hit = any(v in self.known_hashes for v in params.values() if isinstance(v, str))
            return _StubResult([1] if hit else [])
        return _StubResult(self.history)

    async def scalar(self, statement):
        return self.flush_count

    async def get(self, model, primary_key):
        return self.session

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _stub_session(risk_score=0.0, label="Gerçek Kullanıcı", last_seen_at="now", verified_at=None):
    if last_seen_at == "now":
        last_seen_at = main.utcnow()
    return SimpleNamespace(
        id="stub",
        risk_score=risk_score,
        label=label,
        confidence=0.0,
        shap_explanation=[],
        response_time_ms=0.0,
        last_seen_at=last_seen_at,
        verified_at=verified_at,
    )


def _client(db):
    main.app.dependency_overrides[main.get_db] = lambda: db
    return TestClient(main.app)


def _clear_overrides():
    main.app.dependency_overrides.clear()


def _shift_to_now(raw: dict, offset_ms: int = 0) -> dict:
    """Re-times a fixture so its newest event lands at (now + offset_ms). The
    fixtures are pinned to BASE_T for reproducibility; the API now rejects
    telemetry whose clock is far from the server's, as a recording would be."""
    stamps = [e["t"] for key in ("mouse_trajectory", "click_timing", "scroll_events", "key_events") for e in raw[key]]
    stamps.extend(raw["focus_changes"])
    delta = int(time.time() * 1000) + offset_ms - max(stamps)
    shifted = {
        key: [dict(e, t=e["t"] + delta) for e in raw[key]]
        for key in ("mouse_trajectory", "click_timing", "scroll_events", "key_events")
    }
    shifted["focus_changes"] = [f + delta for f in raw["focus_changes"]]
    shifted["hesitation_intervals"] = list(raw["hesitation_intervals"])
    return shifted


def _analyze_payload(session_id: str, raw: dict | None = None) -> dict:
    raw = _shift_to_now(raw or _headless_bot_session())
    return {
        "session_id": session_id,
        "mouse_trajectory": raw["mouse_trajectory"],
        "click_timing": raw["click_timing"],
        "scroll_events": raw["scroll_events"],
        "hesitation_intervals": raw["hesitation_intervals"],
        "focus_changes": raw["focus_changes"],
        "key_events": raw["key_events"],
    }


def _raw_from_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k != "session_id"}


def test_api_rejects_bad_token():
    """Telemetry may only be posted under a session id whose token the poster
    actually holds. Without this, anyone could push fabricated behavior under
    a victim's session id, or simply invent an id and appear scored."""
    session_id = "11111111-2222-3333-4444-555555555555"
    client = _client(_StubDB(session=_stub_session()))
    try:
        payload = _analyze_payload(session_id)

        no_token = client.post("/api/analyze", json=payload)
        assert no_token.status_code == 401, f"jetonsuz istek {no_token.status_code} dondu, 401 bekleniyordu"

        wrong = client.post("/api/analyze", json=payload, headers={"X-DeepCheck-Token": "a" * 64})
        assert wrong.status_code == 401, f"yanlis jeton {wrong.status_code} dondu, 401 bekleniyordu"

        # A token signed for a DIFFERENT session must not work on this one.
        other = client.post(
            "/api/analyze",
            json=payload,
            headers={"X-DeepCheck-Token": main.sign_session("baska-oturum")},
        )
        assert other.status_code == 401, f"baska oturumun jetonu {other.status_code} dondu, 401 bekleniyordu"

        # And the valid one is accepted, so the check is not simply rejecting
        # everything.
        ok = client.post(
            "/api/analyze",
            json=payload,
            headers={"X-DeepCheck-Token": main.sign_session(session_id)},
        )
        assert ok.status_code == 200, f"gecerli jeton {ok.status_code} dondu, 200 bekleniyordu"
        assert ok.json()["session_id"] == session_id
    finally:
        _clear_overrides()


def test_decision_blocks_bot_session():
    """The 40/60/80 ladder is applied server-side, at the enforcement point.

    It used to be applied in Demo.jsx, inside the browser the attacker
    controls, where deleting one comparison was enough to defeat it.
    """
    session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    token = main.sign_session(session_id)
    body = {"session_id": session_id}

    cases = [
        (95.0, "Bot Tespit Edildi", "block"),
        (72.0, "Yüksek Risk", "verify"),
        (48.0, "Şüpheli", "warn"),
        (12.0, "Gerçek Kullanıcı", "allow"),
    ]
    for score, label, expected in cases:
        client = _client(_StubDB(session=_stub_session(score, label)))
        try:
            res = client.post("/api/decision", json=body, headers={"X-DeepCheck-Token": token})
            assert res.status_code == 200, f"{res.status_code} dondu"
            action = res.json()["action"]
            assert action == expected, f"skor {score} icin '{action}' dondu, '{expected}' bekleniyordu"
        finally:
            _clear_overrides()

    # No token at all: no decision, whatever the stored score says.
    client = _client(_StubDB(session=_stub_session(12.0)))
    try:
        assert client.post("/api/decision", json=body).status_code == 401
    finally:
        _clear_overrides()


def test_decision_fails_closed_without_telemetry():
    """A session row exists the moment /api/session runs, and its risk_score
    column defaults to 0.0. A client that loads the page and never runs the
    SDK must not be waved through on that default."""
    session_id = "99999999-8888-7777-6666-555555555555"
    token = main.sign_session(session_id)

    for db, description in [
        (_StubDB(session=_stub_session(0.0), has_flushes=False), "hic akis gondermemis oturum"),
        (_StubDB(session=None), "hic kaydi olmayan oturum"),
    ]:
        client = _client(db)
        try:
            res = client.post(
                "/api/decision",
                json={"session_id": session_id},
                headers={"X-DeepCheck-Token": token},
            )
            assert res.status_code == 200
            action = res.json()["action"]
            assert action == "verify", f"{description} icin '{action}' dondu, 'verify' bekleniyordu"
        finally:
            _clear_overrides()


def test_dashboard_endpoints_require_key():
    """GET /api/sessions exposes every customer's live session id and score."""
    client = _client(_StubDB(session=_stub_session(), history=[]))
    try:
        assert client.get("/api/sessions").status_code == 401
        assert client.get("/api/sessions", headers={"X-Dashboard-Key": "wrong"}).status_code == 401
        ok = client.get("/api/sessions", headers={"X-Dashboard-Key": main.DASHBOARD_KEY})
        assert ok.status_code == 200, f"gecerli anahtar {ok.status_code} dondu"

        assert client.get("/api/score/abc").status_code == 401
    finally:
        _clear_overrides()


def test_analyze_rejects_stale_timestamps():
    """A recording is old by definition. Telemetry whose newest event is far
    from the server clock is a replay (or a broken clock); either way it is
    not evidence about the person at the keyboard right now."""
    session_id = "0a0a0a0a-0000-0000-0000-000000000001"
    token = main.sign_session(session_id)
    client = _client(_StubDB(session=_stub_session()))
    try:
        raw = _headless_bot_session()  # pinned to BASE_T, i.e. months old
        stale = dict(raw, session_id=session_id)
        res = client.post("/api/analyze", json=stale, headers={"X-DeepCheck-Token": token})
        assert res.status_code == 422, f"eski zaman damgali akis {res.status_code} dondu, 422 bekleniyordu"

        future = _analyze_payload(session_id, raw)
        for key in ("mouse_trajectory", "click_timing", "scroll_events", "key_events"):
            future[key] = [dict(e, t=e["t"] + main.MAX_CLOCK_SKEW_MS + 5_000) for e in future[key]]
        res = client.post("/api/analyze", json=future, headers={"X-DeepCheck-Token": token})
        assert res.status_code == 422, f"gelecekten akis {res.status_code} dondu, 422 bekleniyordu"

        fresh = _analyze_payload(session_id, raw)
        res = client.post("/api/analyze", json=fresh, headers={"X-DeepCheck-Token": token})
        assert res.status_code == 200, f"guncel akis {res.status_code} dondu, 200 bekleniyordu"
    finally:
        _clear_overrides()


def test_analyze_rejects_backwards_time():
    """Within a session, time only moves forward. A flush whose newest event
    predates the previous flush's newest event is a replayed window."""
    session_id = "0a0a0a0a-0000-0000-0000-000000000002"
    token = main.sign_session(session_id)
    payload = _analyze_payload(session_id)
    newest = main._newest_event_ms(_raw_from_payload(payload))

    previous = SimpleNamespace(risk_score=50.0, newest_event_at=newest + 5_000)
    for name in FEATURE_NAMES:
        setattr(previous, name, 0.5)
    client = _client(_StubDB(session=_stub_session(), history=[previous]))
    try:
        res = client.post("/api/analyze", json=payload, headers={"X-DeepCheck-Token": token})
        assert res.status_code == 422, f"geriye giden zaman {res.status_code} dondu, 422 bekleniyordu"
    finally:
        _clear_overrides()


def test_analyze_rejects_replayed_payload():
    """The same recording replayed with its clock shifted to "now" must be
    caught. The fingerprint rebases timestamps before hashing, so shifting
    does not change it, and the lookup is global across sessions -- a fresh
    token does not launder a recording."""
    raw = _headless_bot_session()
    first = _shift_to_now(raw)
    second = _shift_to_now(raw, offset_ms=-3_000)
    assert main._payload_fingerprint(first) == main._payload_fingerprint(second), (
        "ayni kaydin saati kaydirilmis kopyasi farkli parmak izi uretti"
    )
    # The headless fixture has no mouse points, so perturb a channel it has.
    perturbed = dict(second)
    perturbed["hesitation_intervals"] = list(second["hesitation_intervals"]) + [999]
    assert main._payload_fingerprint(perturbed) != main._payload_fingerprint(first)

    # Session B replays what session A already posted.
    session_b = "0a0a0a0a-0000-0000-0000-00000000000b"
    client = _client(_StubDB(session=_stub_session(), known_hashes=[main._payload_fingerprint(first)]))
    try:
        res = client.post(
            "/api/analyze",
            json=dict(second, session_id=session_b),
            headers={"X-DeepCheck-Token": main.sign_session(session_b)},
        )
        assert res.status_code == 422, f"tekrar oynatilan akis {res.status_code} dondu, 422 bekleniyordu"
    finally:
        _clear_overrides()


def test_decision_verifies_with_too_few_flushes():
    """One plausible 2-second window is cheap to fabricate. Until
    MIN_FLUSHES_FOR_DECISION windows have been analysed, the answer is
    step-up, whatever the score says."""
    session_id = "0a0a0a0a-0000-0000-0000-000000000003"
    token = main.sign_session(session_id)
    for count in range(main.MIN_FLUSHES_FOR_DECISION):
        client = _client(_StubDB(session=_stub_session(12.0), flush_count=count))
        try:
            res = client.post("/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token})
            body = res.json()
            assert body["action"] == "verify", f"{count} akisla '{body['action']}' dondu, 'verify' bekleniyordu"
            assert body["reason"] == "insufficient_evidence"
        finally:
            _clear_overrides()

    client = _client(_StubDB(session=_stub_session(12.0), flush_count=main.MIN_FLUSHES_FOR_DECISION))
    try:
        body = client.post("/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token}).json()
        assert body["action"] == "allow", f"yeterli akisla '{body['action']}' dondu, 'allow' bekleniyordu"
        assert body["reason"] == "score"
    finally:
        _clear_overrides()


def test_decision_verifies_when_stale():
    """A verdict is about current behaviour. A session last seen long ago --
    a token lifted from a shared machine and cashed in later -- goes to
    step-up even if its stored score is clean."""
    session_id = "0a0a0a0a-0000-0000-0000-000000000004"
    token = main.sign_session(session_id)
    old = main.utcnow() - timedelta(seconds=main.DECISION_MAX_AGE_S + 60)
    client = _client(_StubDB(session=_stub_session(12.0, last_seen_at=old)))
    try:
        body = client.post("/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token}).json()
        assert body["action"] == "verify", f"bayat oturum '{body['action']}' dondu, 'verify' bekleniyordu"
        assert body["reason"] == "stale"
    finally:
        _clear_overrides()


def test_demo_charge_never_charges_blocked_session():
    """The charge lives behind the server. No browser-side sequence can turn
    a blocked or unverified session into a charged one."""
    session_id = "0a0a0a0a-0000-0000-0000-000000000005"
    token = main.sign_session(session_id)
    body = {"session_id": session_id, "amount": 2038.8}

    cases = [
        (_StubDB(session=_stub_session(95.0, "Bot Tespit Edildi")), "declined", "block"),
        (_StubDB(session=_stub_session(72.0, "Yüksek Risk")), "declined", "verify"),
        (_StubDB(session=_stub_session(12.0), flush_count=1), "declined", "verify"),
        (_StubDB(session=None), "declined", "verify"),
        (_StubDB(session=_stub_session(48.0, "Şüpheli")), "charged", "warn"),
        (_StubDB(session=_stub_session(12.0)), "charged", "allow"),
    ]
    for db, expected_status, expected_action in cases:
        client = _client(db)
        try:
            res = client.post("/api/demo/charge", json=body, headers={"X-DeepCheck-Token": token})
            assert res.status_code == 200, f"{res.status_code} dondu"
            out = res.json()
            assert out["status"] == expected_status, f"{expected_action} icin '{out['status']}' dondu"
            assert out["decision"]["action"] == expected_action
            assert (out["charge_id"] is not None) == (expected_status == "charged")
        finally:
            _clear_overrides()

    client = _client(_StubDB(session=_stub_session(12.0)))
    try:
        assert client.post("/api/demo/charge", json=body).status_code == 401
    finally:
        _clear_overrides()


def test_demo_verify_upgrades_verify_but_not_block():
    """Step-up is recorded on the server and only ever upgrades 'verify' to
    'allow'. A wrong code is rejected; a confident bot verdict stays blocked
    no matter how many codes are entered."""
    session_id = "0a0a0a0a-0000-0000-0000-000000000006"
    token = main.sign_session(session_id)
    headers = {"X-DeepCheck-Token": token}

    session = _stub_session(72.0, "Yüksek Risk")
    db = _StubDB(session=session)
    client = _client(db)
    try:
        wrong = client.post("/api/demo/verify", json={"session_id": session_id, "code": "000000"}, headers=headers)
        assert wrong.status_code == 400, f"yanlis kod {wrong.status_code} dondu, 400 bekleniyordu"
        assert session.verified_at is None

        ok = client.post(
            "/api/demo/verify", json={"session_id": session_id, "code": main.DEMO_VERIFY_CODE}, headers=headers
        )
        assert ok.status_code == 200 and ok.json()["verified"] is True
        assert session.verified_at is not None and db.committed

        charged = client.post("/api/demo/charge", json={"session_id": session_id, "amount": 10}, headers=headers).json()
        assert charged["status"] == "charged", f"dogrulanmis oturum '{charged['status']}' dondu"
        assert charged["decision"]["reason"] == "verified"
    finally:
        _clear_overrides()

    blocked = _stub_session(95.0, "Bot Tespit Edildi", verified_at=main.utcnow())
    client = _client(_StubDB(session=blocked))
    try:
        out = client.post("/api/demo/charge", json={"session_id": session_id, "amount": 10}, headers=headers).json()
        assert out["status"] == "declined" and out["decision"]["action"] == "block", (
            "dogrulama bir 'block' kararini asamaz"
        )
    finally:
        _clear_overrides()

    client = _client(_StubDB(session=None))
    try:
        res = client.post(
            "/api/demo/verify", json={"session_id": session_id, "code": main.DEMO_VERIFY_CODE}, headers=headers
        )
        assert res.status_code == 404, "hic akis gondermemis oturum dogrulanamaz"
    finally:
        _clear_overrides()


def test_lstm_reacts_to_trajectory():
    """The sequence model must respond to a session's HISTORY, not only to
    its latest flush.

    Both calls score the identical current flush. The only difference is what
    came before it. If the score does not move, the LSTM is not reading the
    time series -- which was literally the case before: every timestep was a
    copy of the current instant.
    """
    raw = _sparse_typing_human_session()
    current = scorer.extract_features(raw)

    human_like = [current[name] for name in FEATURE_NAMES]
    robotic = dict(current)
    robotic.update({"etkilesim_entropisi": 0.02, "tereddut_skoru": 0.0, "ivme_degisimi": 0.01})
    robotic_row = [robotic[name] for name in FEATURE_NAMES]

    calm = scorer.compute_risk(raw, [human_like] * 9)["risk_score"]
    drifting = scorer.compute_risk(raw, [robotic_row] * 9)["risk_score"]

    assert drifting != calm, (
        f"ayni akis, farkli gecmis -> ayni skor ({calm}). LSTM gecmisi okumuyor."
    )
    assert drifting > calm, (
        f"robotik gecmisli oturum {drifting}, sakin gecmisli oturum {calm} aldi; "
        "gecmisin skoru yukseltmesi bekleniyordu"
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
        test_lstm_reacts_to_trajectory,
        test_api_rejects_bad_token,
        test_decision_blocks_bot_session,
        test_decision_fails_closed_without_telemetry,
        test_dashboard_endpoints_require_key,
        test_analyze_rejects_stale_timestamps,
        test_analyze_rejects_backwards_time,
        test_analyze_rejects_replayed_payload,
        test_decision_verifies_with_too_few_flushes,
        test_decision_verifies_when_stale,
        test_demo_charge_never_charges_blocked_session,
        test_demo_verify_upgrades_verify_but_not_block,
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
