"""SYNTHETIC demo customers for the jury prototype: seeded, labelled, and never
measured.

    docker compose exec backend python demo_seed.py              # seed (idempotent)
    docker compose exec backend python demo_seed.py --status     # what is stored now
    docker compose exec backend python demo_seed.py --reset      # delete and seed again
    docker compose exec backend python demo_seed.py --simulate ayse
    docker compose exec backend python demo_seed.py --simulate ayse --modality keyboard

WHY THIS EXISTS. The per-customer profile layer (profiles.py) says nothing about a
customer until it holds PROFILE_MIN_SESSIONS (19) of their sessions in one input
type, and the team has no customer base to take them from. So the prototype shown
to the jury runs on SYNTHETIC customers: each one is a simulator identity
(train_model.simulate_identity_sessions), given a history of
PROFILE_BUFFER_MAX (20) sessions in the mouse modality and 20 in the keyboard
modality, in the reserved "demo" merchant namespace (spec 5.7) that no real
merchant can claim. Nothing here is a person, and everything written says so:
customer_profiles.is_synthetic, customer_profile_vectors.is_synthetic, the
references' session ids ("sentetik-..."), the customer references themselves,
and the Turkish labels on the Demo page and the SOC panel. No measurement reads
any of it: profile_lab.py never opens the served database, and test_demo.py
fails if an evaluation script starts to.

WHAT A STEP-UP ON THESE CUSTOMERS DEMONSTRATES. A juror who pays as "Ayşe" is a
real person compared with a simulator identity, and a real person differs from
every simulator identity by construction -- so the step-up that follows shows the
MECHANISM (a history-backed deviation asking for extra verification, never a
block, never a score change), not the layer's accuracy on real people. That
accuracy has not been measured, because it needs real customers.
The contrast case, --simulate, runs a NEW session drawn from the SAME synthetic
identity through the real HTTP path and is expected not to be challenged; on
synthetic identities the same-person challenge rate was measured at 4.9% (full
conformal, docs/profile-evaluation.md), which is a lower bound for real people.

HOW THE HISTORY IS BUILT -- the same path a live session takes:
  1. train_model.simulate_identity_sessions() draws a session: ten flush
     payloads, the unit the SDK posts;
  2. each flush goes through main.AnalyzeRequest validation and
     scorer.compute_risk(), exactly as /api/analyze does, and becomes the row
     /api/analyze would store (the twelve features, measured_mask, and the
     client_signals the SDK would report for that pointer mix);
  3. profiles.session_modality() and profiles.session_vector() turn the newest
     main.SPRT_MAX_FLUSHES rows into one session vector, as
     main._read_profile_context() does at decision time;
  4. main._learn_session() stores it -- the learning path itself, with its
     learn-once insert and its storage caps -- and the vector is then flagged
     is_synthetic.
The one departure is named where it happens (_learning_cap_lifted): the per-day
learning cap is lifted for this process while it seeds.

The profile row is created with the values the demo namespace uses when the Demo
page names a customer for the first time (main._read_profile_context): consent
basis "demo", is_demo -- plus is_synthetic. No person consented to anything,
because there is no person.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np  # noqa: E402
from sqlalchemy import delete, select, update  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

import profiles  # noqa: E402
from lstm_model import FEATURE_NAMES  # noqa: E402
from models import CustomerProfile, CustomerProfileVector, DecisionAudit, Session  # noqa: E402


@dataclass(frozen=True)
class SyntheticCustomer:
    key: str  # the --simulate argument
    name: str  # Turkish display name; the Demo page and every label add "sentetik"
    ref: str  # customer_ref in the demo namespace; says "sentetik" itself
    seed: int  # the identity: numpy default_rng(seed) -> train_model._identity_traits()


# frontend/src/demoCustomers.js carries a copy for the Demo page's selector;
# test_demo.py pins the two together. The seeds are arbitrary and fixed: an
# identity is whatever _identity_traits() draws from them, not a chosen person.
DEMO_CUSTOMERS = (
    SyntheticCustomer("ayse", "Ayşe", "sentetik-ayse", 20260919),
    SyntheticCustomer("mehmet", "Mehmet", "sentetik-mehmet", 20260920),
    SyntheticCustomer("zeynep", "Zeynep", "sentetik-zeynep", 20260921),
)

# The simulator's modalities (train_model.IDENTITY_MODALITIES, restated so this
# module imports without torch-heavy train_model): it has no model of a finger,
# so there is no synthetic touch history, and a juror on a phone meets an
# immature touch profile and is not compared at all.
SEED_MODALITIES = ("mouse", "keyboard")

# One full reference buffer per modality: more than PROFILE_MIN_SESSIONS, so the
# profile is mature, and not more than PROFILE_BUFFER_MAX, so the learning
# path's cap evicts nothing while seeding.
SESSIONS_PER_MODALITY = profiles.PROFILE_BUFFER_MAX
assert profiles.PROFILE_MIN_SESSIONS < SESSIONS_PER_MODALITY <= profiles.PROFILE_BUFFER_MAX

# A simulated session whose vector cannot be built (fewer than
# PROFILE_MIN_FLUSHES non-provisional flushes) is skipped, as the decision path
# would skip learning it. profile_lab.py measured how many draws a synthetic
# identity needs to reach 19 vectors; this ceiling only stops a broken
# generator from looping forever.
MAX_DRAWS_PER_MODALITY = 4 * SESSIONS_PER_MODALITY

# Separate numpy streams, so the identity, each modality's history and a
# simulated session never share draws: the identity must come out the same on
# every run, whatever else was simulated before it.
_HISTORY_STREAM = {"mouse": 1, "keyboard": 2}

# What the demo page charges (Demo.jsx ORDER: 1699.00 + 20% KDV).
DEMO_AMOUNT = 2038.80

# Chromium's documented clock clamp and the setTimeout(0) lag main.py measured
# on it (main.MIN_CLOCK_RESOLUTION_US / MIN_TIMER_LAG_MS comments). The
# simulator is not a browser and does not pretend otherwise: these are declared
# values, exactly the fabrication main.py's attestation comment says a client
# that reads the source can make. Attestation closes the post-JSON-directly
# path for clients that do not; it is not what stands between a simulator and
# the API, and this tool is the project's own.
SIMULATED_RUNTIME = {"clock_resolution_us": 100.0, "timer_lag_ms": 0.1}

# How far behind the send time the newest event of a simulated flush sits.
# Inside main.MAX_FUTURE_EVENT_MS / MAX_CLOCK_SKEW_MS by a wide margin.
NEWEST_EVENT_LAG_MS = 50

SIMULATED_LABEL = "SİMÜLE EDİLMİŞ OTURUM — gerçek bir kişi değil"

# Measured on synthetic identities, full conformal, docs/profile-evaluation.md
# (and docs/juri-cevaplari.md): how often a session of the SAME synthetic person
# is challenged. Printed by --simulate so an occasional challenge of the
# contrast case is read as the rate it is, not as a malfunction.
SAME_PERSON_CHALLENGE_RATE = "%4,9"


# --- lazy imports ----------------------------------------------------------------
#
# main (FastAPI app, configuration), scorer (the model bundle) and train_model
# (the simulator) are imported on first use: the roster above must stay readable
# by a test that loads no model at all.


def _main():
    import main

    return main


def _scorer():
    import scorer

    return scorer


def _train_model():
    import train_model

    return train_model


def profile_id_for(customer: SyntheticCustomer) -> str:
    main = _main()
    if main.PROFILE_KEY is None:
        raise SystemExit(
            "DEEPCHECK_PROFILE_KEY tanimli degil: sentetik demo musterileri olusturulamaz. "
            ".env.example icindeki demo bolumune bakin."
        )
    return profiles.derive_profile_id(main.DEMO_MERCHANT_ID, customer.ref, main.PROFILE_KEY)


def reference_session_id(customer: SyntheticCustomer, modality: str, index: int) -> str:
    """The session id a seeded reference is stored under. No such session ever
    existed; the "sentetik-" prefix says so wherever the id is shown (the review
    endpoint lists reference session ids)."""
    return f"sentetik-{customer.key}-{modality}-{index:02d}"


def customer_by_key(key: str) -> SyntheticCustomer:
    for customer in DEMO_CUSTOMERS:
        if customer.key == key:
            return customer
    names = ", ".join(c.key for c in DEMO_CUSTOMERS)
    raise SystemExit(f"Bilinmeyen sentetik musteri {key!r}. Secenekler: {names}")


# --- the history: simulator -> the /api/analyze row -> a session vector -------------


def identity_for(customer: SyntheticCustomer) -> dict:
    """The synthetic person: the first draw of default_rng(customer.seed). Same
    dict on every call, so --simulate reproduces the identity the seed stored."""
    tm = _train_model()
    tm.rng = np.random.default_rng(customer.seed)
    return tm._identity_traits()


def _jsonable(value):
    """What the payload looks like after the wire: numpy scalars become plain
    numbers, exactly as json.dumps on the SDK side would have produced them."""

    def default(obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        raise TypeError(type(obj).__name__)

    return json.loads(json.dumps(value, default=default))


def analyze_raw(window: dict) -> dict:
    """The dict /api/analyze hands scorer.compute_risk: the payload validated by
    main.AnalyzeRequest, then rebuilt the way main.analyze rebuilds it. Keep the
    two in step -- test_demo.py compares the features both give."""
    main = _main()
    payload = main.AnalyzeRequest.model_validate({"session_id": "sentetik", **_jsonable(window)})
    return {
        "mouse_trajectory": [p.model_dump() for p in payload.mouse_trajectory],
        "click_timing": [c.model_dump() for c in payload.click_timing],
        "scroll_events": [s.model_dump() for s in payload.scroll_events],
        "hesitation_intervals": payload.hesitation_intervals,
        "focus_changes": payload.focus_changes,
        "key_events": [k.model_dump() for k in payload.key_events],
    }


def client_signals(pointer_events: int) -> dict:
    """What the SDK reports about the pointer mix. Its counters are cumulative
    over the session (sdk/deepcheck.js noteProvenance), and the simulator draws a
    mouse trajectory only in the mouse modality, so "pointer events so far"
    reproduces what profiles.session_modality() reads. Nothing else in here is
    claimed: no untrusted events, no webdriver."""
    main = _main()
    return main.ClientSignals(pointer_mouse=min(pointer_events, 100_000)).model_dump()


def flush_rows(windows: list[dict]) -> list[dict]:
    """One behavior_data row per flush, oldest first, as /api/analyze stores it:
    compute_risk's twelve features and measured_mask, plus client_signals."""
    scorer = _scorer()
    rows = []
    pointer_events = 0
    for window in windows:
        raw = analyze_raw(window)
        result = scorer.compute_risk(raw)
        pointer_events += len(raw["mouse_trajectory"]) + len(raw["click_timing"])
        rows.append(
            {
                "measured_mask": result["measured_mask"],
                "client_signals": client_signals(pointer_events),
                **{name: result["features"][name] for name in FEATURE_NAMES},
            }
        )
    return rows


