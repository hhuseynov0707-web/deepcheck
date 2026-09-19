"""Tests for demo_seed.py -- the SYNTHETIC demo customers of the jury prototype.

Run:
    pytest test_demo.py -q

The prototype shown to the jury runs on synthetic customers, because the team
has no customer base. That is accepted. What these tests hold the line on is
LABELLING: everything the seed writes is flagged synthetic in the data, the
Demo page lists exactly the customers the seed creates, and no evaluation
script can read any of it. A juror paying as a synthetic customer demonstrates
the mechanism, never an accuracy figure.

The database is the in-memory profile model from test_profiles.py (it
interprets the statements themselves, so the learning path runs unchanged);
the few tests that need the served model bundle say so.
"""

import ast
import asyncio
import copy
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import demo_seed
import profiles
from lstm_model import FEATURE_NAMES
from test_profiles import (  # noqa: F401 -- `api` is a pytest fixture
    _DEVIATING,
    _MATCHING,
    _ProfileTables,
    _audit_rows,
    _customer_session,
    _main,
    _post_charge,
    _post_verify,
    _profile_row,
    _reference_set,
    _scorer_tests,
    _vectors,
    api,
)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BACKEND_DIR)
AYSE = demo_seed.DEMO_CUSTOMERS[0]


def _run(coroutine):
    return asyncio.run(coroutine)


def _fake_history(customer, n_sessions=demo_seed.SESSIONS_PER_MODALITY):
    """A history with the shape build_history returns, without the model: the
    storage tests are about what is written, not about the simulator. Values
    sit around 0.5, where test_profiles' deviating (1.5) and matching (0.5)
    sessions are defined."""
    history = {}
    for stream, modality in enumerate(demo_seed.SEED_MODALITIES):
        vectors = _reference_set(n_sessions, seed=customer.seed % 1000 + stream)
        history[modality] = [
            (
                demo_seed.reference_session_id(customer, modality, index + 1),
                {"vec": vec, "disp": {name: 0.01 for name in FEATURE_NAMES}, "flush_count": 10},
            )
            for index, vec in enumerate(vectors)
        ]
    return history


def _seeded_tables(api, **kwargs):
    tables = _ProfileTables()
    code = _run(demo_seed.seed(api.db(tables=tables), history_builder=_fake_history, log=lambda *_: None, **kwargs))
    # The seed's closing status read opened a read-only transaction; end it,
    # as closing the real session does, so later edits to the committed rows
    # are what the next statement sees.
    tables.rollback()
    return tables, code


def _writes(tables):
    return [s for s in tables.statements if s[0] != "select"]


# --- seeding ---------------------------------------------------------------------


def test_seed_marks_everything_it_writes_as_synthetic(api):
    tables, code = _seeded_tables(api)
    assert code == 0

    profiles_rows = tables.committed["customer_profiles"]
    assert len(profiles_rows) == len(demo_seed.DEMO_CUSTOMERS)
    for customer in demo_seed.DEMO_CUSTOMERS:
        pid = demo_seed.profile_id_for(customer)
        row = _profile_row(tables, pid)
        # Synthetic, in the reserved demo namespace, with the demo basis: no
        # person consented, because there is no person.
        assert row["is_synthetic"] is True and row["is_demo"] is True
        assert row["merchant_id"] == "demo" and row["consent_basis"] == "demo"
        assert row["profiling_enabled"] is True and row["erased_at"] is None
        assert row["escalation_count"] == 0 and row["consecutive_passed_escalations"] == 0
        for modality in demo_seed.SEED_MODALITIES:
            vectors = _vectors(tables, pid, modality=modality)
            # Mature: more than PROFILE_MIN_SESSIONS, within the buffer.
            assert len(vectors) == demo_seed.SESSIONS_PER_MODALITY > profiles.PROFILE_MIN_SESSIONS
            assert all(v["is_synthetic"] is True for v in vectors)
            assert all(v["probation"] is False and v["outcome"] == "pending" for v in vectors)
            assert all(v["session_id"].startswith(f"sentetik-{customer.key}-{modality}-") for v in vectors)
            assert all(v["feature_schema_version"] == profiles.FEATURE_SCHEMA_VERSION for v in vectors)
    # Nothing else was touched: no real session row, no audit row.
    assert tables.committed["sessions"] == [] and tables.committed["decision_audit"] == []
    # The per-day cap was lifted for the seed only.
    assert profiles.PROFILE_LEARN_PER_DAY == 3


