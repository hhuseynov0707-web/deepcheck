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
# The /api/demo/* endpoints are off by default outside DEBUG, because their
# step-up code is a published constant. These tests exercise that flow on
# purpose, so they opt in explicitly -- which is also the only way a real
# deployment should ever enable them.
os.environ.setdefault("DEMO_ENDPOINTS", "1")

import main  # noqa: E402  (must follow the environment setup above)
from fastapi.testclient import TestClient  # noqa: E402
from lstm_model import FEATURE_NAMES, BehaviorLSTM  # noqa: E402

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

    def __init__(
        self,
        session=None,
        history=(),
        has_flushes=True,
        flush_count=None,
        known_hashes=(),
        per_flush=None,
        cluster_peers=0,
    ):
        self.session = session
        self.history = list(history)
        # `flush_count` is what /api/decision used to count; `has_flushes=False`
        # is the older shorthand for "zero".
        if flush_count is None:
            flush_count = 5 if has_flushes else 0
        self.flush_count = flush_count
        # Per-flush scores the sequential test reads. Defaulting them to the
        # session's own score keeps every older test meaningful: a session
        # sitting at 95 got there by producing flushes at 95.
        if per_flush is None:
            score = getattr(session, "risk_score", 0.0) if session is not None else 0.0
            per_flush = [score] * flush_count
        self.per_flush = list(per_flush)
        # Distinct other sessions sharing this session's behaviour bucket.
        self.cluster_peers = cluster_peers
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
        if text.startswith("SELECT behavior_data.risk_score, behavior_data.behavior_bucket"):
            # The sequential test's read: (risk_score, behavior_bucket) rows.
            # The real query is LIMIT SPRT_MAX_FLUSHES, so the sequential
            # statistic can never accumulate over more than that many flushes.
            # Without mirroring the limit here a long ambiguous session drifts
            # across a bound in the stub and nowhere else.
            newest = self.per_flush[: main.SPRT_MAX_FLUSHES]
            return _StubResult([(score, "bucket") for score in newest])
        return _StubResult(self.history)

    async def scalar(self, statement):
        if "count(DISTINCT" in str(statement).lower().replace("count(distinct", "count(DISTINCT"):
            return self.cluster_peers
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

    # A mid-band score (40-60) no longer resolves on five flushes. The
    # sequential test only stops once the evidence supports a verdict, and a
    # session hovering at 48 supports neither -- so it goes to step-up rather
    # than being charged with a warning banner, which is the outcome the
    # adversarial run flagged: "Şüpheli" used to mean the card was charged.
    # Past SPRT_MAX_FLUSHES the ladder applies regardless, which is the
    # `warn` row below.
    # A mid-band score never resolves to a charge, however long the session
    # runs. It used to fall through to the ladder at the flush cap, which made
    # twenty seconds of deliberately ambiguous behaviour a way to be approved.
    cases = [
        (95.0, "Bot Tespit Edildi", "block", 5),
        (72.0, "Yüksek Risk", "verify", 5),
        (48.0, "Şüpheli", "verify", 5),
        (48.0, "Şüpheli", "verify", main.SPRT_MAX_FLUSHES),
        (48.0, "Şüpheli", "verify", main.SPRT_MAX_FLUSHES * 3),
        (12.0, "Gerçek Kullanıcı", "allow", 5),
    ]
    for score, label, expected, flushes in cases:
        client = _client(_StubDB(session=_stub_session(score, label), flush_count=flushes))
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