def session_from_rows(rows: list[dict]) -> tuple[str, dict | None]:
    """(modality, session vector or None) from a session's rows, oldest first --
    what main._read_profile_context computes from the newest SPRT_MAX_FLUSHES
    rows it reads back, newest first."""
    main = _main()
    newest_first = list(reversed(rows))[: main.SPRT_MAX_FLUSHES]
    modality = profiles.session_modality([row["client_signals"] for row in newest_first])
    return modality, profiles.session_vector(newest_first)


def build_history(customer: SyntheticCustomer, n_sessions: int = SESSIONS_PER_MODALITY) -> dict:
    """{modality: [(session_id, vector), ...]} with n_sessions vectors each.

    Deterministic: the same customer gives the same history on every machine
    with the same numpy and model bundle. Takes a while -- every flush is scored
    by compute_risk, like a live one."""
    tm = _train_model()
    history = {}
    for modality in SEED_MODALITIES:
        identity = identity_for(customer)
        tm.rng = np.random.default_rng([customer.seed, _HISTORY_STREAM[modality]])
        vectors = []
        draws = 0
        while len(vectors) < n_sessions:
            if draws >= MAX_DRAWS_PER_MODALITY:
                raise RuntimeError(
                    f"{customer.name} ({modality}): {draws} oturumdan yalnizca {len(vectors)} vektor "
                    "cikti; simulator ya da model paketi beklenmedik durumda."
                )
            windows = tm.simulate_identity_sessions(identity, 1, modality)[0]
            draws += 1
            got_modality, vector = session_from_rows(flush_rows(windows))
            if got_modality != modality:
                raise RuntimeError(
                    f"{customer.name}: {modality} oturumu {got_modality} olarak siniflandi; "
                    "istemci sinyalleri SDK ile uyusmuyor."
                )
            if vector is None:
                continue
            vectors.append((reference_session_id(customer, modality, len(vectors) + 1), vector))
        history[modality] = vectors
    return history