def test_seeding_twice_changes_nothing(api):
    tables, _ = _seeded_tables(api)
    before = copy.deepcopy(tables.committed)
    tables.statements.clear()

    code = _run(demo_seed.seed(api.db(tables=tables), history_builder=_fake_history, log=lambda *_: None))

    assert code == 0
    assert _writes(tables) == [], "an intact seed must not be written again"
    assert tables.committed == before


def test_a_changed_customer_is_left_alone_until_reset(api):
    """A juror's session learned into a synthetic profile (on a later day) and
    a spent challenge: the seed reports it and does not guess; --reset deletes
    every synthetic demo customer and seeds them again."""
    tables, _ = _seeded_tables(api)
    pid = demo_seed.profile_id_for(AYSE)
    tables.committed["customer_profile_vectors"].append(
        tables.with_defaults(
            "customer_profile_vectors",
            dict(
                profile_id=pid,
                session_id="gercek-juri-oturumu",
                modality="mouse",
                feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
                vec={name: 0.9 for name in FEATURE_NAMES},
                disp={},
                flush_count=5,
                probation=True,
            ),
        )
    )
    _profile_row(tables, pid).update(escalation_count=2, escalation_window_start=datetime.now(timezone.utc))
    tables.committed["decision_audit"].append(
        tables.with_defaults(
            "decision_audit", dict(session_id="gercek-juri-oturumu", profile_id=pid, is_synthetic=True)
        )
    )
    tables.committed["sessions"].append(
        tables.with_defaults("sessions", dict(id="gercek-juri-oturumu", profile_id=pid))
    )
    changed = copy.deepcopy(tables.committed)

    lines = []
    code = _run(demo_seed.seed(api.db(tables=tables), history_builder=_fake_history, log=lines.append))
    tables.rollback()
    assert code == 1
    assert tables.committed == changed, "without --reset nothing may be overwritten"
    assert any("--reset" in line for line in lines)
    assert any("SENTETİK" in line and "2/3" in line for line in lines)

    code = _run(demo_seed.seed(api.db(tables=tables), reset=True, history_builder=_fake_history, log=lines.append))
    assert code == 0
    row = _profile_row(tables, pid)
    assert row["is_synthetic"] is True and row["escalation_count"] == 0
    assert not _vectors(tables, pid, session_id="gercek-juri-oturumu")
    assert len(_vectors(tables, pid)) == 2 * demo_seed.SESSIONS_PER_MODALITY
    # Unlinked like an erasure; the audit row stays, still marked synthetic.
    [audit] = tables.committed["decision_audit"]
    assert audit["profile_id"] is None and audit["is_synthetic"] is True
    [session] = tables.committed["sessions"]
    assert session["profile_id"] is None


def test_the_seeded_profile_row_is_the_demo_namespace_row(api):
    """Drift guard: the seed creates the row the demo page's first charge
    creates (main._read_profile_context), plus is_synthetic -- not a
    hand-written variant."""
    api.configure(escalation=True)
    implicit = _ProfileTables()
    session_id = str(uuid.uuid4())
    response = _post_charge(api, _customer_session(api, implicit, session_id, value=_MATCHING), session_id, ref=AYSE.ref)
    assert response.status_code == 200, response.text
    by_demo_page = _profile_row(implicit, demo_seed.profile_id_for(AYSE))

    seeded, _ = _seeded_tables(api)
    by_seed = _profile_row(seeded, demo_seed.profile_id_for(AYSE))

    clocks = {"created_at", "updated_at", "last_seen_at", "consent_recorded_at"}
    differing = {k for k in by_seed if k not in clocks and by_seed[k] != by_demo_page[k]}
    assert differing == {"is_synthetic"}
    assert by_seed["is_synthetic"] is True and by_demo_page["is_synthetic"] is False