def test_decision_waits_for_sequential_evidence():
    """Evidence, not a counter.

    A fixed "three flushes" was a number chosen by judgement. The sequential
    test stops as soon as the accumulated log-likelihood ratio supports a
    verdict, so a blatant session is decided immediately and an ambiguous one
    keeps collecting instead of being waved through the moment a counter is
    satisfied.
    """
    session_id = "0a0a0a0a-0000-0000-0000-000000000003"
    token = main.sign_session(session_id)

    def decide(db):
        client = _client(db)
        try:
            return client.post(
                "/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token}
            ).json()
        finally:
            _clear_overrides()

    # No telemetry at all: nothing to test on.
    body = decide(_StubDB(session=_stub_session(12.0), flush_count=0))
    assert body["action"] == "verify" and body["reason"] == "insufficient_evidence"

    # One flush is never enough, however clean it looks. The lower SPRT bound
    # is crossed by a single flush scoring 9.17 or less, and one fabricated
    # window is the cheapest thing an attacker can produce -- so the floor sits
    # under the sequential test rather than being replaced by it.
    for count in range(1, main.MIN_FLUSHES_FOR_DECISION):
        body = decide(_StubDB(session=_stub_session(5.0), flush_count=count))
        assert body["action"] == "verify", f"{count} akisla '{body['action']}' dondu"
        assert body["reason"] == "insufficient_evidence"

    # Past the floor, a clear human is decided without waiting further.
    body = decide(_StubDB(session=_stub_session(9.0), flush_count=main.MIN_FLUSHES_FOR_DECISION))
    assert body["action"] == "allow", f"acik insan '{body['action']}' dondu"

    # A clear bot likewise.
    body = decide(_StubDB(session=_stub_session(96.0, "Bot Tespit Edildi"), flush_count=3))
    assert body["action"] == "block", f"acik bot '{body['action']}' dondu"

    # Genuine ambiguity keeps collecting rather than resolving either way.
    body = decide(_StubDB(session=_stub_session(50.0, "Şüpheli"), flush_count=3))
    assert body["reason"] == "insufficient_evidence", f"belirsiz oturum '{body['reason']}' dondu"

    # And the sequential statistic really is accumulating, not just reading the
    # latest score: the same session score with more flushes crosses the bound.
    few = decide(_StubDB(session=_stub_session(80.0, "Bot Tespit Edildi"), per_flush=[62.0] * 3, flush_count=3))
    many = decide(_StubDB(session=_stub_session(80.0, "Bot Tespit Edildi"), per_flush=[62.0] * 10, flush_count=10))
    assert few["reason"] == "insufficient_evidence" and many["reason"] == "score", (
        f"kanit birikmiyor: {few['reason']} -> {many['reason']}"
    )


def test_ambiguity_is_never_charged():
    """A session parked in the middle band must not be approved by outlasting
    the flush cap. Ambiguity at a payment gate is a reason to ask for more
    proof, not a reason to accept."""
    session_id = "0e0e0e0e-0000-0000-0000-000000000001"
    token = main.sign_session(session_id)
    for flushes in (main.SPRT_MAX_FLUSHES, main.SPRT_MAX_FLUSHES * 5):
        client = _client(_StubDB(session=_stub_session(50.0, "Şüpheli"), flush_count=flushes))
        try:
            body = client.post(
                "/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token}
            ).json()
            assert body["action"] == "verify", f"{flushes} akistan sonra '{body['action']}' dondu"
            assert body["reason"] == "ambiguous", f"gerekce '{body['reason']}'"
        finally:
            _clear_overrides()

    # And the charge endpoint honours it.
    client = _client(_StubDB(session=_stub_session(50.0, "Şüpheli"), flush_count=main.SPRT_MAX_FLUSHES))
    try:
        out = client.post(
            "/api/demo/charge",
            json={"session_id": session_id, "amount": 10},
            headers={"X-DeepCheck-Token": token},
        ).json()
        assert out["status"] == "declined", f"belirsiz oturum tahsil edildi: {out['status']}"
    finally:
        _clear_overrides()


def test_cluster_of_identical_sessions_is_escalated():
    """Per-session scoring cannot catch competent mimicry, and the adversarial
    run measured that: an independently written humanised bot scored 11.4
    against a human 11.3. What it cannot hide is running twenty-five times and
    producing twenty-five near-identical signatures."""
    session_id = "0d0d0d0d-0000-0000-0000-000000000001"
    token = main.sign_session(session_id)

    saved_flag = main.CLUSTER_ESCALATION_ENABLED
    main.CLUSTER_ESCALATION_ENABLED = True  # off by default; see the note there

    def decide(peers):
        client = _client(_StubDB(session=_stub_session(9.0), flush_count=4, cluster_peers=peers))
        try:
            return client.post(
                "/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token}
            ).json()
        finally:
            _clear_overrides()

    alone = decide(0)
    assert alone["action"] == "allow", f"tek basina oturum '{alone['action']}' dondu"

    crowd = decide(main.CLUSTER_MIN_SESSIONS)
    assert crowd["action"] == "verify", f"kume icindeki oturum '{crowd['action']}' dondu"
    assert crowd["reason"] == "cluster"

    # And it stays silent when disabled, which is the shipped default.
    main.CLUSTER_ESCALATION_ENABLED = False
    quiet = decide(main.CLUSTER_MIN_SESSIONS * 3)
    assert quiet["action"] == "allow", "kapaliyken kume yukseltmesi tetiklendi"
    main.CLUSTER_ESCALATION_ENABLED = saved_flag