# --- storage -----------------------------------------------------------------------


@contextmanager
def _learning_cap_lifted():
    """The per-day learning cap (profiles.PROFILE_LEARN_PER_DAY, 3 per Istanbul
    day), lifted for THIS process while it seeds -- the one departure from the
    learning path, and the reason it is not hidden in a flag.

    The cap bounds how fast a live stream of sessions can reshape a customer's
    profile, which is an attacker widening it one approved checkout at a time.
    A seed is not a session stream and has no one to bound: it writes a fixed
    synthetic exhibit in one transaction. The server's own processes are
    untouched (the value is restored on exit), and the seeded rows DO count
    towards the cap afterwards: the server learns nothing more into a synthetic
    profile on the Istanbul day it was seeded."""
    saved = profiles.PROFILE_LEARN_PER_DAY
    profiles.PROFILE_LEARN_PER_DAY = math.inf
    try:
        yield
    finally:
        profiles.PROFILE_LEARN_PER_DAY = saved


async def _create_profile(db, customer: SyntheticCustomer, profile_id: str) -> None:
    """The row the demo namespace creates when the Demo page names a customer
    for the first time (main._read_profile_context), plus is_synthetic."""
    main = _main()
    now = main.utcnow()
    await db.execute(
        pg_insert(CustomerProfile)
        .values(
            profile_id=profile_id,
            merchant_id=main.DEMO_MERCHANT_ID,
            is_demo=True,
            is_synthetic=True,
            profiling_enabled=True,
            consent_basis="demo",
            consent_recorded_at=now,
            key_version=main.PROFILE_KEY_VERSION,
            feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
            last_seen_at=now,
        )
        .on_conflict_do_nothing(index_elements=["profile_id"])
    )