def test_a_juror_is_compared_against_the_synthetic_history(api):
    """The jury demo, end to end through the HTTP handlers: a session that
    deviates from the synthetic customer's history is asked for extra
    verification -- the generic step_up, the score untouched -- and the code
    lets it through. A session in the history's own pattern is not challenged.
    On the Istanbul day of the seed nothing is learned into the synthetic
    profile: its forty seeded rows fill the per-day learning cap."""
    api.configure(escalation=True)
    tables, _ = _seeded_tables(api)
    pid = demo_seed.profile_id_for(AYSE)
    references_before = copy.deepcopy(_vectors(tables, pid))

    juror = str(uuid.uuid4())
    declined = _post_charge(api, _customer_session(api, tables, juror, value=_DEVIATING), juror, ref=AYSE.ref)
    assert declined.status_code == 200, declined.text
    body = declined.json()
    assert body["status"] == "declined"
    assert body["decision"]["action"] == "verify" and body["decision"]["reason"] == "step_up"
    assert body["decision"]["risk_score"] == 12.0  # unchanged by the layer
    [audit] = _audit_rows(tables, juror)
    assert audit["reason"] == "profile_deviation" and audit["profile_state"] == "evaluated"
    assert audit["modality"] == "mouse" and audit["reference_n"] == demo_seed.SESSIONS_PER_MODALITY
    assert audit["p_value"] <= profiles.PROFILE_ALPHA

    db = _customer_session(api, tables, juror, value=_DEVIATING)
    assert _post_verify(api, db, juror).status_code == 200
    charged = _post_charge(api, db, juror, ref=AYSE.ref)
    assert charged.json()["status"] == "charged"
    assert charged.json()["decision"]["reason"] == "verified"
    assert _vectors(tables, pid) == references_before, "seed day: the synthetic history must not change"

    contrast = str(uuid.uuid4())
    allowed = _post_charge(api, _customer_session(api, tables, contrast, value=_MATCHING), contrast, ref=AYSE.ref)
    assert allowed.json()["status"] == "charged" and allowed.json()["decision"]["reason"] == "score"
    [audit] = _audit_rows(tables, contrast)
    assert audit["profile_state"] == "evaluated" and audit["reason"] == "score"


# --- the contrast case's plumbing -----------------------------------------------------


def test_attestation_is_solved_the_way_the_sdk_solves_it(api):
    """/api/session and /api/session/attest touch no database, so the real
    handlers run here."""
    from fastapi.testclient import TestClient

    client = TestClient(api.main.app)

    def post(path, body, headers=None):
        response = client.post(path, json=body, headers=headers or {})
        return response.status_code, response.json()

    session_id, token = demo_seed.attest(post)
    assert token == api.main.sign_session(session_id)


def test_mark_simulated_flags_the_session_without_moving_its_clock(api):
    tables = _ProfileTables()
    seen = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
    tables.seed("sessions", id="sim-1", last_seen_at=seen)
    tables.seed("sessions", id="baska", last_seen_at=seen)
    tables.seed("decision_audit", session_id="sim-1", reason="score")
    tables.seed(
        "customer_profile_vectors",
        profile_id="p" * 64,
        session_id="sim-1",
        modality="mouse",
        feature_schema_version=1,
        vec={},
        disp={},
        flush_count=5,
    )

    _run(demo_seed.mark_simulated(api.db(tables=tables), "sim-1"))

    sim, other = tables.committed["sessions"]
    assert sim["is_synthetic"] is True and sim["last_seen_at"] == seen
    assert other["is_synthetic"] is False
    assert tables.committed["decision_audit"][0]["is_synthetic"] is True
    assert tables.committed["customer_profile_vectors"][0]["is_synthetic"] is True


# --- the history goes through the served path (needs the model bundle) ------------------


def test_the_history_is_built_through_the_analyze_path():
    """Every flush is validated like an /api/analyze body and scored by
    compute_risk; a simulated session is sorted into the modality it was
    simulated in; the same customer is the same person on every run."""
    _main()
    import scorer
    import train_model

    identity = demo_seed.identity_for(AYSE)
    assert identity == demo_seed.identity_for(AYSE)
    assert identity != demo_seed.identity_for(demo_seed.DEMO_CUSTOMERS[1])

    train_model.rng = np.random.default_rng(7)
    for modality in demo_seed.SEED_MODALITIES:
        windows = train_model.simulate_identity_sessions(identity, 1, modality)[0]
        rows = demo_seed.flush_rows(windows)
        for window, row in zip(windows, rows):
            direct = scorer.compute_risk(window)
            assert row["measured_mask"] == direct["measured_mask"]
            assert {n: row[n] for n in FEATURE_NAMES} == direct["features"]
        got, vector = demo_seed.session_from_rows(rows)
        assert got == modality
        assert vector is None or set(vector["vec"]) == set(FEATURE_NAMES)

    history = demo_seed.build_history(AYSE, n_sessions=2)
    assert history == demo_seed.build_history(AYSE, n_sessions=2), "the history must be reproducible"
    for modality in demo_seed.SEED_MODALITIES:
        assert [sid for sid, _ in history[modality]] == [
            demo_seed.reference_session_id(AYSE, modality, i) for i in (1, 2)
        ]