def test_conformal_guard_only_softens_never_hardens():
    """A one-directional safety net: if a score is unremarkable among held-out
    real humans, refuse to block on it. A mistake here costs a challenge, not a
    customer -- and it must never turn an approval into a block."""
    session_id = "0d0d0d0d-0000-0000-0000-000000000002"
    token = main.sign_session(session_id)
    saved = scorer._bundle

    def decide(score, label):
        client = _client(_StubDB(session=_stub_session(score, label), flush_count=5))
        try:
            return client.post(
                "/api/decision", json={"session_id": session_id}, headers={"X-DeepCheck-Token": token}
            ).json()
        finally:
            _clear_overrides()

    try:
        # Calibration humans who routinely score in the 90s: a 95 is then
        # unremarkable for a person and must not be blocked on.
        # n must clear the finite-sample floor: the smallest p-value the
        # correction can produce is 1/(n+1), so asserting 5% needs n >= 19.
        scorer._bundle = SimpleNamespace(human_calibration=[92.0 + i * 0.2 for i in range(30)])
        softened = decide(95.0, "Bot Tespit Edildi")
        assert softened["action"] == "verify", f"korumaya ragmen '{softened['action']}' dondu"
        assert softened["reason"] == "conformal"

        # The same guard must not touch an approval.
        allowed = decide(9.0, "Gerçek Kullanıcı")
        assert allowed["action"] == "allow", f"koruma onayi bozdu: '{allowed['action']}'"

        # With a normal human calibration, a 95 is extraordinary and is blocked.
        scorer._bundle = SimpleNamespace(human_calibration=[3.0 + i * 0.3 for i in range(30)])
        blocked = decide(95.0, "Bot Tespit Edildi")
        assert blocked["action"] == "block", f"olagandisi skor '{blocked['action']}' dondu"
    finally:
        scorer._bundle = saved
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
        # 40-60 no longer charges on five flushes: the sequential test does not
        # yet support a verdict, so it goes to step-up. Past the flush cap the
        # ladder applies and it charges with a warning, as before.
        (_StubDB(session=_stub_session(48.0, "Şüpheli")), "declined", "verify"),
        (
            _StubDB(session=_stub_session(48.0, "Şüpheli"), flush_count=main.SPRT_MAX_FLUSHES),
            "declined",
            "verify",
        ),
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


def test_token_requires_proof_of_work_and_browser_timers():
    """/api/session hands out a challenge and nothing else.

    The token /api/analyze demands is only issued in exchange for a solved
    proof of work and runtime measurements a browser could actually produce,
    so telemetry cannot be posted by something that never executed the SDK.
    That is a statement about real code in a real engine, not about a human.
    """
    import hashlib as _hashlib

    main._rate_hits.clear()
    client = _client(_StubDB(session=_stub_session()))
    try:
        opened = client.post("/api/session").json()
        assert "token" not in opened, "oturum acilisinda jeton verildi"
        session_id, challenge = opened["session_id"], opened["challenge"]
        good_runtime = {"clock_resolution_us": 100.0, "timer_lag_ms": 1.4}

        def attest(**overrides):
            body = {
                "session_id": session_id,
                "challenge": challenge,
                "nonce": "0",
                "runtime": dict(good_runtime),
            }
            body.update(overrides)
            return client.post("/api/session/attest", json=body)

        # An unsolved nonce buys nothing.
        assert attest(nonce="0").status_code == 400, "cozulmemis is kaniti kabul edildi"

        nonce = None
        for candidate in range(200_000):
            digest = _hashlib.sha256(f"{challenge}.{candidate}".encode()).digest()
            if main._leading_zero_bits(digest) >= main.POW_DIFFICULTY_BITS:
                nonce = str(candidate)
                break
        assert nonce is not None, "zorluk cozulemeyecek kadar yuksek"

        # A clock with no clamp at all is not a browser.
        assert attest(nonce=nonce, runtime={"clock_resolution_us": 0.0, "timer_lag_ms": 1.4}).status_code == 400
        # Nor is an event loop that schedules instantly.
        assert attest(nonce=nonce, runtime={"clock_resolution_us": 100.0, "timer_lag_ms": 0.0}).status_code == 400

        ok = attest(nonce=nonce)
        assert ok.status_code == 201, f"gecerli kanit {ok.status_code} dondu"
        body = ok.json()
        assert body["attested"] is True
        assert body["token"] == main.sign_session(session_id)

        # A solution cannot be carried to a different session.
        other = "0f0f0f0f-0000-0000-0000-000000000001"
        moved = client.post(
            "/api/session/attest",
            json={"session_id": other, "challenge": challenge, "nonce": nonce, "runtime": good_runtime},
        )
        assert moved.status_code == 400, "cozum baska oturuma tasinabildi"
    finally:
        main._rate_hits.clear()
        _clear_overrides()