async def store_customer(db, customer: SyntheticCustomer, history: dict) -> int:
    """Create the profile and learn every seeded vector through
    main._learn_session, in one transaction. Returns the vectors stored."""
    main = _main()
    profile_id = profile_id_for(customer)
    await _create_profile(db, customer, profile_id)
    stored = []
    with _learning_cap_lifted():
        for modality in SEED_MODALITIES:
            for session_id, vector in history[modality]:
                ctx = main.ProfileContext(
                    merchant_id=main.DEMO_MERCHANT_ID,
                    verdict=profiles.ProfileVerdict(state=profiles.STATE_IMMATURE, modality=modality),
                    profile_id=profile_id,
                    learnable=True,
                    modality=modality,
                    vector=vector,
                )
                if not await main._learn_session(db, session_id, ctx, probation=False):
                    raise RuntimeError(
                        f"{customer.name}: {session_id} ogrenilemedi (profil satiri beklenen durumda degil)."
                    )
                stored.append(session_id)
    await db.execute(
        update(CustomerProfileVector)
        .where(CustomerProfileVector.profile_id == profile_id)
        .where(CustomerProfileVector.session_id.in_(stored))
        .values(is_synthetic=True)
    )
    return len(stored)


async def delete_profiles(db, profile_ids: list[str]) -> None:
    """Remove synthetic demo customers the way an erasure does
    (main.profile_erase, mode "erase"): lock the rows, delete the vectors --
    including any a juror's session added -- unlink sessions and decision audit
    rows, delete the profiles. The audit rows themselves stay: they are the
    record of decisions that were made, and they keep their is_synthetic."""
    main = _main()
    if not profile_ids:
        return
    await db.execute(
        select(CustomerProfile.profile_id).where(CustomerProfile.profile_id.in_(profile_ids)).with_for_update()
    )
    await db.execute(delete(CustomerProfileVector).where(CustomerProfileVector.profile_id.in_(profile_ids)))
    await db.execute(main._unlink_sessions_from_profiles(Session.profile_id.in_(profile_ids)))
    await db.execute(
        update(DecisionAudit).where(DecisionAudit.profile_id.in_(profile_ids)).values(profile_id=None)
    )
    await db.execute(delete(CustomerProfile).where(CustomerProfile.profile_id.in_(profile_ids)))


# --- status ------------------------------------------------------------------------