def test_simulated_flushes_are_rebased_without_changing_what_is_measured():
    main = _main()
    import scorer
    import train_model

    identity = demo_seed.identity_for(AYSE)
    train_model.rng = np.random.default_rng(11)
    windows = train_model.simulate_identity_sessions(identity, 1, "mouse")[0]
    target = 1_790_000_000_000
    for window in windows:
        rebased = demo_seed.rebase_window(window, target)
        raw = demo_seed._jsonable(window)
        assert main._newest_event_ms(rebased) == target
        before, after = scorer.compute_risk(raw), scorer.compute_risk(rebased)
        assert after["features"] == before["features"]
        assert after["measured_mask"] == before["measured_mask"]
        # And the server sees it as the same window, not a new one.
        assert main._payload_fingerprint(rebased) == main._payload_fingerprint(raw)


# --- labelling on the page -------------------------------------------------------------


def test_the_demo_page_lists_exactly_the_seeded_customers():
    with open(os.path.join(REPO_DIR, "frontend", "src", "demoCustomers.js"), encoding="utf-8") as fh:
        source = fh.read()
    listed = re.findall(r'\{\s*ref:\s*"([^"]+)",\s*name:\s*"([^"]+)"\s*\}', source)
    assert listed == [(c.ref, c.name) for c in demo_seed.DEMO_CUSTOMERS]
    count = re.search(r"^export const SYNTHETIC_SESSIONS_PER_MODALITY = (\d+);", source, re.MULTILINE)
    assert count and int(count.group(1)) == demo_seed.SESSIONS_PER_MODALITY
    assert "sentetik geçmiş" in source
    for customer in demo_seed.DEMO_CUSTOMERS:
        assert "sentetik" in customer.ref, "the reference itself must say synthetic"


# --- no measurement reads synthetic data -------------------------------------------------

_EVALUATION_SCRIPTS = (
    "profile_lab.py",
    "evaluate.py",
    "benchmark.py",
    "model_selection.py",
    "train_model.py",
    "record_session.py",
)


def _imports(path):
    tree = ast.parse(open(os.path.join(BACKEND_DIR, path), encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_evaluation_script_can_read_the_demo_customers():
    """The synthetic customers live in the served database. Of the scripts
    that produce a reported number, only profile_lab.py opens a database at
    all -- its own throwaway one, for latency -- and it refuses any other."""
    for script in _EVALUATION_SCRIPTS:
        assert "demo_seed" not in _imports(script), f"{script} imports the demo seed"
    for script in ("evaluate.py", "benchmark.py", "model_selection.py", "train_model.py"):
        imported = _imports(script)
        assert not {"database", "models", "main"} & imported, f"{script} reads the served database"

    import profile_lab

    with pytest.raises(SystemExit):
        profile_lab.measure_latency("postgresql+asyncpg://deepcheck:x@db:5432/deepcheck", 1, 0, "served")

    # Every statement profile_lab runs against profile tables lives in
    # measure_latency, behind that refusal.
    source = open(os.path.join(BACKEND_DIR, "profile_lab.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    latency = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "measure_latency"
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "customer_profile" in node.value:
            assert latency.lineno <= node.lineno <= latency.end_lineno, (
                f"profile_lab.py:{node.lineno} reads profile tables outside measure_latency"
            )


class _RecordStubDB:
    """Just enough AsyncSession for record_session._load()."""

    def __init__(self, session, flushes):
        self.session, self.flushes = session, flushes

    async def get(self, model, key):
        return self.session

    async def execute(self, statement):
        flushes = self.flushes
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(flushes)))


def _stored_flush():
    return SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        risk_score=10.0,
        raw_purged=False,
        client_signals={"pointer_mouse": 30},
        mouse_trajectory=[],
        click_timing=[],
        scroll_rhythm=[],
        hesitation_intervals=[],
        focus_changes=[],
        key_events=[{"t": 1}],
        **{name: 0.5 for name in FEATURE_NAMES},
    )


def _stored_session(is_synthetic):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id="oturum", created_at=now, last_seen_at=now, risk_score=10.0, label="Gerçek Kullanıcı",
        is_synthetic=is_synthetic,
    )