def test_rate_limit_rejects_a_burst():
    """Unlimited /api/session minting is free database growth, and unlimited
    /api/analyze is ~50ms of CPU per call against a fixed worker pool. Both
    were unbounded."""
    main._rate_hits.clear()
    client = _client(_StubDB(session=_stub_session()))
    try:
        limit, _ = main.RATE_LIMITS["session"]
        for i in range(limit):
            res = client.post("/api/session")
            assert res.status_code == 201, f"{i + 1}. istek {res.status_code} dondu"

        blocked = client.post("/api/session")
        assert blocked.status_code == 429, f"limit asildiktan sonra {blocked.status_code} dondu"
        assert blocked.headers.get("Retry-After"), "429 yanitinda Retry-After yok"

        # The limiter must not leak across buckets: analyze is keyed by session
        # id and has its own budget.
        session_id = "0b0b0b0b-0000-0000-0000-000000000001"
        ok = client.post(
            "/api/analyze",
            json=_analyze_payload(session_id),
            headers={"X-DeepCheck-Token": main.sign_session(session_id)},
        )
        assert ok.status_code == 200, f"ayri kovadaki istek {ok.status_code} dondu"
    finally:
        main._rate_hits.clear()
        _clear_overrides()


def test_rate_limiter_memory_is_bounded():
    """An attacker rotating session ids must not be able to grow the limiter's
    own bookkeeping without limit."""
    main._rate_hits.clear()
    try:
        for i in range(main._RATE_KEY_CAP + 500):
            main._rate_limit("analyze", f"key-{i}")
        assert len(main._rate_hits) <= main._RATE_KEY_CAP, (
            f"limiter {len(main._rate_hits)} anahtar tutuyor, tavan {main._RATE_KEY_CAP}"
        )
    finally:
        main._rate_hits.clear()


def test_bundle_requires_lstm_weights():
    """model.pkl without lstm_model.pt used to load a RANDOMLY initialised
    LSTM and let it contribute 30% of every score, with nothing logged and
    /api/health still reporting the model as loaded."""
    if not os.path.exists(scorer.LSTM_PATH):
        raise AssertionError("lstm_model.pt yok; once `python train_model.py` calistirin")

    hidden = scorer.LSTM_PATH + ".hidden"
    saved_bundle = scorer._bundle
    os.rename(scorer.LSTM_PATH, hidden)
    try:
        scorer._bundle = None
        raised = False
        try:
            scorer.get_bundle()
        except FileNotFoundError:
            raised = True
        assert raised, "lstm_model.pt eksikken model yuklendi; FileNotFoundError bekleniyordu"
    finally:
        os.rename(hidden, scorer.LSTM_PATH)
        scorer._bundle = saved_bundle

    # And /api/health reports the failure rather than claiming to be healthy.
    scorer._bundle = None
    os.rename(scorer.LSTM_PATH, hidden)
    try:
        client = _client(_StubDB())
        body = client.get("/api/health").json()
        assert body["model_loaded"] is False, "eksik agirlik dosyasiyla saglikli bildirildi"
        assert body["status"] == "model yüklenmedi"
    finally:
        os.rename(hidden, scorer.LSTM_PATH)
        scorer._bundle = saved_bundle
        _clear_overrides()