@dataclass
class CustomerStatus:
    customer: SyntheticCustomer
    exists: bool = False
    synthetic_flag: bool = False
    # modality -> seeded references stored (synthetic, not probation)
    seeded: dict | None = None
    # vectors NOT written by the seed: a juror's session learned into the profile
    foreign_vectors: int = 0
    escalations_used: int = 0
    intact: bool = False
    problems: list | None = None


async def inspect_customer(db, customer: SyntheticCustomer) -> CustomerStatus:
    main = _main()
    profile_id = profile_id_for(customer)
    status = CustomerStatus(customer=customer, seeded={m: 0 for m in SEED_MODALITIES}, problems=[])
    row = (
        await db.execute(
            select(
                CustomerProfile.is_synthetic,
                CustomerProfile.is_demo,
                CustomerProfile.merchant_id,
                CustomerProfile.consent_basis,
                CustomerProfile.profiling_enabled,
                CustomerProfile.erased_at,
                CustomerProfile.feature_schema_version,
                CustomerProfile.key_version,
                CustomerProfile.escalation_count,
                CustomerProfile.escalation_window_start,
            ).where(CustomerProfile.profile_id == profile_id)
        )
    ).first()
    if row is None:
        status.problems.append("profil yok")
        return status
    (is_synthetic, is_demo, merchant_id, basis, enabled, erased_at, schema, key_version, count, window) = row
    status.exists = True
    status.synthetic_flag = bool(is_synthetic)
    now = main.utcnow()
    window = main._as_utc(window)
    status.escalations_used = 0 if profiles.budget_window_expired(window, now) else int(count or 0)

    expected = {
        m: {reference_session_id(customer, m, i) for i in range(1, SESSIONS_PER_MODALITY + 1)}
        for m in SEED_MODALITIES
    }
    for session_id, modality, synthetic, probation, outcome, vector_schema in (
        await db.execute(
            select(
                CustomerProfileVector.session_id,
                CustomerProfileVector.modality,
                CustomerProfileVector.is_synthetic,
                CustomerProfileVector.probation,
                CustomerProfileVector.outcome,
                CustomerProfileVector.feature_schema_version,
            ).where(CustomerProfileVector.profile_id == profile_id)
        )
    ).all():
        if (
            synthetic
            and not probation
            and outcome != "disputed"
            and vector_schema == profiles.FEATURE_SCHEMA_VERSION
            and session_id in expected.get(modality, ())
        ):
            status.seeded[modality] += 1
        else:
            status.foreign_vectors += 1

    if not is_synthetic:
        status.problems.append("profil sentetik olarak isaretli degil")
    if not is_demo or merchant_id != main.DEMO_MERCHANT_ID:
        status.problems.append("profil demo ad alaninda degil")
    if basis != "demo" or not enabled or erased_at is not None:
        status.problems.append("profil etkin degil")
    if schema != profiles.FEATURE_SCHEMA_VERSION or key_version != main.PROFILE_KEY_VERSION:
        status.problems.append("ozellik semasi ya da anahtar surumu eski")
    for modality in SEED_MODALITIES:
        if status.seeded[modality] != SESSIONS_PER_MODALITY:
            status.problems.append(f"{modality}: {status.seeded[modality]}/{SESSIONS_PER_MODALITY} sentetik referans")
    if status.foreign_vectors:
        status.problems.append(f"{status.foreign_vectors} vektor tohumdan gelmiyor (gercek bir oturum ogrenilmis)")
    status.intact = not status.problems
    return status


def describe(status: CustomerStatus) -> str:
    c = status.customer
    head = f"{c.name} ({c.ref}) — SENTETİK demo müşterisi"
    if not status.exists:
        return f"{head}: yok"
    seeded = ", ".join(
        f"{'fare' if m == 'mouse' else 'klavye'} {status.seeded[m]}/{SESSIONS_PER_MODALITY}"
        for m in SEED_MODALITIES
    )
    budget = f"sorgulama bütçesi {status.escalations_used}/{profiles.PROFILE_MAX_ESCALATIONS} kullanıldı"
    state = "hazır" if status.intact else "DEĞİŞMİŞ: " + "; ".join(status.problems)
    return f"{head}: {seeded} sentetik referans oturumu; {budget}; {state}"


# --- seeding -----------------------------------------------------------------------