def test_record_session_still_records_a_real_session():
    """The positive half of the next test, so it cannot pass by refusing
    everything."""
    import record_session

    record = _run(record_session._load(_RecordStubDB(_stored_session(False), [_stored_flush()]), "oturum"))
    assert record is not None and len(record["flushes"]) == 1


@pytest.mark.xfail(
    strict=True,
    reason="record_session.py (not owned by the demo step) still files a simulated session; "
    "see open issue: _load() must return None when sessions.is_synthetic is true",
)
def test_record_session_refuses_a_simulated_session():
    """record_session.py is the one gate between the served database and the
    evaluation set (data/real/ and, with --to-training, lab/real_telemetry.json).
    A session driven by demo_seed.py --simulate is flagged is_synthetic; filed
    as "human" it would put simulator output into every later measurement."""
    import record_session

    record = _run(record_session._load(_RecordStubDB(_stored_session(True), [_stored_flush()]), "oturum"))
    assert record is None


# --- the SOC panel labels synthetic data (server change pending) -------------------------


@pytest.mark.xfail(
    strict=True,
    reason="main.py (not owned by the demo step) does not expose the synthetic flags yet; "
    "see open issue: DecisionAudit.is_synthetic, profile.synthetic and is_synthetic on /api/score and /api/sessions",
)
def test_the_soc_panel_is_told_what_is_synthetic(api):
    """The SOC card and session list show "Sentetik demo verisi" from these
    fields (frontend ProfilePanel / SessionTable). A juror is a real person,
    so the session is not synthetic -- but the profile it was compared against
    is, and the decision row must say so for as long as it is kept."""
    from test_profiles import _dashboard_db, _score

    api.configure(escalation=True)
    tables, _ = _seeded_tables(api)
    juror = str(uuid.uuid4())
    _post_charge(api, _customer_session(api, tables, juror, value=_DEVIATING), juror, ref=AYSE.ref)

    [audit] = _audit_rows(tables, juror)
    assert audit["is_synthetic"] is True
    body = _score(api, _dashboard_db(api, tables), juror).json()
    assert body["profile"]["synthetic"] is True
    assert body["is_synthetic"] is False

    simulated = SimpleNamespace(
        id="sim", risk_score=10.0, label="Gerçek Kullanıcı", confidence=0.9, response_time_ms=5.0,
        created_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc), is_synthetic=True,
    )
    rows = api.client(api.db(history=[simulated])).get(
        "/api/sessions", headers={"X-Dashboard-Key": api.main.DASHBOARD_KEY}
    ).json()
    assert rows[0]["is_synthetic"] is True


@pytest.mark.xfail(
    strict=True,
    reason="main.py (not owned by the demo step) still learns real sessions into a synthetic profile; "
    "see open issue: skip learning when the profile is_synthetic",
)
def test_a_synthetic_profile_is_never_taught(api):
    """A synthetic customer is an exhibit: compared against, never taught. A
    juror's approved session must not become one of the "synthetic" profile's
    references (the per-day cap only hides this on the seed day, so the seeded
    rows are moved to an earlier day here)."""
    api.configure(escalation=True)
    tables, _ = _seeded_tables(api)
    for row in tables.committed["customer_profile_vectors"]:
        row["created_at"] = datetime.now(timezone.utc) - timedelta(days=2)
    pid = demo_seed.profile_id_for(AYSE)
    before = copy.deepcopy(_vectors(tables, pid))

    juror = str(uuid.uuid4())
    response = _post_charge(api, _customer_session(api, tables, juror, value=_MATCHING), juror, ref=AYSE.ref)
    assert response.json()["status"] == "charged"
    assert _vectors(tables, pid) == before