def test_client_signals_recorded_but_not_scored():
    """Provenance signals are stored for later measurement. Until they have
    been evaluated against real sessions they must not move the score -- a
    self-reported flag is something the client being judged can simply lie
    about."""
    session_id = "0b0b0b0b-0000-0000-0000-000000000002"
    token = main.sign_session(session_id)
    base = _headless_bot_session()

    plain = _analyze_payload(session_id, base)
    db_plain = _StubDB(session=_stub_session())
    client = _client(db_plain)
    try:
        first = client.post("/api/analyze", json=plain, headers={"X-DeepCheck-Token": token}).json()
    finally:
        _clear_overrides()

    flagged = _analyze_payload(session_id, base)
    flagged["client_signals"] = {
        "untrusted_events": 41,
        "webdriver": True,
        "pointer_mouse": 3,
        "pointer_pen": 0,
        "pointer_touch": 0,
    }
    db_flagged = _StubDB(session=_stub_session())
    client = _client(db_flagged)
    try:
        second = client.post("/api/analyze", json=flagged, headers={"X-DeepCheck-Token": token}).json()
    finally:
        _clear_overrides()

    assert second["risk_score"] == first["risk_score"], (
        f"istemci sinyalleri skoru degistirdi ({first['risk_score']} -> {second['risk_score']}); "
        "bu alanlar yalnizca kaydedilmeli, puanlanmamali"
    )

    stored = db_flagged.added[-1].client_signals
    assert stored["webdriver"] is True and stored["untrusted_events"] == 41, (
        f"istemci sinyalleri kaydedilmedi: {stored}"
    )
    # An older SDK that sends nothing must still be accepted, with defaults.
    assert db_plain.added[-1].client_signals["untrusted_events"] == 0


def test_training_seeds_torch():
    """The forests take random_state=42, but torch was left unseeded, so the
    LSTM's weight init, dropout and batch shuffling differed on every training
    run -- a different sequence model each time, carrying 30% of the ensemble
    weight. Retraining could move a session by more than ten risk points with
    no code change, and it made the "reproducible from a fixed seed" claim
    true only of the forests."""
    import importlib

    import torch

    import train_model

    importlib.reload(train_model)
    assert torch.initial_seed() == train_model.SEED, (
        f"train_model torch tohumunu ekmiyor (initial_seed={torch.initial_seed()}); "
        "LSTM her egitimde farkli cikar"
    )

    # And the seed actually makes initialisation reproducible.
    def fingerprint():
        torch.manual_seed(train_model.SEED)
        model = BehaviorLSTM()
        return torch.cat([p.detach().flatten() for p in model.parameters()])

    assert torch.equal(fingerprint(), fingerprint()), "ayni tohum farkli agirliklar uretti"


def test_demo_endpoints_off_by_default_outside_debug():
    """The demo step-up code is a fixed constant printed on the demo page, so
    anything scored `verify` can be upgraded to `allow` by anyone who reads it.
    Acceptable in a demo, never in a deployment -- so the endpoints must not be
    reachable unless someone turned them on deliberately."""
    resolved = lambda env: (env.get("DEMO_ENDPOINTS", env.get("DEBUG", "0")).strip() == "1")
    assert resolved({}) is False, "hicbir ayar yokken demo uc noktalari acik"
    assert resolved({"DEBUG": "0"}) is False, "DEBUG=0 iken demo uc noktalari acik"
    assert resolved({"DEBUG": "1"}) is True, "yerel demoda kapali kalmamali"
    assert resolved({"DEBUG": "0", "DEMO_ENDPOINTS": "1"}) is True, "acik secim gecersiz"


def test_analyze_withholds_shap_from_the_scored_client():
    """/api/analyze answers the party being assessed. Naming the features that
    convicted them is a tuning signal: submit, read the reason, adjust, repeat.
    The row keeps the explanation and the dashboard reads it behind its key."""
    assert main.SHAP_IN_ANALYZE is False, "SHAP varsayilan olarak istemciye donuyor"

    session_id = "0c0c0c0c-0000-0000-0000-000000000001"
    db = _StubDB(session=_stub_session())
    client = _client(db)
    try:
        res = client.post(
            "/api/analyze",
            json=_analyze_payload(session_id),
            headers={"X-DeepCheck-Token": main.sign_session(session_id)},
        )
        assert res.status_code == 200
        assert res.json()["shap_explanation"] == [], "skorlanan istemciye SHAP sizdi"
        # Still stored, so the SOC dashboard loses nothing.
        assert db.session.shap_explanation, "aciklama satira yazilmamis"
    finally:
        _clear_overrides()