async def seed(db, *, reset: bool = False, customers=DEMO_CUSTOMERS, history_builder=build_history, log=print) -> int:
    """Seed every customer. Returns 0, or 1 when a customer was left alone
    because its stored state is not what the seed wrote (use --reset).

    Idempotent: an intact customer is not touched, so running this twice
    changes nothing. --reset deletes every synthetic demo customer (the roster,
    and any profile flagged is_synthetic that the roster no longer names) and
    seeds them again -- which also returns each challenge budget to zero."""
    exit_code = 0
    if reset:
        roster = [profile_id_for(c) for c in customers]
        flagged = [
            pid
            for (pid,) in (
                await db.execute(select(CustomerProfile.profile_id).where(CustomerProfile.is_synthetic.is_(True)))
            ).all()
        ]
        doomed = sorted(set(roster) | set(flagged))
        await delete_profiles(db, doomed)
        await db.commit()
        log(f"Sifirlama: {len(doomed)} sentetik demo profili silindi.")

    for customer in customers:
        status = await inspect_customer(db, customer)
        if status.intact:
            log(describe(status) + " (dokunulmadi)")
            continue
        if status.exists:
            log(describe(status))
            log(f"  -> {customer.name} olduğu gibi bırakıldı; yeniden oluşturmak için: python demo_seed.py --reset")
            exit_code = 1
            continue
        started = time.perf_counter()
        log(f"{customer.name}: sentetik geçmiş simülatörden üretiliyor (her akış compute_risk ile skorlanır)...")
        history = history_builder(customer)
        stored = await store_customer(db, customer, history)
        await db.commit()
        log(f"  {stored} sentetik vektör kaydedildi ({time.perf_counter() - started:.0f} sn).")
        log(describe(await inspect_customer(db, customer)))
    return exit_code


async def print_status(db, *, customers=DEMO_CUSTOMERS, log=print) -> int:
    code = 0
    for customer in customers:
        status = await inspect_customer(db, customer)
        log(describe(status))
        code |= 0 if status.intact else 1
    return code


def configuration_warnings() -> list[str]:
    """What stands between a seeded customer and a visible step-up."""
    main = _main()
    warnings = []
    if not main.PROFILE_ENABLED:
        warnings.append(
            "Musteri profili katmani KAPALI (PROFILE_LAYER=1, DEEPCHECK_PROFILE_KEY ve en az bir "
            "DEEPCHECK_MERCHANT_KEYS kaydi gerekir): sentetik musterilerle karsilastirma yapilmaz."
        )
    elif not main.PROFILE_ESCALATION:
        warnings.append(
            "PROFILE_ESCALATION=0: golge modu. SOC panosu sapmayi gosterir, fakat ek dogrulama istenmez."
        )
    if not main.DEMO_ENDPOINTS_ENABLED:
        warnings.append("DEMO_ENDPOINTS kapali: demo sayfasi /api/demo/charge'a ulasamaz (404).")
    return warnings


# --- the contrast case: a simulated session of the same identity ---------------------


def solve_proof_of_work(challenge: str, difficulty_bits: int) -> str:
    """The nonce sdk/deepcheck.js searches for: SHA-256("<challenge>.<nonce>")
    with difficulty_bits leading zero bits (main._check_proof_of_work)."""
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}.{nonce}".encode()).digest()
        bits = 0
        for byte in digest:
            if byte == 0:
                bits += 8
                continue
            bits += 8 - byte.bit_length()
            break
        if bits >= difficulty_bits:
            return str(nonce)
        nonce += 1


def rebase_window(window: dict, newest_at_ms: int) -> dict:
    """The same flush with every timestamp shifted so its newest event sits at
    newest_at_ms, i.e. just before it is sent -- what a live SDK flush looks
    like to main.py's replay checks. A uniform shift changes no feature (every
    feature reads gaps, and click density is measured back from the flush's own
    newest click), which test_demo.py checks against compute_risk."""
    window = _jsonable(window)
    stamps = [e["t"] for key in ("mouse_trajectory", "click_timing", "scroll_events", "key_events") for e in window[key]]
    stamps.extend(window["focus_changes"])
    if not stamps:
        return window
    shift = int(newest_at_ms - max(stamps))
    for key in ("mouse_trajectory", "click_timing", "scroll_events", "key_events"):
        for event in window[key]:
            event["t"] = int(event["t"] + shift)
    window["focus_changes"] = [f + shift for f in window["focus_changes"]]
    return window


def http_poster(api_url: str):
    """POST JSON to the running API. Returns (status, body)."""
    base = api_url.rstrip("/")

    def post(path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw) if raw else {}
            except ValueError:
                return exc.code, {"detail": raw.decode("utf-8", "replace")}

    return post


