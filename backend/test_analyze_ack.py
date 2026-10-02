"""/api/analyze's acknowledgement-only reply (main.ANALYZE_ACK_REPLY).

The store's nginx adds `X-DeepCheck-Reply: ack` to every SDK call it forwards,
so the payer's browser is told that its window was taken and nothing about how
it scored. Two things must hold, and both are tested here against the real
handler with the stub database test_scorer.py uses:

1. The header changes the REPLY and nothing else: the same window is checked,
   scored and stored identically with and without it. A header that quietly
   skipped the scoring would leave /api/decision judging a session with no
   evidence, i.e. every store payer would be sent to step-up.
2. Without the header, or with any other value, the reply is exactly what it
   was: the lab tools (bot_lab.py, live_bot.py --legacy, demo_seed.py
   --simulate) read the score from it on the core's own port.

Needs model.pkl, like test_scorer.py.
"""

import pytest

# test_scorer first: it sets the environment main reads at import time
# (DEMO_ENDPOINTS among it). pytest collects this file before test_scorer.py,
# so importing main here first froze the demo endpoints off for the whole run
# and every /api/demo/* test in the other files answered 404.
from test_scorer import (
    _analyze_payload,
    _assert_nothing_written,
    _clear_overrides,
    _client,
    _idle_payload,
    _natural_human_session,
    _raw_from_payload,
    _stub_session,
    _StubDB,
)
import main
from lstm_model import FEATURE_NAMES
import scorer

FULL_REPLY_KEYS = set(main.AnalyzeResponse.model_fields)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    # The limiter is process-wide and keyed by session id. These tests send
    # many windows under a handful of ids, and pytest runs this file before
    # test_demo/test_profiles/test_scorer: left full, the buckets answered
    # those files' checkouts with 429 and they failed in the full run while
    # passing alone. Same reset the rate-limit tests in test_scorer.py do.
    main._rate_hits.clear()
    yield
    main._rate_hits.clear()

# Everything a stored behaviour row carries that is a function of the window
# alone. created_at and the row id are the database's, not the handler's.
STORED_ROW_FIELDS = (
    "session_id",
    "mouse_trajectory",
    "click_timing",
    "scroll_rhythm",
    "hesitation_intervals",
    "focus_changes",
    "key_events",
    *FEATURE_NAMES,
    "measured_mask",
    "risk_score",
    "payload_hash",
    "behavior_bucket",
    "newest_event_at",
    "client_signals",
)


def _post(session_id: str, payload: dict, extra_headers: dict | None = None, db: _StubDB | None = None):
    """One /api/analyze call against a fresh stub database. (response, db)."""
    db = db if db is not None else _StubDB(session=_stub_session())
    client = _client(db)
    headers = {"X-DeepCheck-Token": main.sign_session(session_id), **(extra_headers or {})}
    try:
        res = client.post("/api/analyze", json=payload, headers=headers)
    finally:
        _clear_overrides()
    return res, db


def _stored_rows(db: _StubDB) -> list:
    return [obj for obj in db.added if isinstance(obj, main.BehaviorData)]


@pytest.mark.parametrize("suffix, name", [
    ("01", "X-DeepCheck-Reply"),
    ("02", "x-deepcheck-reply"),
    ("03", "X-DEEPCHECK-REPLY"),
])
def test_ack_reply_says_accepted_and_nothing_else(suffix, name):
    """Not the score, the label, the confidence, the measured-feature count,
    the provisional flag, the SHAP list or the timing: each one tells the
    scored party something about how its last window landed."""
    session_id = "0a0c0000-0000-0000-0000-0000000000" + suffix
    res, db = _post(session_id, _analyze_payload(session_id), {name: "ack"})

    assert res.status_code == 200, res.text
    assert res.json() == {"session_id": session_id, "accepted": True}, (
        f"onay yaniti skorlanan tarafa fazladan alan dondurdu: {sorted(res.json())}"
    )
    # Accepted means taken: the window was stored.
    assert db.committed is True
    assert len(_stored_rows(db)) == 1