def test_opening_window_is_marked_provisional_not_suspicious():
    """The first seconds of a real session carry almost no signal.

    A window with a couple of pointer samples and a handful of keystrokes
    leaves most of the vector on its neutral fallbacks, and the rest estimated
    from too few samples. benchmark.py measured the consequence: 35% of
    legitimate opening windows scored above the block threshold. The score is
    still computed and stored -- what changes is that it is flagged as
    unsupported, so the badge says "still measuring" instead of accusing a
    customer who has only just arrived.
    """
    base = BASE_T
    sparse = {
        "mouse_trajectory": [{"x": 300 + i * 4, "y": 200 + i * 3, "t": base + i * 30} for i in range(3)],
        "click_timing": [{"x": 312, "y": 209, "t": base + 400}],
        "scroll_events": [],
        "key_events": [{"t": base + 900 + i * 190} for i in range(3)],
        "focus_changes": [],
        "hesitation_intervals": [500.0],
    }
    out = scorer.compute_risk(sparse)
    assert out["provisional"] is True, (
        f"acilis penceresi {out['measured_features']}/12 olcumle kesin sayildi"
    )
    assert out["measured_features"] < scorer.MIN_MEASURED_FOR_CONFIDENT_SCORE

    # An ordinary session is not held back: the flag must not become a blanket
    # excuse that hides every verdict.
    rich = _natural_human_session()
    full = scorer.compute_risk(rich)
    assert full["provisional"] is False, (
        f"tam oturum {full['measured_features']}/12 olcumle geri tutuldu"
    )

    # And it is display only -- a bot whose window is thin is still scored and
    # still stopped by the decision layer, which never reads this flag.
    bot = scorer.compute_risk(_headless_bot_session())
    assert bot["risk_score"] > 40, "provisional bayragi skoru degistirmemeli"


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


def test_isolation_forest_is_not_consulted_when_scoring():
    """The Isolation Forest is stored in the bundle but must never be read on
    the request path.

    It is fitted on human rows only, so "normal" to it means the human
    distribution -- and the automation worth catching here is automation built
    to sit inside that distribution. Measured against held-out real browser
    rows its standalone ROC-AUC was 0.340: not weak, inverted. It was ranking
    the attacker as the more normal party, and its 0.2 share pulled real
    scores toward the wrong answer while costing 14 ms of a 32 ms scoring
    budget.

    Weight zero and a call that still happens is the worst of both: the
    latency without the signal. So this booby-traps decision_function and
    asserts a flush still scores. If someone reintroduces the term, this fails
    with the reason attached rather than quietly making every request slower.
    """
    bundle = scorer.get_bundle()
    original = bundle.iso_forest.decision_function

    def explode(*_args, **_kwargs):
        raise AssertionError(
            "IsolationForest skorlama yolunda cagrildi. Gerekcesi scorer.py'de: "
            "gercek satirlar uzerinde tek basina ROC-AUC 0.340 -- tesadufden de "
            "kotu. Yeniden eklenecekse once olculmeli."
        )

    bundle.iso_forest.decision_function = explode
    try:
        result = scorer.compute_risk(_natural_human_session())
    finally:
        bundle.iso_forest.decision_function = original

    assert 0.0 <= result["risk_score"] <= 100.0

    # And the blend really is the two-model one, to the rounding of the score.
    assert scorer.ENSEMBLE_RF_WEIGHT + scorer.ENSEMBLE_LSTM_WEIGHT == 1.0, (
        "ansambl cekileri 1.0 toplamiyor; skor artik olasilik olarak okunamaz"
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
        test_opening_window_is_marked_provisional_not_suspicious,
        test_lstm_reacts_to_trajectory,
        test_isolation_forest_is_not_consulted_when_scoring,
        test_api_rejects_bad_token,
        test_decision_blocks_bot_session,
        test_decision_fails_closed_without_telemetry,
        test_dashboard_endpoints_require_key,
        test_analyze_rejects_stale_timestamps,
        test_analyze_rejects_backwards_time,
        test_analyze_rejects_replayed_payload,
        test_decision_waits_for_sequential_evidence,
        test_ambiguity_is_never_charged,
        test_cluster_of_identical_sessions_is_escalated,
        test_conformal_guard_only_softens_never_hardens,
        test_decision_verifies_when_stale,
        test_demo_charge_never_charges_blocked_session,
        test_demo_verify_upgrades_verify_but_not_block,
        test_token_requires_proof_of_work_and_browser_timers,
        test_rate_limit_rejects_a_burst,
        test_rate_limiter_memory_is_bounded,
        test_bundle_requires_lstm_weights,
        test_client_signals_recorded_but_not_scored,
        test_demo_endpoints_off_by_default_outside_debug,
        test_analyze_withholds_shap_from_the_scored_client,
        test_training_seeds_torch,
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