def attest(post) -> tuple[str, str]:
    """A session id and its token, the way the SDK obtains them."""
    status, minted = post("/api/session", {})
    if status != 201:
        raise SystemExit(f"/api/session {status}: {minted.get('detail')}")
    nonce = solve_proof_of_work(minted["challenge"], int(minted["difficulty_bits"]))
    status, attested = post(
        "/api/session/attest",
        {
            "session_id": minted["session_id"],
            "challenge": minted["challenge"],
            "nonce": nonce,
            "runtime": SIMULATED_RUNTIME,
        },
    )
    if status != 201:
        raise SystemExit(f"/api/session/attest {status}: {attested.get('detail')}")
    return minted["session_id"], attested["token"]


async def mark_simulated(db, session_id: str) -> None:
    """Flag everything the simulated session left behind as synthetic: its
    session row, the decision audit rows about it, and its vector if the
    decision path learned it. last_seen_at is assigned to itself so the
    session's freshness clock does not move (main._unlink_sessions_from_profiles
    explains the onupdate trap)."""
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(is_synthetic=True, last_seen_at=Session.last_seen_at)
    )
    await db.execute(update(DecisionAudit).where(DecisionAudit.session_id == session_id).values(is_synthetic=True))
    await db.execute(
        update(CustomerProfileVector)
        .where(CustomerProfileVector.session_id == session_id)
        .values(is_synthetic=True)
    )
    await db.commit()