def test_ack_reply_stores_and_scores_exactly_as_the_full_reply():
    """The same window, posted once with the header and once without, into
    two empty databases: the stored row and the session it updates must be
    the same, and both must be the scorer's own verdict."""
    session_id = "0a0c0000-0000-0000-0000-000000000010"
    payload = _analyze_payload(session_id, _natural_human_session(seed=3))
    expected = scorer.compute_risk(_raw_from_payload(payload))

    full_res, full_db = _post(session_id, payload)
    ack_res, ack_db = _post(session_id, payload, {"X-DeepCheck-Reply": "ack"})

    assert full_res.status_code == 200 and ack_res.status_code == 200, (full_res.text, ack_res.text)
    assert set(full_res.json()) == FULL_REPLY_KEYS
    assert set(ack_res.json()) == {"session_id", "accepted"}

    full_rows, ack_rows = _stored_rows(full_db), _stored_rows(ack_db)
    assert len(full_rows) == 1 and len(ack_rows) == 1, "pencere iki yoldan birinde kaydedilmedi"
    for field in STORED_ROW_FIELDS:
        assert getattr(ack_rows[0], field) == getattr(full_rows[0], field), (
            f"onay basligi kaydedilen satirin '{field}' alanini degistirdi"
        )
    assert ack_rows[0].risk_score == expected["risk_score"]
    assert ack_rows[0].measured_mask == expected["measured_mask"]

    # The session row /api/decision and the SOC read: same smoothed score,
    # label, confidence and explanation. The timing is measured per call, so
    # only its presence is compared.
    for field in ("risk_score", "label", "confidence", "shap_explanation"):
        assert getattr(ack_db.session, field) == getattr(full_db.session, field), (
            f"onay basligi oturumun '{field}' alanini degistirdi"
        )
    assert ack_db.session.risk_score == full_res.json()["risk_score"]
    assert ack_db.session.shap_explanation, "aciklama SOC icin satira yazilmamis"
    assert isinstance(ack_db.session.response_time_ms, (int, float)) and ack_db.session.response_time_ms > 0
    assert ack_db.committed is True


def test_no_header_keeps_the_full_reply():
    session_id = "0a0c0000-0000-0000-0000-000000000020"
    res, db = _post(session_id, _analyze_payload(session_id))

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == FULL_REPLY_KEYS, f"tam yanit degisti: {sorted(body)}"
    assert body["session_id"] == session_id
    assert body["risk_score"] == db.session.risk_score
    assert body["label"] == db.session.label
    assert "accepted" not in body


@pytest.mark.parametrize("suffix, value", [("31", "full"), ("32", "ACK"), ("33", "ack, full"), ("34", ""), ("35", "1")])
def test_any_other_value_keeps_the_full_reply(suffix, value):
    """Exact match only: nginx sends exactly "ack". A near miss is a
    configuration mistake, and it must not be read as a third reply shape."""
    session_id = "0a0c0000-0000-0000-0000-0000000000" + suffix
    res, _ = _post(session_id, _analyze_payload(session_id), {"X-DeepCheck-Reply": value})

    assert res.status_code == 200, res.text
    assert set(res.json()) == FULL_REPLY_KEYS, f"'{value}' degeri yaniti degistirdi: {sorted(res.json())}"


def test_ack_for_a_behaviourless_flush_reads_and_writes_nothing():
    """The full reply to an idle window shows the score the session already
    stored. The acknowledgement has nothing to show, so it skips that read
    too -- and, as before, writes nothing."""
    session_id = "0a0c0000-0000-0000-0000-000000000040"

    class _NoReadDB(_StubDB):
        async def get(self, model, primary_key):
            raise AssertionError("onay yaniti icin oturum okundu")

    db = _NoReadDB(session=_stub_session(87.0))
    res, db = _post(session_id, _idle_payload(session_id), {"X-DeepCheck-Reply": "ack"}, db=db)

    assert res.status_code == 200, res.text
    assert res.json() == {"session_id": session_id, "accepted": True}
    _assert_nothing_written(db, "onayli bosta akis")


def test_a_refused_window_is_never_acknowledged():
    """`accepted` is only ever sent for a window the core took. A replayed
    window and a missing token keep their error statuses under the header."""
    session_id = "0a0c0000-0000-0000-0000-000000000050"
    payload = _analyze_payload(session_id)
    replayed = _StubDB(
        session=_stub_session(),
        known_hashes=[main._payload_fingerprint(_raw_from_payload(payload))],
    )
    res, db = _post(session_id, payload, {"X-DeepCheck-Reply": "ack"}, db=replayed)
    assert res.status_code == 422, res.text
    assert "accepted" not in res.json()
    assert _stored_rows(db) == []

    client = _client(_StubDB(session=_stub_session()))
    try:
        res = client.post("/api/analyze", json=payload, headers={"X-DeepCheck-Reply": "ack"})
    finally:
        _clear_overrides()
    assert res.status_code == 401, res.text
    assert "accepted" not in res.json()


def test_openapi_documents_both_reply_shapes():
    """One response_model that admits both bodies, so the schema says what a
    client can actually receive."""
    main.app.openapi_schema = None
    try:
        spec = main.app.openapi()
    finally:
        main.app.openapi_schema = None
    schema = spec["paths"]["/api/analyze"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    refs = {option.get("$ref", "").rsplit("/", 1)[-1] for option in schema.get("anyOf", [])}
    assert refs == {"AnalyzeResponse", "AnalyzeAck"}, f"200 semasi: {schema}"
    ack = spec["components"]["schemas"]["AnalyzeAck"]
    assert set(ack["properties"]) == {"session_id", "accepted"}
    assert ack.get("additionalProperties") is False
    header_params = {
        p["name"] for p in spec["paths"]["/api/analyze"]["post"].get("parameters", []) if p["in"] == "header"
    }
    assert "x-deepcheck-reply" in header_params