async def latest_audit(db, session_id: str) -> dict | None:
    columns = (
        DecisionAudit.action,
        DecisionAudit.reason,
        DecisionAudit.public_reason,
        DecisionAudit.profile_state,
        DecisionAudit.modality,
        DecisionAudit.reference_n,
        DecisionAudit.p_value,
        DecisionAudit.top_features,
        DecisionAudit.shadow,
    )
    row = (
        await db.execute(
            select(*columns)
            .where(DecisionAudit.session_id == session_id)
            .order_by(DecisionAudit.decided_at.desc(), DecisionAudit.id.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else {column.key: value for column, value in zip(columns, row)}


async def simulate(
    db_factory,
    customer: SyntheticCustomer,
    *,
    post,
    modality: str = "mouse",
    sim_seed: int | None = None,
    interval_s: float | None = None,
    log=print,
) -> dict:
    """One NEW session of the customer's own synthetic identity through the real
    HTTP path: /api/session, attestation with a solved proof of work, ten
    /api/analyze flushes paced like the SDK, then /api/demo/charge naming the
    customer. Prints the decision, labelled as a simulated session."""
    main = _main()
    tm = _train_model()
    if modality not in SEED_MODALITIES:
        raise SystemExit(f"desteklenmeyen giris turu {modality!r} ({', '.join(SEED_MODALITIES)})")
    sim_seed = secrets.randbits(32) if sim_seed is None else sim_seed
    interval_s = main.SDK_FLUSH_INTERVAL_MS / 1000 if interval_s is None else interval_s

    identity = identity_for(customer)
    tm.rng = np.random.default_rng([customer.seed, 99, sim_seed])
    windows = tm.simulate_identity_sessions(identity, 1, modality)[0]

    log("=" * 72)
    log(SIMULATED_LABEL)
    log(f"Sentetik müşteri: {customer.name} ({customer.ref}); giriş türü: {'fare' if modality == 'mouse' else 'klavye'}")
    log(f"Simülatör tohumu: {sim_seed} (aynı oturum için: --sim-seed {sim_seed})")

    session_id, token = attest(post)
    headers = {"X-DeepCheck-Token": token}
    log(f"Oturum: {session_id}")

    pointer_events = 0
    last = {}
    for index, window in enumerate(windows):
        if index:
            await asyncio.sleep(interval_s)
        now_ms = int(time.time() * 1000)
        flush = rebase_window(window, now_ms - NEWEST_EVENT_LAG_MS)
        pointer_events += len(flush["mouse_trajectory"]) + len(flush["click_timing"])
        body = {
            "session_id": session_id,
            **flush,
            "client_signals": client_signals(pointer_events),
            "client_sent_at": now_ms,
        }
        status, last = post("/api/analyze", body, headers)
        if status != 200:
            raise SystemExit(f"/api/analyze {status} ({index + 1}. akis): {last.get('detail')}")
        if index == 0:
            # Flagged before the decision is made, so nothing ever reads this
            # session as a person's -- not even between two flushes.
            async with db_factory() as db:
                await mark_simulated(db, session_id)
    log(
        f"Akışlar: {len(windows)}/{len(windows)} gönderildi; son oturum skoru "
        f"{last.get('risk_score')} ({last.get('label')})"
    )

    status, charge = post(
        "/api/demo/charge",
        {"session_id": session_id, "amount": DEMO_AMOUNT, "customer_ref": customer.ref},
        headers,
    )
    if status != 200:
        raise SystemExit(f"/api/demo/charge {status}: {charge.get('detail')}")
    decision = charge.get("decision") or {}

    async with db_factory() as db:
        await mark_simulated(db, session_id)
        audit = await latest_audit(db, session_id)

    log(f"Ödeme kararı (/api/demo/charge): {charge.get('status')} — action={decision.get('action')}, reason={decision.get('reason')}")
    if audit is None:
        log("Profil katmanı: bu karar için denetim kaydı yok (katman kapalı ya da müşteri belirtilmedi).")
    else:
        p_value = audit["p_value"]
        log(
            "Profil katmanı (karar denetim kaydı, yalnızca operatör için): "
            f"durum={audit['profile_state']}, giriş türü={audit['modality']}, "
            f"referans={audit['reference_n']}, p={'—' if p_value is None else f'{p_value:.4f}'}, "
            f"iç gerekçe={audit['reason']}, gölge modu={'evet' if audit['shadow'] else 'hayır'}"
        )
    challenged_by_profile = audit is not None and audit["reason"] == "profile_deviation"
    if challenged_by_profile:
        log(
            "Sonuç: profil katmanı ek doğrulama istedi. Aynı sentetik kimliğin oturumlarında bu, "
            f"sentetik kimlikler üzerinde ölçülen {SAME_PERSON_CHALLENGE_RATE} oranında olur "
            "(docs/profile-evaluation.md; gerçek kişiler için bir alt sınır)."
        )
    elif charge.get("status") == "charged":
        log("Sonuç: ek doğrulama istenmedi — beklenen: oturum, profili oluşturan aynı sentetik kimlikten geldi.")
    else:
        log(
            "Sonuç: ödeme alınmadı, fakat gerekçe profil katmanı değil (iç gerekçe "
            f"{audit['reason'] if audit else decision.get('reason')}): oturum kanıtı karar için yeterli değildi."
        )
    log("Bu oturum, karar kaydı ve varsa öğrenilen vektörü veritabanında is_synthetic=true olarak işaretlendi.")
    log("=" * 72)
    return {"session_id": session_id, "charge": charge, "audit": audit, "sim_seed": sim_seed}


# --- CLI ---------------------------------------------------------------------------


async def _run(args) -> int:
    import database

    try:
        await database.init_db()
        factory = database.get_sessionmaker()
        for warning in configuration_warnings():
            print("UYARI: " + warning)
        if args.simulate:
            await simulate(
                factory,
                customer_by_key(args.simulate),
                post=http_poster(args.api_url),
                modality=args.modality,
                sim_seed=args.sim_seed,
            )
            return 0
        async with factory() as db:
            if args.status:
                return await print_status(db)
            return await seed(db, reset=args.reset)
    finally:
        await database.get_engine().dispose()


def main_cli(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(
        description="Juri prototipi icin SENTETIK demo musterileri (gercek kisi degil)."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--reset", action="store_true", help="Sentetik demo musterilerini silip yeniden olustur")
    action.add_argument("--status", action="store_true", help="Yalnizca mevcut durumu yazdir")
    action.add_argument(
        "--simulate",
        metavar="MUSTERI",
        help=f"Ayni sentetik kimlikten yeni bir oturum simule et ({', '.join(c.key for c in DEMO_CUSTOMERS)})",
    )
    parser.add_argument("--modality", choices=SEED_MODALITIES, default="mouse", help="--simulate icin giris turu")
    parser.add_argument("--sim-seed", type=int, default=None, help="--simulate icin simulator tohumu")
    parser.add_argument(
        "--api-url",
        default=os.getenv("DEMO_SEED_API_URL", "http://localhost:8000"),
        help="--simulate icin calisan API (konteyner icinde varsayilan dogru)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main_cli())
