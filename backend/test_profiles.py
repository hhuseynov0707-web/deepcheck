"""Tests for profiles.py -- the per-customer behavioural profile statistic.

Run:
    pytest test_profiles.py -q

These need no database, no FastAPI app and no model bundle: profiles.py is
deliberately pure so that every threshold in it can be argued about without
infrastructure. (The retention, boot-configuration and endpoint tests at the
bottom are the exception: they import main on first use, and still run against
fakes, not Postgres.) What they exist to catch is the failure mode this layer has and
the risk score does not -- a statistic that quietly challenges a real customer
for behaving slightly differently from their own past, which is a control that
costs sales while catching nobody. Nearly every assertion below is therefore an
assertion about ABSTENTION: the layer must say nothing unless it has actually
measured something.
"""

import copy
import itertools
import math
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import profiles
from lstm_model import FEATURE_NAMES

KEY = b"test-profile-key-not-for-production-32+chars"
BASE_T = datetime(2026, 9, 1, 12, 0, 0)


# --- fixtures ----------------------------------------------------------------


def _vec(**overrides) -> dict:
    """A full 12-feature session vector, mid-scale, with overrides."""
    vec = {name: 0.50 for name in FEATURE_NAMES}
    vec.update(overrides)
    return vec


def _reference_set(n: int, seed: int = 0, spread: float = 0.08) -> list[dict]:
    """n session vectors from one synthetic "person": a fixed centre plus
    per-session noise. SYNTHETIC -- see the honesty note in profiles.py; a
    generator with fixed parameters is self-consistent in a way no real person
    is, so nothing measured here is a claim about real customers."""
    rng = np.random.default_rng(seed)
    references = []
    for _ in range(n):
        references.append(
            {name: float(0.50 + rng.normal(0, spread)) for name in FEATURE_NAMES}
        )
    return references


def _flush(mask_bits: int, **values) -> dict:
    row = {name: 0.5 for name in FEATURE_NAMES}
    row.update(values)
    row["measured_mask"] = mask_bits
    return row


_ALL_MEASURED = (1 << len(FEATURE_NAMES)) - 1


def _entry(created_at: datetime, probation: bool = False, tag: str = "") -> dict:
    return {"created_at": created_at, "probation": probation, "tag": tag}


# --- identity ----------------------------------------------------------------


def test_profile_id_is_stable_and_namespaced_per_merchant():
    a = profiles.derive_profile_id("acme", "customer-42", KEY)
    again = profiles.derive_profile_id("acme", "customer-42", KEY)
    other_merchant = profiles.derive_profile_id("globex", "customer-42", KEY)
    other_key = profiles.derive_profile_id("acme", "customer-42", b"a different key")

    assert a == again, "derivation must be deterministic or no profile is findable"
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)
    assert a != other_merchant, "two merchants must not share one customer's profile"
    assert a != other_key


def test_profile_id_length_prefixing_blocks_a_field_boundary_collision():
    # The concatenation bug this encoding exists to prevent: with "merchant|ref"
    # a merchant who puts the separator in their own id lands on another
    # merchant's customer, which is cross-merchant linkability handed over for
    # free.
    assert profiles.derive_profile_id("a|b", "c", KEY) != profiles.derive_profile_id(
        "a", "b|c", KEY
    )


def test_profile_id_does_not_carry_the_raw_reference():
    ref = "+905551234567"
    profile_id = profiles.derive_profile_id("acme", ref, KEY)
    assert ref not in profile_id
    # Not even a fragment: the id is hex, so no part of a phone number, e-mail
    # address or national id can survive into it.
    for start in range(len(ref) - 3):
        assert ref[start : start + 4] not in profile_id


# --- reference statistics ----------------------------------------------------


def test_reference_statistics_match_an_independent_batch_computation():
    """The whole design recomputes centre and scale from the stored vectors on
    every read rather than carrying incremental state, so the thing to verify is
    that the recomputation is the textbook statistic -- checked here against
    numpy rather than against another copy of the same code."""
    references = _reference_set(19, seed=7)
    stats = profiles.feature_stats(references)
    for name in FEATURE_NAMES:
        column = np.array([r[name] for r in references], dtype=float)
        expected_centre = float(np.median(column))
        expected_scale = 1.4826 * float(np.median(np.abs(column - expected_centre)))
        assert stats[name].n_obs == len(references)
        assert stats[name].centre == round(expected_centre, 12) or math.isclose(
            stats[name].centre, expected_centre, rel_tol=1e-12, abs_tol=1e-12
        )
        assert math.isclose(stats[name].scale, expected_scale, rel_tol=1e-12, abs_tol=1e-12)


def test_a_feature_the_profile_has_never_seen_does_not_participate():
    # Every reference is silent on odak_degisimi (touch sessions that never lost
    # focus, say); the candidate measures it and measures it oddly. A feature
    # with no history carries no information about this customer.
    references = _reference_set(19, seed=3)
    for reference in references:
        reference["odak_degisimi"] = None
    candidate = _vec(odak_degisimi=9.0)

    stats = profiles.feature_stats(references)
    assert stats["odak_degisimi"].n_obs == 0
    assert "odak_degisimi" not in profiles.participating_features(candidate, stats)

    verdict = profiles.evaluate_profile(candidate, references)
    assert verdict.state == profiles.STATE_EVALUATED
    assert all(item["feature"] != "odak_degisimi" for item in verdict.top_features)
    assert verdict.escalate is False


def test_a_thinly_observed_feature_does_not_participate():
    # Measured in 7 of 19 sessions, one below PROFILE_MIN_FEATURE_OBS.
    references = _reference_set(19, seed=11)
    for reference in references[:12]:
        reference["kanal_gecis_gecikmesi"] = None
    stats = profiles.feature_stats(references)
    assert stats["kanal_gecis_gecikmesi"].n_obs == profiles.PROFILE_MIN_FEATURE_OBS - 1
    assert "kanal_gecis_gecikmesi" not in profiles.participating_features(_vec(), stats)


def test_low_spread_feature_is_excluded_not_floored():
    """The single most important false-positive fix in the layer.

    odak_degisimi is an integer count that is 0 for most people in most
    sessions. Floor its scale and a customer who alt-tabs once to read the SMS
    code this very system just sent them produces an enormous z and is
    challenged for doing exactly what the previous challenge asked."""
    references = _reference_set(19, seed=5)
    for reference in references:
        reference["odak_degisimi"] = 0.0
    candidate = _vec(odak_degisimi=1.0)

    stats = profiles.feature_stats(references)
    assert stats["odak_degisimi"].scale == 0.0
    assert "odak_degisimi" not in profiles.participating_features(candidate, stats)

    score, top = profiles.deviation(
        candidate, stats, profiles.participating_features(candidate, stats)
    )
    assert score is not None and math.isfinite(score), "a zero-variance feature must not divide by zero"
    assert all(item["feature"] != "odak_degisimi" for item in top)


def test_a_profile_with_no_spread_anywhere_abstains():
    # Every reference identical: a profile that has only ever seen one exact
    # vector cannot say anything about a new one, and must not pretend to.
    references = [_vec() for _ in range(profiles.PROFILE_MIN_SESSIONS)]
    verdict = profiles.evaluate_profile(_vec(tereddut_skoru=0.9), references)
    assert verdict.state == profiles.STATE_TOO_FEW_FEATURES
    assert verdict.escalate is False
    assert verdict.deviation is None


# --- the deviation statistic -------------------------------------------------


def test_deviation_is_zero_for_an_exact_match():
    references = _reference_set(19, seed=1)
    stats = profiles.feature_stats(references)
    centre = {name: stats[name].centre for name in FEATURE_NAMES}
    features = profiles.participating_features(centre, stats)
    score, _ = profiles.deviation(centre, stats, features)
    assert score == 0.0


def test_deviation_grows_with_distance():
    references = _reference_set(19, seed=2)
    stats = profiles.feature_stats(references)
    scores = []
    for offset in (0.0, 0.1, 0.3, 0.8):
        candidate = {name: stats[name].centre + offset for name in FEATURE_NAMES}
        features = profiles.participating_features(candidate, stats)
        score, _ = profiles.deviation(candidate, stats, features)
        scores.append(score)
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


def test_deviation_is_finite_when_one_feature_has_zero_variance():
    references = _reference_set(19, seed=4)
    for reference in references:
        reference["zaman_kuantasyonu"] = 0.0
    candidate = _vec(zaman_kuantasyonu=0.0, tereddut_skoru=0.9)
    stats = profiles.feature_stats(references)
    features = profiles.participating_features(candidate, stats)
    score, top = profiles.deviation(candidate, stats, features)
    assert score is not None and math.isfinite(score)
    assert len(top) == min(profiles.PROFILE_TOP_K, len(features))


def test_top_features_are_the_largest_z_values():
    references = _reference_set(19, seed=6)
    stats = profiles.feature_stats(references)
    candidate = {name: stats[name].centre for name in FEATURE_NAMES}
    candidate["tereddut_skoru"] = stats["tereddut_skoru"].centre + 1.0
    candidate["ivme_degisimi"] = stats["ivme_degisimi"].centre + 0.5
    features = profiles.participating_features(candidate, stats)
    _, top = profiles.deviation(candidate, stats, features)
    assert [item["feature"] for item in top][:2] == ["tereddut_skoru", "ivme_degisimi"]


# --- the maturity gate -------------------------------------------------------


def test_maturity_is_derived_from_alpha_not_tuned():
    # With n references the smallest attainable p-value is 1/(n+1), so at
    # alpha 0.05 the layer cannot fire below 19. This is arithmetic, and the
    # test exists so a future "let's lower it to 5 so the demo fires" shows up
    # as a failure rather than as a threshold that can never be reached.
    assert profiles.PROFILE_MIN_SESSIONS == math.ceil(1 / profiles.PROFILE_ALPHA) - 1
    assert 1 / (profiles.PROFILE_MIN_SESSIONS + 1) <= profiles.PROFILE_ALPHA


def test_maturity_gate_blocks_escalation_and_performs_no_comparison():
    references = _reference_set(profiles.PROFILE_MIN_SESSIONS - 1, seed=8)
    wildly_different = _vec(**{name: 5.0 for name in FEATURE_NAMES})
    verdict = profiles.evaluate_profile(wildly_different, references)

    assert verdict.state == profiles.STATE_IMMATURE
    assert verdict.escalate is False
    # Nothing computed, nothing named: an immature profile is the ABSENCE of an
    # opinion, not a weak one, and the SOC panel must not be shown a deviation
    # that was measured against too little.
    assert verdict.deviation is None
    assert verdict.p_value is None
    assert verdict.top_features == []
    assert verdict.reference_n == profiles.PROFILE_MIN_SESSIONS - 1


def test_a_mature_profile_can_escalate_on_a_far_candidate():
    references = _reference_set(profiles.PROFILE_MIN_SESSIONS, seed=9)
    far = _vec(**{name: 0.50 + 1.0 for name in FEATURE_NAMES})
    verdict = profiles.evaluate_profile(far, references)
    assert verdict.state == profiles.STATE_EVALUATED
    assert verdict.escalate is True
    assert verdict.p_value is not None and verdict.p_value <= profiles.PROFILE_ALPHA
    assert verdict.p_value_low is not None, "the replay tail is recorded even though it is not enforced"


def test_a_matching_session_is_never_escalated():
    # Trust-on-match is not implemented and matching buys nothing, but the
    # inverse must hold absolutely: a session that looks like the customer's own
    # history must never be challenged BY THIS LAYER.
    references = _reference_set(profiles.PROFILE_MIN_SESSIONS, seed=10)
    stats = profiles.feature_stats(references)
    candidate = {name: stats[name].centre for name in FEATURE_NAMES}
    verdict = profiles.evaluate_profile(candidate, references)
    assert verdict.state == profiles.STATE_EVALUATED
    assert verdict.escalate is False


def test_in_profile_sessions_are_challenged_at_most_alpha():
    """Empirical check of the only guarantee this layer offers: over
    exchangeable draws from one synthetic identity, the escalation rate on
    in-profile sessions stays at or below alpha.

    SYNTHETIC identities, so this is a LOWER BOUND on the real false-challenge
    rate and nothing more -- a generator with fixed parameters is far more
    self-consistent than a person with a new phone, a cold, or a train to
    catch."""
    escalated = 0
    trials = 300
    for seed in range(trials):
        draws = _reference_set(profiles.PROFILE_MIN_SESSIONS + 1, seed=1000 + seed)
        verdict = profiles.evaluate_profile(draws[-1], draws[:-1])
        assert verdict.state == profiles.STATE_EVALUATED
        escalated += int(verdict.escalate)
    rate = escalated / trials
    # +0.03 is sampling tolerance at n=300, not headroom granted to the method.
    assert rate <= profiles.PROFILE_ALPHA + 0.03, f"false challenge rate {rate:.3f}"


# --- full-conformal calibration ------------------------------------------------
#
# A vector-level SYNTHETIC identity: per feature, a person's own centre, their
# own session-to-session spread, and whether the feature is measured at all in
# a given session. Vector-level rather than train_model.simulate_identity_
# sessions() because that simulator costs about 6 ms a session plus the torch
# and model-bundle imports, and a calibration check belongs in the default
# suite at a few seconds.
#
# Per feature: (within-person spread, share of identities for whom the feature
# never varies, chance it is measured in one session). The spreads and the
# never-varies shares are the medians and zero-scale shares profile_lab.py
# measured over 20-session reference sets of train_model's synthetic identities
# (docs/profile-evaluation.md section 4); each identity's spread is scaled by a
# lognormal factor (sigma 0.31, from that table's p5/p50 ratio of about 0.6).
# The measurement chances are NOT measured: they are set so that WHICH features
# participate differs from session to session, which is exactly the condition
# under which the leave-one-out rank and full conformal part ways.
# zaman_kuantasyonu sits on PROFILE_SCALE_FLOOR on purpose, for the same reason.
# Nothing here is a real person; every rate below is a statement about this
# generator and the rank, not about customers.
_SYNTHETIC_FEATURES = {
    "mouse": {
        "scroll_hizi_varyansi": (0.079, 0.0, 0.6),
        "tereddut_skoru": (0.054, 0.0, 1.0),
        "etkilesim_entropisi": (0.035, 0.0, 1.0),
        "ivme_degisimi": (0.184, 0.21, 1.0),
        "tiklama_yogunlugu": (0.074, 0.04, 1.0),
        "odak_degisimi": (0.0, 1.0, 1.0),
        "hiz_otokorelasyonu": (0.033, 0.0, 1.0),
        "yon_tutarliligi": (0.056, 0.0, 1.0),
        "zaman_kuantasyonu": (0.0063, 0.0, 1.0),
        "duraklama_dagilimi": (0.085, 0.0, 1.0),
        "tiklama_oncesi_hareket": (0.124, 0.24, 1.0),
        "kanal_gecis_gecikmesi": (0.05, 0.0, 0.25),
    },
    "keyboard": {
        "scroll_hizi_varyansi": (0.083, 0.0, 0.6),
        "tereddut_skoru": (0.161, 0.0, 1.0),
        "etkilesim_entropisi": (0.044, 0.0, 1.0),
        "tiklama_yogunlugu": (0.0, 1.0, 1.0),
        "odak_degisimi": (0.0, 1.0, 1.0),
        "zaman_kuantasyonu": (0.0084, 0.0, 1.0),
        "duraklama_dagilimi": (0.052, 0.0, 1.0),
        "kanal_gecis_gecikmesi": (0.05, 0.0, 0.25),
    },
}


def _synthetic_identity(rng, modality: str) -> dict:
    table = _SYNTHETIC_FEATURES[modality]
    identity = {}
    for name in FEATURE_NAMES:
        if name not in table:
            identity[name] = None  # never measured on this input type
            continue
        spread, never_varies, measured = table[name]
        sd = 0.0 if rng.random() < never_varies else spread * float(np.exp(rng.normal(0.0, 0.31)))
        identity[name] = (float(rng.uniform(0.25, 0.75)), sd, measured)
    return identity


def _identity_session(rng, identity: dict) -> dict:
    vec = {}
    for name in FEATURE_NAMES:
        traits = identity[name]
        if traits is None:
            vec[name] = None
            continue
        centre, sd, measured = traits
        value = centre + float(rng.normal(0.0, sd)) if sd > 0 else centre
        vec[name] = value if rng.random() < measured else None
    return vec


def test_full_conformal_scores_every_point_with_one_function():
    """The property the alpha bound rests on, checked directly rather than
    through a rate. Put the candidate among its n references: every one of the
    n + 1 points must be scored against the OTHER n points by the same
    function (profiles.point_deviation), whichever of them plays the candidate.

    Two consequences are asserted exactly. (1) The calibration set a verdict
    ranks against is, point for point, the deviation each reference gets when
    IT is the candidate. (2) Rotating the candidate role through all n + 1
    sessions escalates at most floor(alpha * scorable) of them -- the
    finite-sample guarantee as an identity, with no sampling tolerance. The
    leave-one-out rank this replaced scored each reference against n - 1
    points on the candidate's features, so neither held for it.

    SYNTHETIC identities with heterogeneous feature participation (see
    _SYNTHETIC_FEATURES), both input types, so references do get dropped and
    abstentions do happen. Every other identity also carries one session twice
    -- a replay -- because a copy scores exactly like its original, and ties
    are where both tails' >= / <= have to be right."""
    alpha = profiles.PROFILE_ALPHA
    rng = np.random.default_rng(20260917)
    rotations = escalations = tied = 0
    for modality in ("mouse", "keyboard"):
        for index in range(8):
            identity = _synthetic_identity(rng, modality)
            points = [_identity_session(rng, identity) for _ in range(profiles.PROFILE_BUFFER_MAX + 1)]
            if index % 2:
                points[-1] = dict(points[0])
            others = [points[:j] + points[j + 1 :] for j in range(len(points))]
            scores = [profiles.point_deviation(points[j], others[j])[0] for j in range(len(points))]
            scorable = [j for j, s in enumerate(scores) if s is not None]

            escalated_here = 0
            for j, point in enumerate(points):
                verdict = profiles.evaluate_profile(point, others[j], modality=modality)
                if scores[j] is None:
                    assert verdict.state == profiles.STATE_TOO_FEW_FEATURES, (modality, j)
                    continue
                calibration = profiles.calibration_deviations(point, others[j])
                assert calibration == [scores[i] for i in range(len(points)) if i != j and scores[i] is not None]
                if len(scorable) - 1 < profiles.PROFILE_MIN_SESSIONS:
                    assert verdict.state == profiles.STATE_IMMATURE and not verdict.escalate
                    continue
                assert verdict.state == profiles.STATE_EVALUATED
                assert verdict.deviation == scores[j]
                at_least = sum(1 for i in scorable if i != j and scores[i] >= scores[j])
                at_most = sum(1 for i in scorable if i != j and scores[i] <= scores[j])
                assert verdict.p_value == round((1 + at_least) / len(scorable), 4)
                assert verdict.p_value_low == round((1 + at_most) / len(scorable), 4)
                rotations += 1
                tied += int(at_least + at_most > len(scorable) - 1)
                escalated_here += int(verdict.escalate)
            assert escalated_here <= math.floor(alpha * len(scorable)), (modality, escalated_here, len(scorable))
            escalations += escalated_here
    # Not vacuous: the fixture produced mature rotations, real escalations and
    # tied scores.
    assert rotations >= 200 and escalations >= 8 and tied >= 8, (rotations, escalations, tied)


def test_same_person_challenge_rate_is_within_alpha_on_synthetic_identities():
    """The rate a customer actually pays: fresh sessions of the same synthetic
    person against a 20-session profile, over a fixed-seed set of 100
    mouse-like identities x 10 sessions.

    Expected value, for exchangeable sessions and no ties: exactly 1/21 (4.8%)
    -- the candidate is challenged only when it is the most extreme of 21
    points. The tolerance is 0.025, about three standard errors of this
    design: full conformal's per-profile challenge probability varies between
    reference sets as Beta(1, 20) (variance 0.0021), so the rate's variance is
    0.0021 / 100 + 0.043 / 1000, a standard error of 0.008. Two-sided, because a
    rank that never escalates would pass a one-sided bound.

    SYNTHETIC identities (_SYNTHETIC_FEATURES), so this is a LOWER BOUND on a
    real customer's false-challenge rate: a generator is more self-consistent
    than a person. What it pins is the calibration, not the level."""
    rng = np.random.default_rng(20260918)
    challenged = presented = 0
    for _ in range(100):
        identity = _synthetic_identity(rng, "mouse")
        references = [_identity_session(rng, identity) for _ in range(profiles.PROFILE_BUFFER_MAX)]
        for _ in range(10):
            verdict = profiles.evaluate_profile(_identity_session(rng, identity), references, modality="mouse")
            assert verdict.state == profiles.STATE_EVALUATED, verdict.state
            challenged += int(verdict.escalate)
            presented += 1
    rate = challenged / presented
    expected = 1 / (profiles.PROFILE_BUFFER_MAX + 1)
    assert expected <= profiles.PROFILE_ALPHA
    assert abs(rate - expected) <= 0.025, f"ayni kisi icin ek dogrulama orani {rate:.3f}, beklenen {expected:.3f}"


def test_a_thin_session_abstains():
    verdict = profiles.evaluate_profile(None, _reference_set(19, seed=12))
    assert verdict.state == profiles.STATE_THIN_SESSION
    assert verdict.escalate is False


# --- the bounded reference list ----------------------------------------------


def test_buffer_never_exceeds_the_cap_and_keeps_the_newest():
    buffer: list[dict] = []
    for i in range(25):
        buffer, _ = profiles.admit_vector(buffer, _entry(BASE_T + timedelta(days=i), tag=f"v{i}"))
        assert len(buffer) <= profiles.PROFILE_BUFFER_MAX
    assert len(buffer) == profiles.PROFILE_BUFFER_MAX
    assert [entry["tag"] for entry in buffer] == [
        f"v{i}" for i in range(25 - profiles.PROFILE_BUFFER_MAX, 25)
    ]


def test_probation_eviction_never_drops_a_clean_vector():
    # Four probation slots already used; the fifth must cost the OLDEST
    # PROBATION vector, never a clean one. Otherwise a patient attacker who can
    # pass step-up repeatedly evicts the customer's real behaviour one clean
    # vector at a time and ends up owning the profile.
    buffer: list[dict] = []
    for i in range(10):
        buffer, _ = profiles.admit_vector(buffer, _entry(BASE_T + timedelta(days=i), tag=f"clean{i}"))
    for i in range(profiles.PROFILE_PROBATION_MAX):
        buffer, _ = profiles.admit_vector(
            buffer, _entry(BASE_T + timedelta(days=20 + i), probation=True, tag=f"prob{i}")
        )
    assert sum(1 for e in buffer if e["probation"]) == profiles.PROFILE_PROBATION_MAX

    buffer, evicted = profiles.admit_vector(
        buffer, _entry(BASE_T + timedelta(days=40), probation=True, tag="prob-new")
    )
    assert [e["tag"] for e in evicted] == ["prob0"]
    assert sum(1 for e in buffer if e["probation"]) == profiles.PROFILE_PROBATION_MAX
    assert sum(1 for e in buffer if not e["probation"]) == 10, "no clean vector may be evicted"


def test_probation_is_capped_at_one_fifth_of_the_buffer():
    # Probation vectors are not references (they are excluded from the
    # statistic until promoted), so this cap bounds STORAGE of unconfirmed
    # sessions, not anyone's share of the statistic: at most 4 beside the 20.
    buffer: list[dict] = []
    for i in range(40):
        buffer, _ = profiles.admit_vector(
            buffer, _entry(BASE_T + timedelta(hours=i), probation=True, tag=f"p{i}")
        )
    assert sum(1 for e in buffer if e["probation"]) <= profiles.PROFILE_PROBATION_MAX
    assert [e["tag"] for e in buffer] == [f"p{i}" for i in range(40 - profiles.PROFILE_PROBATION_MAX, 40)]
    assert profiles.PROFILE_PROBATION_MAX / profiles.PROFILE_BUFFER_MAX <= 0.2


def test_a_probation_vector_never_evicts_a_reference():
    """A full reference set plus probation vectors: the probation vector takes
    a slot of its own, and only a reference can push a reference out. If a
    probation admission evicted the oldest reference, an attacker who passes
    step-up twice would drop a 20-reference profile to 18 -- below maturity --
    and switch the layer off for himself."""
    buffer = [_entry(BASE_T + timedelta(days=i), tag=f"ref{i}") for i in range(profiles.PROFILE_BUFFER_MAX)]
    for i in range(profiles.PROFILE_PROBATION_MAX + 2):
        buffer, evicted = profiles.admit_vector(
            buffer, _entry(BASE_T + timedelta(days=40 + i), probation=True, tag=f"prob{i}")
        )
        assert not any(not e["probation"] for e in evicted), f"denetimli ekleme referans cikardi: {evicted}"
    references = [e for e in buffer if profiles.is_reference(e)]
    assert len(references) == profiles.PROFILE_BUFFER_MAX
    assert len(buffer) == profiles.PROFILE_BUFFER_MAX + profiles.PROFILE_PROBATION_MAX

    # A newly learned reference evicts the oldest REFERENCE, never a newer or
    # older probation vector: those are pending evidence.
    buffer, evicted = profiles.admit_vector(buffer, _entry(BASE_T + timedelta(days=90), tag="ref-new"))
    assert [e["tag"] for e in evicted] == ["ref0"]
    assert sum(1 for e in buffer if e["probation"]) == profiles.PROFILE_PROBATION_MAX

    # Two entries that compare equal are still two entries.
    twins = [_entry(BASE_T, tag="ikiz") for _ in range(profiles.PROFILE_BUFFER_MAX + 1)]
    kept, evicted = profiles.enforce_buffer_caps(twins)
    assert (len(kept), len(evicted)) == (profiles.PROFILE_BUFFER_MAX, 1)


def test_is_reference_excludes_probation_and_disputed():
    assert profiles.is_reference({"probation": False, "outcome": "pending"}) is True
    assert profiles.is_reference({"probation": False, "outcome": "settled"}) is True
    assert profiles.is_reference({"probation": True, "outcome": "pending"}) is False
    assert profiles.is_reference({"probation": False, "outcome": "disputed"}) is False


def test_healing_promotes_a_run_never_a_single_pass():
    """Promotion path B. The run is the probation vectors newer than the
    newest reference of the modality; it is promoted only when the profile's
    counter has reached PROFILE_HEAL_AFTER AND the run itself is that long."""
    heal = profiles.PROFILE_HEAL_AFTER
    references = [_entry(BASE_T + timedelta(days=i), tag=f"ref{i}") for i in range(12)]
    run = [_entry(BASE_T + timedelta(days=30 + i), probation=True, tag=f"run{i}") for i in range(heal)]

    promoted = profiles.promotable_run(references + run, heal)
    assert [e["tag"] for e in promoted] == [f"run{i}" for i in range(heal)]
    # The counter alone is not enough...
    assert profiles.promotable_run(references + run[:1], heal) == []
    assert profiles.promotable_run(references + run[:1], 10 * heal) == []
    # ...nor is the run alone...
    assert profiles.promotable_run(references + run, heal - 1) == []
    # ...and a reference in between breaks the run: the customer passed
    # unchallenged since, so the earlier passes are not "in a row".
    between = _entry(BASE_T + timedelta(days=30, hours=12), tag="between")
    assert [e["tag"] for e in profiles.promotable_run(references + run + [between], heal)] == []
    older = [_entry(BASE_T + timedelta(days=20), probation=True, tag="old-prob")]
    broken = references + older + [_entry(BASE_T + timedelta(days=25), tag="clean")] + run[:2]
    assert profiles.promotable_run(broken, heal) == []

    # The promoted run is the newest part of the buffer and is never cut by
    # the rebuild that follows it.
    assert heal <= profiles.PROFILE_PROBATION_MAX <= profiles.PROFILE_HEAL_KEEP
    for entry in promoted:
        entry["probation"] = False
    kept, _ = profiles.heal_buffer(references + run)
    assert {id(e) for e in promoted} <= {id(e) for e in kept}
    assert profiles.is_mature(sum(profiles.is_reference(e) for e in kept)) is False


def test_an_absorbed_vector_moves_the_centre_less_than_a_running_mean_would():
    """This is what replaced the "reduced-weight Welford update" in the design.

    A bounded buffer plus a median centre bounds one vector's influence by
    construction: no weight parameter, no probabilistic acceptance, and nothing
    that has to be tuned. The comparison below is against the full-weight
    alternative that a running mean would have given."""
    references = [_vec(tereddut_skoru=0.50) for _ in range(profiles.PROFILE_BUFFER_MAX - 1)]
    outlier = _vec(tereddut_skoru=5.0)

    before = profiles.feature_stats(references)["tereddut_skoru"].centre
    after = profiles.feature_stats(references + [outlier])["tereddut_skoru"].centre
    buffered_shift = abs(after - before)

    column = [r["tereddut_skoru"] for r in references]
    mean_before = sum(column) / len(column)
    mean_after = (sum(column) + outlier["tereddut_skoru"]) / (len(column) + 1)
    mean_shift = abs(mean_after - mean_before)

    assert buffered_shift < mean_shift
    assert buffered_shift == 0.0, "one outlier in twenty cannot move a median at all"


def test_one_absorbed_vector_cannot_move_the_p_value_far():
    # The other half of the bound: with the statistic being a rank against the
    # profile's own sessions, adding one reference moves any p-value by at most
    # 1/(n+1) -- the resolution of the rank itself.
    references = _reference_set(profiles.PROFILE_MIN_SESSIONS, seed=13)
    candidate = _vec(tereddut_skoru=0.75)
    before = profiles.evaluate_profile(candidate, references)
    poisoned = references + [_vec(**{name: 3.0 for name in FEATURE_NAMES})]
    after = profiles.evaluate_profile(candidate, poisoned)
    assert before.state == after.state == profiles.STATE_EVALUATED
    resolution = 1.0 / (profiles.PROFILE_MIN_SESSIONS + 1)
    assert abs(after.p_value - before.p_value) <= 2 * resolution + 1e-9


def test_healing_drops_back_below_maturity_on_purpose():
    buffer = [_entry(BASE_T + timedelta(days=i), tag=f"v{i}") for i in range(20)]
    kept, dropped = profiles.heal_buffer(buffer)
    assert len(kept) == profiles.PROFILE_HEAL_KEEP
    assert [e["tag"] for e in kept] == [f"v{i}" for i in range(10, 20)]
    assert len(dropped) == 10
    # The point of the rebuild: the layer must now abstain until it has
    # re-learned who the customer is, rather than keep challenging them against
    # a picture of who they used to be.
    assert profiles.is_mature(len(kept)) is False
    assert profiles.needs_healing(profiles.PROFILE_HEAL_AFTER) is True
    assert profiles.needs_healing(profiles.PROFILE_HEAL_AFTER - 1) is False


# --- session vectors ---------------------------------------------------------


def test_unmeasured_features_never_enter_a_vector():
    # tereddut_skoru's bit is clear in every flush, so its stored 0.5 is a
    # NEUTRAL_DEFAULTS fallback, not a measurement. It must come out as None --
    # otherwise the profile learns "how much telemetry did this session
    # produce", not "is this the same person".
    bit = FEATURE_NAMES.index("tereddut_skoru")
    mask = _ALL_MEASURED & ~(1 << bit)
    rows = [_flush(mask, tereddut_skoru=0.5) for _ in range(4)]
    built = profiles.session_vector(rows)
    assert built is not None
    assert built["vec"]["tereddut_skoru"] is None
    assert built["vec"]["scroll_hizi_varyansi"] == 0.5
    assert built["flush_count"] == 4


def test_provisional_flushes_are_dropped_entirely():
    # Five bits set is below scorer's confident-score floor, so every one of
    # these flushes is mostly fallbacks.
    thin_mask = 0b11111
    assert bin(thin_mask).count("1") < profiles.PROVISIONAL_MEASURED_MIN
    assert profiles.session_vector([_flush(thin_mask) for _ in range(10)]) is None


def test_a_row_without_a_measured_mask_is_dropped_not_guessed():
    rows = [_flush(_ALL_MEASURED) for _ in range(2)]
    legacy = _flush(_ALL_MEASURED)
    legacy["measured_mask"] = None
    assert profiles.session_vector(rows + [legacy]) is None, (
        "two usable flushes is below PROFILE_MIN_FLUSHES; the legacy row must not "
        "be counted to make up the third"
    )


def test_a_feature_measured_in_one_flush_only_is_not_learned():
    bit = FEATURE_NAMES.index("ivme_degisimi")
    rows = [_flush(_ALL_MEASURED & ~(1 << bit)) for _ in range(3)]
    rows.append(_flush(_ALL_MEASURED, ivme_degisimi=0.9))
    built = profiles.session_vector(rows)
    assert built is not None
    assert built["vec"]["ivme_degisimi"] is None


def test_session_vector_is_a_median_and_records_dispersion():
    rows = [
        _flush(_ALL_MEASURED, tereddut_skoru=0.1),
        _flush(_ALL_MEASURED, tereddut_skoru=0.5),
        _flush(_ALL_MEASURED, tereddut_skoru=0.9),
        _flush(_ALL_MEASURED, tereddut_skoru=9.0),  # one odd flush
    ]
    built = profiles.session_vector(rows)
    assert built["vec"]["tereddut_skoru"] == 0.7, "median, so one odd flush cannot set the centre"
    # Dispersion is stored and never scored: the mid-session handover it would
    # catch is already caught per-flush by scorer.LEVEL_SHIFT_POINTS.
    assert built["disp"]["tereddut_skoru"] > 0


def test_too_few_flushes_abstains():
    rows = [_flush(_ALL_MEASURED) for _ in range(profiles.PROFILE_MIN_FLUSHES - 1)]
    assert profiles.session_vector(rows) is None


# --- modality ----------------------------------------------------------------


def test_modality_comes_from_the_pointer_mix():
    assert profiles.session_modality([{"pointer_touch": 40, "pointer_mouse": 2}]) == profiles.MODALITY_TOUCH
    assert profiles.session_modality([{"pointer_mouse": 40, "pointer_touch": 1}]) == profiles.MODALITY_MOUSE
    assert profiles.session_modality([{"pointer_pen": 12}]) == profiles.MODALITY_MOUSE
    assert profiles.session_modality([{}]) == profiles.MODALITY_KEYBOARD
    assert profiles.session_modality(None) == profiles.MODALITY_KEYBOARD
    # Garbage from the client must not throw: these counts are self-reported.
    assert profiles.session_modality([{"pointer_touch": "many"}]) == profiles.MODALITY_KEYBOARD


# --- budget, breaker, and the shape the SOC panel sees -----------------------


def test_per_profile_budget_stops_at_three_per_window():
    now = BASE_T
    assert profiles.budget_allows(0, now, now) is True
    assert profiles.budget_allows(profiles.PROFILE_MAX_ESCALATIONS - 1, now, now) is True
    assert profiles.budget_allows(profiles.PROFILE_MAX_ESCALATIONS, now, now) is False
    # A fresh window, and a never-used profile, both allow.
    old = now - timedelta(days=profiles.PROFILE_BUDGET_WINDOW_DAYS + 1)
    assert profiles.budget_allows(99, old, now) is True
    assert profiles.budget_allows(99, None, now) is True


def test_population_breaker_is_an_absolute_ceiling():
    assert profiles.breaker_allows(profiles.PROFILE_BREAKER_MAX - 1) is True
    assert profiles.breaker_allows(profiles.PROFILE_BREAKER_MAX) is False


def test_public_block_is_uniform_and_carries_no_identifiers():
    references = _reference_set(profiles.PROFILE_MIN_SESSIONS, seed=14)
    evaluated = profiles.evaluate_profile(_vec(), references).as_public_block()
    immature = profiles.evaluate_profile(_vec(), references[:3]).as_public_block()
    thin = profiles.evaluate_profile(None, references).as_public_block()

    expected_keys = {
        "state",
        "modality",
        "reference_n",
        "deviation",
        "p_value",
        "p_value_low",
        "top_features",
        "escalated",
    }
    for block in (evaluated, immature, thin):
        assert set(block) == expected_keys, "the shape must not reveal the state"
        assert block["state"] in profiles.PROFILE_STATES
        # Never the pseudonym, never the raw reference, never the stored
        # vectors: the same argument as SHAP_IN_ANALYZE being off.
        assert "profile_id" not in block
        assert "customer_ref" not in block
        assert "references" not in block


# --- schema version ----------------------------------------------------------


def test_feature_names_change_requires_a_schema_bump():
    """Pinned so that a rename, a reorder, or a rescale of any feature cannot
    land without also bumping FEATURE_SCHEMA_VERSION.

    Stored profiles are compared only within one version. Without this pin, a
    rescaled feature -- tereddut_skoru's move to a log-percentile scale is a
    real example from this repo -- would make every profiled customer deviate on
    the same afternoon, with no failing test anywhere."""
    import hashlib

    import lstm_model

    # One home: profiles re-exports the constant, it does not keep a copy that
    # could be bumped on one side only.
    assert profiles.FEATURE_SCHEMA_VERSION == lstm_model.FEATURE_SCHEMA_VERSION
    assert isinstance(lstm_model.FEATURE_SCHEMA_VERSION, int)

    digest = hashlib.sha256("|".join(FEATURE_NAMES).encode("utf-8")).hexdigest()
    known = {
        1: "e43f3a0fc865d4e002ba71db4dcc45123d107c2590e919e29fe7c7e4a39fc7e7",
    }
    assert profiles.FEATURE_SCHEMA_VERSION in known, (
        "FEATURE_SCHEMA_VERSION was bumped without pinning the feature list it "
        "describes; add the new sha256 to `known` above"
    )
    assert digest == known[profiles.FEATURE_SCHEMA_VERSION], (
        f"FEATURE_NAMES changed ({digest}) without bumping FEATURE_SCHEMA_VERSION. "
        "Stored customer profiles are only comparable within one version."
    )


def test_thresholds_track_scorer():
    """profiles.py keeps two of scorer's constants as literals so that it stays
    importable without the model stack (scorer pulls shap and torch: ~18s of
    import for two integers). This is the test that stops the duplication from
    drifting silently."""
    import scorer

    assert profiles.PROVISIONAL_MEASURED_MIN == scorer.MIN_MEASURED_FOR_CONFIDENT_SCORE
    assert profiles.PROFILE_MIN_FEATURES == scorer.BUCKET_MIN_MEASURED


# --- synthetic identities (spec section 10.1) -----------------------------------


def _rounded(obj):
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {key: _rounded(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_rounded(value) for value in obj]
    return obj


# sha256 of the flush payloads, and of compute_feature_scaling's output, that the
# generator produced at SEED for 40 sessions BEFORE synthetic identities were
# added to train_model.py. Measured with that code on Windows (numpy 2.3.3) and
# in the backend image (numpy 1.26.4, Linux): identical on both, and identical
# again on both after the change. The payloads are rounded to 6 decimals so the
# pin is a statement about the random draws, not about a platform's last ulp:
# the extracted-feature arrays themselves differed between the two numpy builds
# (np.log10 / variance rounding), so they are deliberately not pinned here.
_PRE_IDENTITY_PAYLOAD_SHA256 = "33ca0282792e4f7828e40cf97272b61dc3e007248d00d6ef1c15e43de581f1de"
_PRE_IDENTITY_SCALING_SHA256 = "1df2250c9363b8e75553acbf2459732d53043e8382ca5fac86217d0456e80aa5"
_PRE_IDENTITY_TAIL_DRAW = 0.6346661405957013


def test_identity_support_does_not_change_the_training_dataset():
    """T30. profile_lab.py needs synthetic people whose traits persist across
    sessions, and the only generator is the one the served model is trained
    from. The hard constraint is that adding identities changes NOTHING about
    what training generates: same draws, same order, same values -- or the
    next `python train_model.py` would ship a different model for a feature
    that is not even switched on."""
    import hashlib
    import json

    import train_model

    saved = train_model.rng
    try:
        # _session_traits() and _session_traits(None) are the same call.
        train_model.rng = np.random.default_rng(train_model.SEED)
        plain = train_model._session_traits()
        after_plain = float(train_model.rng.random())
        train_model.rng = np.random.default_rng(train_model.SEED)
        explicit = train_model._session_traits(None)
        after_explicit = float(train_model.rng.random())
        assert plain == explicit
        assert after_plain == after_explicit
        # No identity key leaks into a training session's traits.
        assert set(plain) == {"zero_clicks", "sparse_mouse", "headless", "vx", "vy"}

        # And the whole generator is pinned to its pre-identity output: the
        # same loop generate_synthetic_dataset() runs (feature extraction
        # consumes no draws), then the scaling pass, then one more draw to prove
        # the draw COUNT matched too, not only the values.
        train_model.rng = np.random.default_rng(train_model.SEED)
        rng = train_model.rng
        n = 40
        is_bot = rng.integers(0, 2, size=n)
        payloads = []
        for i in range(n):
            base_t = 1_700_000_000_000 + int(rng.integers(0, 10**9))
            payloads.append(train_model.simulate_session_windows(train_model._pick_persona(is_bot[i]), base_t))
        digest = hashlib.sha256(json.dumps(_rounded(payloads), sort_keys=True).encode()).hexdigest()
        assert digest == _PRE_IDENTITY_PAYLOAD_SHA256, (
            "the synthetic training payloads changed: identity support must leave "
            "generate_synthetic_dataset() bit-identical (spec 10.1)"
        )
        scaling = train_model.compute_feature_scaling(n_sessions=n)
        assert hashlib.sha256(json.dumps(scaling, sort_keys=True).encode()).hexdigest() == _PRE_IDENTITY_SCALING_SHA256
        assert float(train_model.rng.random()) == _PRE_IDENTITY_TAIL_DRAW
    finally:
        train_model.rng = saved


def test_identity_sessions_persist_the_person_and_refuse_touch():
    """The identity path itself: one person's sessions carry that person's
    parameters (plus per-session noise), the modality is what was asked for,
    and touch is refused rather than approximated -- the simulator has no
    model of a finger, so a touch number would describe an invented persona."""
    import train_model

    saved = train_model.rng
    try:
        train_model.rng = np.random.default_rng(7)
        person = train_model._identity_traits()
        assert set(person) == set(train_model.IDENTITY_PARAMETERS)

        mouse = train_model.simulate_identity_sessions(person, 3, "mouse")
        keyboard = train_model.simulate_identity_sessions(person, 3, "keyboard")
        assert len(mouse) == 3 and all(len(s) == train_model.SEQUENCE_LENGTH for s in mouse)
        assert all(w["mouse_trajectory"] and w["click_timing"] for s in mouse for w in s)
        assert all(not w["mouse_trajectory"] and not w["click_timing"] for s in keyboard for w in s)

        traits = [train_model._session_traits(person) for _ in range(200)]
        for name, (_, between_sd, lo, hi) in train_model.IDENTITY_PARAMETERS.items():
            values = np.array([t[name] for t in traits])
            assert lo <= values.min() and values.max() <= hi
            # Per-session spread is the declared fraction of the between-person
            # spread (clipping can only shrink it).
            assert values.std() <= train_model.WITHIN_IDENTITY_SD_RATIO * between_sd * 1.2

        with pytest.raises(ValueError):
            train_model.simulate_identity_sessions(person, 1, "touch")
    finally:
        train_model.rng = saved


# --- retention (spec section 8) ------------------------------------------------
#
# The two sweeps talk to Postgres, and these tests do not: CI has no database on
# purpose (see .github/workflows/ci.yml). What is under test is the transaction
# structure -- which statements share a commit, what a failure rolls back, and
# whether a lock is released -- so the fake below records statements per
# transaction and models the two Postgres behaviours that structure depends on:
# a failed statement aborts the transaction until a rollback, and a
# transaction-scoped advisory lock stays held until that transaction ends.
#
# What this fake CANNOT express is the bug W5 fixed: it is one connection, and
# the leak needed two (the lock taken on the pooled connection the sweep
# started on, the unlock issued on whichever connection came back after the
# commit). test_the_retention_locks_are_transaction_scoped below guards the
# fix by reading the statements themselves instead.


def _main():
    """main, imported on first use. The statistic tests above need no app, so
    this file still collects without one; the environment is the same one
    test_scorer.py sets, and setdefault makes the order irrelevant."""
    import os

    os.environ.setdefault("DEEPCHECK_SECRET", "test-secret-not-for-production")
    os.environ.setdefault("DASHBOARD_KEY", "test-dashboard-key")
    os.environ.setdefault("DEBUG", "0")
    os.environ.setdefault("DEMO_ENDPOINTS", "1")
    import main

    return main


_IDLE_PROFILE_ID = "ab" * 32


class _FakeResult:
    def __init__(self, rowcount=0, rows=()):
        self.rowcount = rowcount
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDatabase:
    """Shared state behind every fake connection: committed transactions and
    held advisory locks."""

    # Rows each statement "affects", keyed by the start of its SQL text.
    ROWCOUNTS = {
        "UPDATE behavior_data": 5,
        "DELETE FROM behavior_data": 4,
        "DELETE FROM customer_profile_vectors": 3,
        "DELETE FROM decision_audit": 2,
        "DELETE FROM profile_access_audit": 1,
    }

    def __init__(self, idle_ids=(_IDLE_PROFILE_ID,), fail_on=None):
        self.idle_ids = list(idle_ids)
        # A statement prefix that raises, the way a constraint violation would.
        self.fail_on = fail_on
        self.transactions = []  # committed, each a list of statement texts
        self.held_locks = set()
        self.rollbacks = 0

    def sessionmaker(self):
        return lambda: _FakeConnection(self)


class _FakeConnection:
    def __init__(self, database):
        self.db = database
        self.pending = []
        self.aborted = False
        # Advisory locks belong to a connection, not to the deployment: this
        # is what THIS connection holds, and it is what the transaction ending
        # releases.
        self.locks = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        # Closing a session discards whatever was not committed -- and ends
        # the transaction, so Postgres drops the xact locks it held.
        self.pending = []
        self._release()
        return False

    def _release(self):
        """Postgres releases a transaction-scoped advisory lock when the
        transaction ends, on the connection that holds it."""
        self.db.held_locks -= self.locks
        self.locks = set()

    async def scalar(self, statement, params=None):
        sql = str(statement)
        assert "pg_try_advisory_xact_lock" in sql, sql
        if params["key"] in self.db.held_locks:
            return False
        self.db.held_locks.add(params["key"])
        self.locks.add(params["key"])
        return True

    async def execute(self, statement, params=None):
        from sqlalchemy.exc import IntegrityError, InternalError

        sql = str(statement)
        # There is no unlock statement to model any more, and there must not
        # be one: an unlock is what W5 found running on the wrong connection.
        assert "pg_advisory_unlock" not in sql, sql
        if self.aborted:
            # Postgres refuses every command in an aborted transaction until
            # a rollback.
            raise InternalError(sql, params, Exception("current transaction is aborted"))
        if self.db.fail_on and sql.startswith(self.db.fail_on):
            self.aborted = True
            # A real driver error carries the bound parameters -- here, the
            # profile ids -- which is why the sweep must not log it verbatim.
            raise IntegrityError(
                sql, statement.compile().params, Exception("violates foreign key constraint")
            )
        self.pending.append(sql)
        if sql.startswith("SELECT customer_profiles.profile_id"):
            rows, self.db.idle_ids = self.db.idle_ids, []
            return _FakeResult(rows=rows)
        if sql.startswith("DELETE FROM customer_profiles "):
            return _FakeResult(rowcount=len(statement.compile().params["profile_id_1"]))
        for prefix, count in _FakeDatabase.ROWCOUNTS.items():
            if sql.startswith(prefix):
                return _FakeResult(rowcount=count)
        return _FakeResult()

    async def commit(self):
        if self.aborted:
            raise AssertionError("commit on an aborted transaction")
        if self.pending:
            self.db.transactions.append(self.pending)
        self.pending = []
        self._release()

    async def rollback(self):
        self.pending = []
        self.aborted = False
        self.db.rollbacks += 1
        self._release()


def _committed(database, prefix):
    """Indexes of the committed transactions containing a statement."""
    return [
        i for i, tx in enumerate(database.transactions) if any(s.startswith(prefix) for s in tx)
    ]


def _run_retention_pass(main, database, monkeypatch):
    import asyncio

    monkeypatch.setattr(main, "get_sessionmaker", database.sessionmaker)
    asyncio.run(main._retention_pass())


def test_profile_sweep_does_not_break_the_telemetry_sweep(monkeypatch, caplog):
    """T25. A session row still references the profile being deleted, and the
    profile delete fails the way a foreign key would make it fail. The
    raw-telemetry blanking must still commit, the profile pass must roll back
    as a whole, its lock must be released so the NEXT pass is not skipped, and
    the failure must be one Turkish line that carries no profile id."""
    import logging

    main = _main()
    caplog.set_level(logging.DEBUG)
    database = _FakeDatabase(fail_on="DELETE FROM customer_profiles ")

    _run_retention_pass(main, database, monkeypatch)

    blanking = _committed(database, "UPDATE behavior_data")
    assert blanking, "ham telemetri karartmasi profil hatasi yuzunden islenmedi"
    assert _committed(database, "DELETE FROM behavior_data") == blanking
    # Nothing of the failed profile pass survived: not the vector delete that
    # ran before the failure, not the session unlink.
    assert not _committed(database, "DELETE FROM customer_profile_vectors")
    assert not _committed(database, "UPDATE sessions")
    assert database.rollbacks == 1
    assert database.held_locks == set(), (
        f"kilit birakilmadi: {database.held_locks}; sonraki her profil temizligi atlanirdi"
    )

    failures = [r for r in caplog.records if "Profil temizligi basarisiz" in r.getMessage()]
    assert len(failures) == 1 and failures[0].levelno == logging.ERROR
    assert "Saklama temizligi basarisiz" not in caplog.text
    assert _IDLE_PROFILE_ID not in caplog.text
    assert _IDLE_PROFILE_ID[:12] not in caplog.text

    # The next pass, with the fault gone, is not locked out.
    database.fail_on = None
    database.idle_ids = [_IDLE_PROFILE_ID]
    _run_retention_pass(main, database, monkeypatch)
    assert _committed(database, "DELETE FROM customer_profiles ")
    assert database.held_locks == set()


def test_profile_sweep_runs_in_its_own_transaction_in_order(monkeypatch, caplog):
    """Section 8, step by step: the profile pass is a separate commit from the
    telemetry pass, under a lock of its own, links are NULLed before the
    profile row goes, and the counts are logged in Turkish with no
    identifiers."""
    import logging

    main = _main()
    caplog.set_level(logging.INFO)
    database = _FakeDatabase()
    taken = []
    original_scalar = _FakeConnection.scalar

    async def recording_scalar(self, statement, params=None):
        taken.append(params["key"])
        return await original_scalar(self, statement, params)

    monkeypatch.setattr(_FakeConnection, "scalar", recording_scalar)
    _run_retention_pass(main, database, monkeypatch)

    assert taken == [main._RETENTION_LOCK_KEY, main._PROFILE_RETENTION_LOCK_KEY]
    assert main._RETENTION_LOCK_KEY != main._PROFILE_RETENTION_LOCK_KEY
    assert database.held_locks == set()

    telemetry = _committed(database, "UPDATE behavior_data")
    profile = _committed(database, "DELETE FROM customer_profiles ")
    assert len(telemetry) == 1 and len(profile) == 1
    assert telemetry != profile, "iki temizlik ayni islemde calisti"

    statements = database.transactions[profile[0]]

    def position(prefix):
        hits = [i for i, s in enumerate(statements) if s.startswith(prefix)]
        assert len(hits) == 1, f"{prefix}: {hits}"
        return hits[0]

    order = [
        position("DELETE FROM customer_profile_vectors"),
        position("SELECT customer_profiles.profile_id"),
        position("UPDATE sessions"),
        position("UPDATE decision_audit"),
        position("UPDATE profile_access_audit"),
        position("DELETE FROM customer_profiles "),
        position("DELETE FROM decision_audit"),
        position("DELETE FROM profile_access_audit"),
    ]
    assert order == sorted(order), f"bolum 8 sirasi bozuldu: {statements}"

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Profil temizligi:")]
    assert lines == ["Profil temizligi: 3 vektor, 1 profil, 2 karar kaydi, 1 erisim kaydi silindi"]
    assert _IDLE_PROFILE_ID not in caplog.text


def test_the_retention_locks_are_transaction_scoped(monkeypatch):
    """Both sweeps must take pg_try_advisory_xact_lock and must issue no
    unlock at all.

    An AsyncSession returns its connection to the pool on commit, so a
    session-scoped lock taken before the sweep's work and released in a
    finally after its commit is released on whatever connection came back
    next. Measured on Postgres 16 with the previous code: the lock was taken
    on backend 2480, the unlock ran on 2481 and returned false, and 2480 kept
    the lock. With that connection pinned out of the pool -- which is what an
    ordinary request does -- the very next _sweep_once() returned (0, 0) and
    the 48-hour-old behavior_data row survived; with the xact form it returned
    (2, 2) and the row was gone. Nothing logs a skipped pass, so this failed
    silently: no raw blanking, no row deletion, no profile, vector or audit
    deletion, against what docs/kvkk-aydinlatma.md and the Demo page promise.

    The fake database above is a single connection and cannot reproduce a
    two-connection leak, so this reads the statements instead."""
    import asyncio
    import pathlib

    main = _main()
    for sweep, key in (
        (main._sweep_once, main._RETENTION_LOCK_KEY),
        (main._sweep_profiles_once, main._PROFILE_RETENTION_LOCK_KEY),
    ):
        seen = []

        class _Recording(_FakeConnection):
            async def scalar(self, statement, params=None):
                seen.append((str(statement), dict(params or {})))
                return await super().scalar(statement, params)

            async def execute(self, statement, params=None):
                seen.append((str(statement), dict(params or {})))
                return await super().execute(statement, params)

        database = _FakeDatabase()
        monkeypatch.setattr(main, "get_sessionmaker", lambda: (lambda: _Recording(database)))
        asyncio.run(sweep())

        lock_sql, lock_params = seen[0]
        assert "pg_try_advisory_xact_lock" in lock_sql, lock_sql
        assert lock_params == {"key": key}
        assert not any("advisory" in sql for sql, _ in seen[1:]), seen
        assert database.held_locks == set()

    # And no unlock survives anywhere in the module: a second call site would
    # leak exactly the same way, and the xact form has nothing to unlock.
    source = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    assert "pg_advisory_unlock" not in source
    assert "pg_try_advisory_lock" not in source


def test_objection_tombstones_are_never_swept(monkeypatch):
    """An objection leaves a tombstone precisely so a refused profile cannot be
    recreated; sweeping it as "idle" would undo the objection. Rendered for
    Postgres with the values bound, so the exclusion is checked against the
    actual literal rather than a placeholder."""
    import asyncio

    from sqlalchemy.dialects import postgresql

    main = _main()
    captured = []

    class _Capturing(_FakeConnection):
        async def execute(self, statement, params=None):
            if str(statement).startswith("SELECT customer_profiles.profile_id"):
                captured.append(
                    str(
                        statement.compile(
                            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                        )
                    )
                )
            return await super().execute(statement, params)

    database = _FakeDatabase()
    monkeypatch.setattr(main, "get_sessionmaker", lambda: (lambda: _Capturing(database)))
    asyncio.run(main._sweep_profiles_once())

    assert captured, "bos profil secimi calismadi"
    assert "customer_profiles.last_seen_at <" in captured[0]
    assert "customer_profiles.consent_basis != 'objected'" in captured[0]
    assert "FOR UPDATE SKIP LOCKED" in captured[0]


def test_telemetry_sweep_failure_does_not_stop_the_profile_sweep(monkeypatch, caplog):
    """The isolation runs both ways: a failing telemetry pass must not keep
    profiles, vectors and audit rows past their retention either."""
    import logging

    main = _main()
    caplog.set_level(logging.INFO)
    database = _FakeDatabase(fail_on="DELETE FROM behavior_data")

    _run_retention_pass(main, database, monkeypatch)

    assert "Saklama temizligi basarisiz" in caplog.text
    assert _committed(database, "DELETE FROM customer_profiles ")
    assert _committed(database, "DELETE FROM decision_audit")
    assert main._PROFILE_RETENTION_LOCK_KEY not in database.held_locks
    # And the failed telemetry pass released its own lock, by ending its
    # transaction: a rollback is the only thing that ends an aborted one.
    assert main._RETENTION_LOCK_KEY not in database.held_locks, "basarisiz temizlik kilidi birakmadi"


def test_the_demo_namespace_lives_on_the_session_clock(monkeypatch):
    """What a demo visitor leaves in the reserved "demo" namespace -- a vector
    learned from their payment, the implicit profile their typed reference
    created, the decision audit rows -- is deleted on the session's clock
    (ROW_RETENTION_HOURS), not after 180 and 90 days: nobody consented on the
    demo page and there is no erasure route into that namespace. The seeded
    synthetic customers keep their history, and a merchant's customer keeps
    the ordinary retention. Run against the in-memory table model, which
    evaluates the sweep's actual WHERE clauses."""
    import asyncio

    main = _main()
    tables = _ProfileTables()
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=main.ROW_RETENTION_HOURS + 1)
    fresh = now - timedelta(hours=1)
    idle_visitor, active_visitor = _pid("demo", "ziyaretci-1"), _pid("demo", "ziyaretci-2")
    seeded, customer = _pid("demo", "sentetik-ayse"), _pid("acme", _REF)
    for pid, merchant, synthetic, seen in (
        (idle_visitor, "demo", False, old),
        (active_visitor, "demo", False, fresh),
        (seeded, "demo", True, now - timedelta(days=30)),
        (customer, "acme", False, old),
    ):
        tables.seed(
            "customer_profiles",
            profile_id=pid,
            merchant_id=merchant,
            is_demo=merchant == "demo",
            is_synthetic=synthetic,
            consent_basis="demo" if merchant == "demo" else "explicit_consent",
            profiling_enabled=True,
            last_seen_at=seen,
        )
    for pid, session_id, created, synthetic in (
        (idle_visitor, "ziyaret-eski", old, False),
        (active_visitor, "ziyaret-yeni", fresh, False),
        (seeded, "tohum-1", now - timedelta(days=40), True),
        (customer, "musteri-1", old, False),
    ):
        tables.seed(
            "customer_profile_vectors",
            profile_id=pid,
            session_id=session_id,
            created_at=created,
            modality="mouse",
            feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
            vec={name: 0.5 for name in FEATURE_NAMES},
            disp={},
            flush_count=5,
            is_synthetic=synthetic,
        )
    for session_id, pid, merchant, decided in (
        ("ziyaret-eski", idle_visitor, "demo", old),
        ("juri-ayse", seeded, "demo", old),
        ("ziyaret-yeni", active_visitor, "demo", fresh),
        ("musteri-1", customer, "acme", old),
    ):
        tables.seed("decision_audit", session_id=session_id, profile_id=pid, merchant_id=merchant, decided_at=decided)
    tables.seed("profile_access_audit", operator_id="denetci-1", endpoint="profile.review", session_id="ziyaret-eski", profile_id=idle_visitor)

    class _SweepDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def scalar(self, statement, params=None):
            assert "pg_try_advisory_xact_lock" in str(statement)
            return True

        async def execute(self, statement, params=None):
            return tables.execute(statement)

        async def commit(self):
            tables.commit()

        async def rollback(self):
            tables.rollback()

    monkeypatch.setattr(main, "get_sessionmaker", lambda: _SweepDB)
    asyncio.run(main._sweep_profiles_once())

    committed = tables.committed
    assert {r["profile_id"] for r in committed["customer_profiles"]} == {active_visitor, seeded, customer}
    assert {r["session_id"] for r in committed["customer_profile_vectors"]} == {"ziyaret-yeni", "tohum-1", "musteri-1"}
    assert {r["session_id"] for r in committed["decision_audit"]} == {"ziyaret-yeni", "musteri-1"}
    [access] = committed["profile_access_audit"]
    assert access["profile_id"] is None and access["session_id"] == "ziyaret-eski"


def test_profile_links_have_no_foreign_key():
    """Spec section 2, item 2. Sessions live 24h and profiles 180 days: a
    foreign key on either link column would let a profile delete abort a
    sweep -- the failure the separate transaction above exists to contain.
    Nothing that points at a profile may hold a constraint on it."""
    _main()
    from database import Base

    tables = Base.metadata.tables
    assert not tables["sessions"].c.profile_id.foreign_keys
    assert not tables["decision_audit"].c.profile_id.foreign_keys
    for name in (
        "customer_profiles",
        "customer_profile_vectors",
        "decision_audit",
        "profile_access_audit",
    ):
        assert not tables[name].foreign_keys, f"{name} bir yabanci anahtar tasiyor"


def test_an_absent_audit_vector_is_sql_null_not_json_null():
    """`WHERE candidate_vec IS NOT NULL` has to mean "this decision kept the
    vector it compared", and `WHERE top_features IS NOT NULL` has to mean "the
    layer had an opinion".

    SQLAlchemy's JSON types serialise Python None as the JSON value `null`
    unless none_as_null is set, and _write_audit_row passes None explicitly on
    every decision without a deviation. Measured on Postgres 16 before this
    was set: after 10 decisions of which 2 deviated, all 10 rows answered
    `candidate_vec IS NOT NULL` true, jsonb_typeof 'object' twice and 'null'
    eight times, and the erasure UPDATE wrote JSON null too -- so an erased
    row looked exactly like a row that never held a vector. The behavioural
    data really was gone, so the KVKK erasure claim held; what did not hold is
    the only query anyone would write to check it.

    This checks the bind processor rather than a database, because the bind
    processor is the thing that was wrong."""
    from sqlalchemy.dialects import postgresql

    _main()
    import database
    from database import Base

    columns = Base.metadata.tables["decision_audit"].c
    for name in ("candidate_vec", "top_features"):
        column = columns[name]
        assert column.nullable, name
        process = column.type.bind_processor(postgresql.dialect())
        assert process(None) is None, f"{name}: None hala JSON null olarak yaziliyor"
        # A value still goes in as JSON, so nothing else about the column moved.
        assert process({"a": 1.0}) not in (None, ""), name

    # And the rows the previous code already wrote are repaired at boot.
    statements = [s for s in database._ADDITIVE_MIGRATIONS if s.startswith("UPDATE decision_audit")]
    assert len(statements) == 2, statements
    for name in ("candidate_vec", "top_features"):
        assert any(
            f"SET {name} = NULL" in s and f"jsonb_typeof({name}) = 'null'" in s for s in statements
        ), name


# --- boot configuration (spec sections 3.1, 3.3, 3.5) --------------------------
#
# These are properties of IMPORTING main, so they are tested by importing it:
# one child interpreter imports main once per configuration (importlib.reload).
# scorer and lstm_model are stubbed in the child because main only needs two
# constants from them at import, and the real modules pull shap and torch --
# ~13s per interpreter for a question about environment parsing.

_MERCHANT_KEY_A = "a" * 16 + "-merchant-key-A-" + "0" * 8
_MERCHANT_KEY_B = "b" * 16 + "-merchant-key-B-" + "1" * 8
_PROFILE_KEY = "p" * 16 + "-profile-key-for-tests-" + "2" * 8
_BOOT_DASHBOARD_KEY = "boot-test-dashboard-key-long-enough-0123"

_BOOT_SCRIPT = r"""
import importlib, json, os, sys, types

feature_names, schema_version, smoothing_window, cases = json.loads(sys.stdin.read())
for module, attrs in (
    ("lstm_model", {"FEATURE_NAMES": feature_names, "FEATURE_SCHEMA_VERSION": schema_version}),
    ("scorer", {"SMOOTHING_WINDOW": smoothing_window}),
):
    stub = types.ModuleType(module)
    stub.__dict__.update(attrs)
    sys.modules[module] = stub

controlled = ("DEBUG", "DEEPCHECK_", "DASHBOARD_KEY", "PROFILE_")
base = {k: v for k, v in os.environ.items() if not k.startswith(controlled)}
results = {}
main = None
for name, env in cases.items():
    os.environ.clear()
    os.environ.update(base)
    os.environ.update(env)
    try:
        if main is None:
            import main
        else:
            importlib.reload(main)
        results[name] = {
            "enabled": main.PROFILE_ENABLED,
            "has_key": main.PROFILE_KEY is not None,
            "merchants": sorted(main.MERCHANT_KEYS),
            "debug": main.DEBUG,
            "layer": main.PROFILE_LAYER,
            "escalation": main.PROFILE_ESCALATION,
            "reviewers": sorted(main.PROFILE_REVIEW_KEYS),
        }
    except RuntimeError as exc:
        results[name] = {"error": str(exc)}
print(json.dumps(results))
"""


def _boot_cases(main) -> dict:
    production = {"DEBUG": "0", "DEEPCHECK_SECRET": "boot-test-secret", "DASHBOARD_KEY": _BOOT_DASHBOARD_KEY}
    local = {"DEBUG": "1"}
    merchants = {"DEEPCHECK_MERCHANT_KEYS": f"acme:{_MERCHANT_KEY_A}, globex:{_MERCHANT_KEY_B}"}
    layer = {"PROFILE_LAYER": "1", "PROFILE_ESCALATION": "1"}
    cases = {}
    for mode, env in (("debug", local), ("production", production)):
        # Nothing profile-related set at all: the shipped default.
        cases[f"{mode}/defaults"] = dict(env)
        cases[f"{mode}/no-key"] = {**env, **merchants, **layer}
        cases[f"{mode}/empty-key"] = {**env, **merchants, **layer, "DEEPCHECK_PROFILE_KEY": "   "}
        cases[f"{mode}/complete"] = {**env, **merchants, **layer, "DEEPCHECK_PROFILE_KEY": _PROFILE_KEY}
        cases[f"{mode}/layer-flag-unset"] = {**env, **merchants, "DEEPCHECK_PROFILE_KEY": _PROFILE_KEY}
        cases[f"{mode}/no-merchants"] = {**env, **layer, "DEEPCHECK_PROFILE_KEY": _PROFILE_KEY}
        cases[f"{mode}/key-is-dev-secret"] = {**env, **merchants, "DEEPCHECK_PROFILE_KEY": main._DEV_SECRET}
        cases[f"{mode}/key-is-a-merchant-key"] = {**env, **merchants, "DEEPCHECK_PROFILE_KEY": _MERCHANT_KEY_A}
        keyed = {**env, **merchants, "DEEPCHECK_PROFILE_KEY": _PROFILE_KEY}
        cases[f"{mode}/review-keys"] = {**keyed, "PROFILE_REVIEW_KEYS": f"denetci-1:{_REVIEW_KEY}"}
        cases[f"{mode}/review-key-is-merchant-key"] = {**keyed, "PROFILE_REVIEW_KEYS": f"denetci-1:{_MERCHANT_KEY_A}"}
        cases[f"{mode}/review-key-is-profile-key"] = {**keyed, "PROFILE_REVIEW_KEYS": f"denetci-1:{_PROFILE_KEY}"}
        cases[f"{mode}/review-key-is-dashboard-key"] = {
            **keyed,
            "PROFILE_REVIEW_KEYS": "denetci-1:" + (_BOOT_DASHBOARD_KEY if mode == "production" else main._DEV_DASHBOARD_KEY),
        }
        malformed = {
            "no-separator": f"acme{_MERCHANT_KEY_A}",
            "uppercase-id": f"Acme:{_MERCHANT_KEY_A}",
            "short-key": "acme:" + _MERCHANT_KEY_A[:31],
            "space-in-key": "acme:" + _MERCHANT_KEY_A[:20] + " " + _MERCHANT_KEY_A[20:],
            "reserved-demo": f"demo:{_MERCHANT_KEY_A}",
            "duplicate-id": f"acme:{_MERCHANT_KEY_A},acme:{_MERCHANT_KEY_B}",
            "shared-key": f"acme:{_MERCHANT_KEY_A},globex:{_MERCHANT_KEY_A}",
            "empty-entry": f"acme:{_MERCHANT_KEY_A},",
            # Both dashboard keys are >= 32 characters, so only the reuse rule
            # can reject these.
            "key-is-dashboard-key": "acme:" + (_BOOT_DASHBOARD_KEY if mode == "production" else main._DEV_DASHBOARD_KEY),
        }
        for label, value in malformed.items():
            cases[f"{mode}/merchants-{label}"] = {**env, "DEEPCHECK_MERCHANT_KEYS": value}
    return cases


_boot_results_cache: dict = {}


def _boot_results() -> dict:
    """Runs the child once per test session and shares the result."""
    if not _boot_results_cache:
        import json
        import subprocess
        import sys
        from pathlib import Path

        import lstm_model

        main = _main()
        payload = json.dumps(
            [list(FEATURE_NAMES), lstm_model.FEATURE_SCHEMA_VERSION, main.scorer.SMOOTHING_WINDOW, _boot_cases(main)]
        )
        completed = subprocess.run(
            [sys.executable, "-c", _BOOT_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        _boot_results_cache.update(json.loads(completed.stdout.strip().splitlines()[-1]))
    return _boot_results_cache


def test_profile_key_has_no_dev_fallback():
    """T2. DEBUG=1 is allowed a published development secret for the token and
    the dashboard; the profile key is not. A pseudonym keyed on a published
    constant is recomputable by anyone from a raw reference, so an unset key
    must leave the layer OFF in DEBUG exactly as it does in production."""
    results = _boot_results()
    for mode in ("debug", "production"):
        for case in ("no-key", "empty-key"):
            result = results[f"{mode}/{case}"]
            assert "error" not in result, f"{mode}/{case}: {result}"
            assert result["debug"] is (mode == "debug")
            assert result["has_key"] is False, f"{mode}/{case}: bir gelistirme anahtarina dusuldu"
            assert result["enabled"] is False, f"{mode}/{case}: anahtarsiz katman acik"
            assert result["merchants"] == ["acme", "globex"]

        # Every one of the three conditions is required, in both modes.
        assert results[f"{mode}/complete"]["enabled"] is True, results[f"{mode}/complete"]
        assert results[f"{mode}/layer-flag-unset"]["enabled"] is False
        assert results[f"{mode}/no-merchants"]["enabled"] is False

        # The key may not be a secret that already exists for another purpose.
        for case in ("key-is-dev-secret", "key-is-a-merchant-key"):
            result = results[f"{mode}/{case}"]
            assert "error" in result, f"{mode}/{case} acilista reddedilmedi: {result}"
            assert "DEEPCHECK_PROFILE_KEY" in result["error"]
            assert _MERCHANT_KEY_A not in result["error"]


def test_malformed_merchant_keys_fail_the_boot_in_every_mode():
    """Section 3.1. A malformed credential list must stop the process at
    deploy time -- in DEBUG too -- rather than surface as 401s at a merchant's
    checkout. And the boot error must not carry the key text: it is the most
    common thing pasted into the wrong place, and boot errors are copied into
    every log a deployment has."""
    results = _boot_results()
    labels = [name.split("/merchants-", 1)[1] for name in results if name.startswith("debug/merchants-")]
    assert len(labels) >= 9
    for mode in ("debug", "production"):
        for label in labels:
            result = results[f"{mode}/merchants-{label}"]
            assert "error" in result, f"{mode}/{label} acilista reddedilmedi: {result}"
            assert "DEEPCHECK_MERCHANT_KEYS" in result["error"]
            for secret in (_MERCHANT_KEY_A, _MERCHANT_KEY_B, _MERCHANT_KEY_A[:31]):
                assert secret not in result["error"], f"{mode}/{label}: hata mesaji anahtari iceriyor"


# --- merchant-authenticated profile endpoints (spec sections 5.1-5.4, 11) -----
#
# Against an in-memory model of the profile tables rather than Postgres, for the
# same reason as the retention tests above. The model INTERPRETS the SQLAlchemy
# statements the handlers build -- WHERE trees, IN subqueries, ON CONFLICT with
# its own WHERE, RETURNING -- instead of matching SQL text, so "the vectors are
# gone" is an assertion about rows rather than about which strings were sent.
# It fails loudly on anything it does not model (an unknown expression, an
# ORDER BY that is not a plain column) instead of quietly ignoring it.

_PROFILE_TABLES = (
    "customer_profiles",
    "customer_profile_vectors",
    "decision_audit",
    "profile_access_audit",
)

_ACME = {"X-Merchant-Id": "acme", "X-Merchant-Key": _MERCHANT_KEY_A}
_GLOBEX = {"X-Merchant-Id": "globex", "X-Merchant-Key": _MERCHANT_KEY_B}
# The shape of a real reference: a phone number. Distinctive enough that a
# substring search over a log or a response body means something.
_REF = "+905551234567"
_OTHER_REF = "+905559876543"


def _pid(merchant: str = "acme", ref: str = _REF) -> str:
    return profiles.derive_profile_id(merchant, ref, _PROFILE_KEY.encode("utf-8"))


def _sql_eval(node, row, tables):
    """Evaluates one SQLAlchemy expression against one row dict."""
    from sqlalchemy.sql import elements, operators, selectable

    if isinstance(node, elements.BindParameter):
        return node.effective_value
    if isinstance(node, elements.Null):
        return None
    if isinstance(node, elements.True_):
        return True
    if isinstance(node, elements.False_):
        return False
    if isinstance(node, (elements.Grouping, selectable.ScalarSelect)):
        return _sql_eval(node.element, row, tables)
    if isinstance(node, selectable.Select):
        return [values[0] for values in tables.select(node)]
    if isinstance(node, elements.BooleanClauseList):
        results = [_sql_eval(clause, row, tables) for clause in node.clauses]
        if node.operator is operators.and_:
            return all(results)
        if node.operator is operators.or_:
            return any(results)
    if isinstance(node, elements.BinaryExpression):
        left = _sql_eval(node.left, row, tables)
        right = _sql_eval(node.right, row, tables)
        if node.operator is operators.is_:
            return left is right
        if node.operator is operators.is_not:
            return left is not right
        if left is None or right is None:
            # Three-valued logic: a comparison with NULL is never true.
            return False
        comparisons = {
            operators.eq: lambda a, b: a == b,
            operators.ne: lambda a, b: a != b,
            operators.lt: lambda a, b: a < b,
            operators.le: lambda a, b: a <= b,
            operators.gt: lambda a, b: a > b,
            operators.ge: lambda a, b: a >= b,
            operators.in_op: lambda a, b: a in b,
            operators.not_in_op: lambda a, b: a not in b,
            # escalation_count + 1: the budget increment is done by the database.
            operators.add: lambda a, b: a + b,
        }
        if node.operator in comparisons:
            return comparisons[node.operator](left, right)
    if isinstance(node, elements.ColumnElement) and getattr(node, "table", None) is not None:
        return row[node.name]
    raise NotImplementedError(f"profil tablo modeli bunu degerlendiremiyor: {node!r}")


class _TableResult:
    def __init__(self, rows=(), rowcount=None):
        self._rows = list(rows)
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _TableResult([row[0] for row in self._rows])

    def scalar_one_or_none(self):
        assert len(self._rows) <= 1
        return self._rows[0] if self._rows else None


class _ProfileTables:
    """Committed rows per table, plus the working copy of the open
    transaction: a statement changes the working copy, commit publishes it,
    rollback discards it -- so "all in one transaction" is observable."""

    def __init__(self, fail_on=None):
        self.committed = {name: [] for name in (*_PROFILE_TABLES, "sessions")}
        self.working = None
        # (kind, table) of every statement this model ran, in order.
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        # (kind, table) that raises the way a constraint violation would.
        self.fail_on = fail_on
        self._next_id = itertools.count(1)

    @staticmethod
    def handles(statement) -> bool:
        from sqlalchemy.sql import dml, selectable

        if isinstance(statement, dml.UpdateBase):
            name = statement.table.name
            # sessions only for UPDATE: the profile link column. The session
            # upsert in /api/analyze stays with _StubDB.
            return name in _PROFILE_TABLES or (name == "sessions" and isinstance(statement, dml.Update))
        if isinstance(statement, selectable.Select):
            froms = statement.get_final_froms()
            return len(froms) == 1 and getattr(froms[0], "name", None) in _PROFILE_TABLES
        return False

    def rows(self, table):
        if self.working is None:
            self.working = copy.deepcopy(self.committed)
        return self.working[table]

    def seed(self, table, **values):
        assert self.working is None, "seed before the first request"
        row = self.with_defaults(table, values)
        self.committed[table].append(row)
        return row

    def commit(self):
        if self.working is not None:
            self.committed = self.working
            self.working = None
        self.commits += 1

    def rollback(self):
        self.working = None
        self.rollbacks += 1

    def with_defaults(self, table, values):
        from sqlalchemy.sql import functions

        from database import Base

        row = {}
        for column in Base.metadata.tables[table].columns:
            if column.name in values:
                row[column.name] = values[column.name]
            elif column.default is not None and column.default.is_scalar:
                row[column.name] = column.default.arg
            elif column.server_default is not None and isinstance(
                getattr(column.server_default, "arg", None), functions.now
            ):
                row[column.name] = datetime.now(timezone.utc)
            elif column.primary_key and column.autoincrement is True:
                row[column.name] = next(self._next_id)
            else:
                row[column.name] = None
        unknown = set(values) - set(row)
        assert not unknown, f"{table} tablosunda olmayan sutunlar: {unknown}"
        return row

    def select(self, statement):
        from sqlalchemy.sql import elements, functions, operators

        table = statement.get_final_froms()[0].name
        where = statement.whereclause
        matched = [row for row in self.rows(table) if where is None or _sql_eval(where, row, self)]
        columns = list(statement.selected_columns)
        if len(columns) == 1 and isinstance(columns[0], functions.count):
            assert not statement._order_by_clauses and statement._limit_clause is None
            [argument] = list(columns[0].clauses)
            if isinstance(argument, elements.UnaryExpression) and argument.operator is operators.distinct_op:
                # COUNT(DISTINCT column): distinct non-NULL values, as SQL counts
                # them -- the breaker's per-customer count depends on both halves.
                name = argument.element.name
                return [(len({row[name] for row in matched if row[name] is not None}),)]
            if getattr(argument, "table", None) is None and getattr(argument, "name", None) == "*":
                return [(len(matched),)]
            raise NotImplementedError(f"profil tablo modeli bu sayimi modellemiyor: {argument!r}")
        # ORDER BY applied last key first, with a stable sort, so the first key
        # wins -- which is what SQL does.
        for clause in reversed(statement._order_by_clauses):
            descending = False
            if isinstance(clause, elements.UnaryExpression):
                if clause.modifier not in (operators.desc_op, operators.asc_op):
                    raise NotImplementedError(f"profil tablo modeli bu siralamayi modellemiyor: {clause!r}")
                descending = clause.modifier is operators.desc_op
                clause = clause.element
            if getattr(clause, "table", None) is None:
                raise NotImplementedError(f"profil tablo modeli bu siralamayi modellemiyor: {clause!r}")
            name = clause.name
            matched.sort(
                key=lambda r: (r[name] is not None, r[name] if r[name] is not None else 0), reverse=descending
            )
        if statement._limit_clause is not None:
            matched = matched[: _sql_eval(statement._limit_clause, {}, self)]
        names = [column.name for column in columns]
        return [tuple(row[name] for name in names) for row in matched]

    def execute(self, statement):
        from sqlalchemy.dialects.postgresql.dml import OnConflictDoUpdate
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.sql import dml
        from sqlalchemy.sql.elements import ClauseElement

        if isinstance(statement, dml.UpdateBase):
            table = statement.table.name
        else:
            table = statement.get_final_froms()[0].name
        kind = type(statement).__name__.lower()
        self.statements.append((kind, table))
        if self.fail_on == (kind, table):
            # A real driver error carries the bound parameters -- here the
            # profile id -- which is what must never reach a log.
            raise IntegrityError(str(statement), statement.compile().params, Exception("violates constraint"))

        rows = self.rows(table)
        if kind == "select":
            return _TableResult(self.select(statement))

        if kind == "insert":
            values = {getattr(k, "name", k): _sql_eval(v, {}, self) for k, v in statement._values.items()}
            clause = statement._post_values_clause
            if clause is not None:
                targets = [getattr(t, "name", t) for t in clause.inferred_target_elements]
            else:
                targets = [c.name for c in statement.table.primary_key.columns]
            existing = next((r for r in rows if all(r[t] == values.get(t) for t in targets)), None)
            affected = None
            if existing is None:
                affected = self.with_defaults(table, values)
                rows.append(affected)
            elif clause is None:
                raise IntegrityError(str(statement), {}, Exception("duplicate key"))
            elif isinstance(clause, OnConflictDoUpdate):
                where = clause.update_whereclause
                if where is None or _sql_eval(where, existing, self):
                    for key, value in clause.update_values_to_set:
                        if isinstance(value, ClauseElement):
                            value = _sql_eval(value, existing, self)
                        existing[getattr(key, "name", key)] = value
                    affected = existing
            returning = [c.name for c in (statement._returning or ())]
            out = [tuple(affected[n] for n in returning)] if affected is not None and returning else []
            return _TableResult(out, rowcount=0 if affected is None else 1)

        where = statement.whereclause
        matched = [r for r in rows if where is None or _sql_eval(where, r, self)]
        if kind == "update":
            assigned = {getattr(k, "name", k) for k in statement._values}
            # SQLAlchemy renders Column.onupdate into a Core UPDATE for every
            # column the statement does not assign -- sessions.last_seen_at is
            # one -- so the model must too, or it hides exactly that bug.
            implicit = [
                c.name for c in statement.table.columns if c.onupdate is not None and c.name not in assigned
            ]
            for row in matched:
                # SET reads the row as it was before the statement.
                new = {getattr(k, "name", k): _sql_eval(v, row, self) for k, v in statement._values.items()}
                new.update({name: datetime.now(timezone.utc) for name in implicit})
                row.update(new)
            returning = [c.name for c in (statement._returning or ())]
            if returning:
                return _TableResult([tuple(row[n] for n in returning) for row in matched])
        elif kind == "delete":
            for row in matched:
                rows.remove(row)
        else:
            raise NotImplementedError(kind)
        return _TableResult(rowcount=len(matched))


def _scorer_tests():
    """test_scorer, for its _StubDB and _stub_session: extended here, not
    copied, so the decision path sees exactly the stub its own tests use."""
    _main()
    import test_scorer

    return test_scorer


_stub_profile_db_classes = []


def _stub_profile_db_class():
    if _stub_profile_db_classes:
        return _stub_profile_db_classes[0]
    base = _scorer_tests()._StubDB

    class _StubProfileDB(base):
        """test_scorer's _StubDB, extended with the profile tables."""

        def __init__(self, *args, tables=None, flushes=(), **kwargs):
            super().__init__(*args, **kwargs)
            self.tables = tables if tables is not None else _ProfileTables()
            # The session's flushes as the profile path's widened read returns
            # them, newest first: dicts carrying measured_mask, client_signals
            # and the twelve feature columns.
            self.flushes = list(flushes)

        async def execute(self, statement, params=None):
            if _ProfileTables.handles(statement):
                self.executed.append(str(statement))
                return self.tables.execute(statement)
            names = [getattr(c, "name", None) for c in getattr(statement, "selected_columns", ())]
            if "measured_mask" in names and "client_signals" in names:
                self.executed.append(str(statement))
                self.tables.statements.append(("select", "behavior_data"))
                limit = statement._limit_clause
                rows = self.flushes[: limit.effective_value] if limit is not None else self.flushes
                return _TableResult([tuple(row[name] for name in names) for row in rows])
            return await super().execute(statement)

        async def scalar(self, statement, params=None):
            if _ProfileTables.handles(statement):
                self.executed.append(str(statement))
                first = self.tables.execute(statement).first()
                return None if first is None else first[0]
            return await super().scalar(statement)

        async def get(self, model, primary_key, **kwargs):
            table = getattr(model, "__tablename__", None)
            if table in _PROFILE_TABLES:
                key = next(iter(model.__table__.primary_key.columns)).name
                row = next((r for r in self.tables.rows(table) if r[key] == primary_key), None)
                return None if row is None else SimpleNamespace(**row)
            return await super().get(model, primary_key)

        def add(self, obj):
            table = getattr(obj, "__tablename__", None)
            if table in _PROFILE_TABLES:
                values = {
                    column.name: getattr(obj, column.name)
                    for column in obj.__table__.columns
                    if getattr(obj, column.name) is not None
                }
                self.tables.statements.append(("insert", table))
                self.tables.rows(table).append(self.tables.with_defaults(table, values))
            super().add(obj)

        async def commit(self):
            self.tables.commit()
            await super().commit()

        async def rollback(self):
            self.tables.rollback()

    _stub_profile_db_classes.append(_StubProfileDB)
    return _StubProfileDB


@pytest.fixture
def api(monkeypatch):
    """main with the profile layer configured by the test, and a way to point
    the app at a stub database. Every setting is restored afterwards, so the
    rest of the suite still runs with the layer off, as a default deployment
    does."""
    main = _main()
    from fastapi.testclient import TestClient

    def configure(*, layer=True, key=True, merchants=True, escalation=False):
        monkeypatch.setattr(
            main, "MERCHANT_KEYS", {"acme": _MERCHANT_KEY_A, "globex": _MERCHANT_KEY_B} if merchants else {}
        )
        monkeypatch.setattr(main, "PROFILE_KEY", _PROFILE_KEY.encode("utf-8") if key else None)
        monkeypatch.setattr(main, "PROFILE_LAYER", layer)
        # Shadow unless a test asks for enforcement, as a first deployment is.
        monkeypatch.setattr(main, "PROFILE_ESCALATION", escalation)
        # The same expression main evaluates at import.
        monkeypatch.setattr(main, "PROFILE_ENABLED", layer and key and merchants)

    def client(db):
        main.app.dependency_overrides[main.get_db] = lambda: db
        return TestClient(main.app)

    def decide(db, extra=None, headers=None):
        # A fresh session id per call: the per-session decision bucket is not
        # what these tests are about.
        session_id = str(uuid.uuid4())
        return client(db).post(
            "/api/decision",
            json={"session_id": session_id, **(extra or {})},
            headers={"X-DeepCheck-Token": main.sign_session(session_id), **(headers or {})},
        )

    def db(**kwargs):
        return _stub_profile_db_class()(**kwargs)

    def allow_session():
        return _scorer_tests()._stub_session(12.0)

    configure()
    main._rate_hits.clear()
    # The breaker's per-worker cache would otherwise carry one test's count
    # into the next.
    breaker = dict(main._profile_breaker)
    main._profile_breaker.update(checked_at=None, count=0, warned_window=None)
    try:
        yield SimpleNamespace(
            main=main, configure=configure, client=client, decide=decide, db=db, allow_session=allow_session
        )
    finally:
        main.app.dependency_overrides.clear()
        main._rate_hits.clear()
        main._profile_breaker.update(breaker)


def _profile_writes(tables, names=("customer_profiles", "customer_profile_vectors", "sessions")):
    return [s for s in tables.statements if s[0] != "select" and s[1] in names]


def test_customer_ref_requires_a_merchant_credential(api):
    """T13. A customer reference is asserted by the merchant, so it is only
    accepted with the merchant's credential -- otherwise anyone holding a
    session token names any customer. Missing is 400 and wrong is 401, both
    BEFORE a single profile query, and loud whether or not the layer is on."""
    baseline = api.decide(api.db(session=api.allow_session()))
    assert baseline.status_code == 200, baseline.text

    missing = "Musteri referansi icin satici kimlik dogrulamasi gerekli"
    cases = [
        ({}, 400, missing),
        ({"X-Merchant-Id": "acme"}, 400, missing),
        ({"X-Merchant-Key": _MERCHANT_KEY_A}, 400, missing),
        ({"X-Merchant-Id": "acme", "X-Merchant-Key": _MERCHANT_KEY_B}, 401, "Yetkisiz satici"),
        ({"X-Merchant-Id": "initech", "X-Merchant-Key": _MERCHANT_KEY_A}, 401, "Yetkisiz satici"),
        # Non-ASCII in a header must be a 401, not a TypeError inside
        # compare_digest and a 500.
        ({"X-Merchant-Id": "acme", "X-Merchant-Key": ("anahtar-" + "ş" * 30).encode("utf-8")}, 401, "Yetkisiz satici"),
    ]
    for layer in (True, False):
        api.configure(layer=layer)
        for headers, status, detail in cases:
            db = api.db(session=api.allow_session())
            response = api.decide(db, {"customer_ref": _REF}, headers)
            assert response.status_code == status, (layer, headers, response.text)
            assert response.json()["detail"] == detail
            assert _REF not in response.text
            assert db.tables.statements == [], f"kimlik dogrulamadan once profil sorgusu: {db.tables.statements}"
            assert not any(t in text for text in db.executed for t in _PROFILE_TABLES)

    # Without a reference the merchant headers are not even read: the request
    # is the one that existed before references did.
    api.configure()
    unread = api.decide(api.db(session=api.allow_session()), {}, {"X-Merchant-Id": "acme", "X-Merchant-Key": "yanlis"})
    assert unread.status_code == 200 and unread.json() == baseline.json()

    # Layer off, valid credential: the reference is ignored entirely -- nothing
    # read, nothing written, no bucket charged, the decision unchanged.
    api.configure(layer=False)
    db = api.db(session=api.allow_session())
    ignored = api.decide(db, {"customer_ref": _REF}, _ACME)
    assert ignored.status_code == 200 and ignored.json() == baseline.json()
    assert db.tables.statements == []
    assert ("profile", "acme") not in api.main._rate_hits


def test_decision_never_creates_a_profile(api):
    """T14. Only the consent endpoint creates a profile row. An unknown
    reference on a decision -- whatever the verdict -- creates nothing, and
    the decision is the one it would have been without the reference. (An
    audit row is not a profile, so decision_audit is not what this watches.)"""
    ts = _scorer_tests()
    for score, label in ((12.0, "Gerçek Kullanıcı"), (72.0, "Yüksek Risk"), (95.0, "Bot Tespit Edildi")):
        baseline = api.decide(api.db(session=ts._stub_session(score, label)))
        db = api.db(session=ts._stub_session(score, label))
        response = api.decide(
            db,
            {"customer_ref": _REF, "risk_context": {"amount_band": "high", "new_beneficiary": True}},
            _ACME,
        )
        assert response.status_code == 200, response.text
        assert response.json() == baseline.json(), f"referans karari degistirdi ({score})"
        assert _profile_writes(db.tables) == [], f"karar profil tablosuna yazdi: {db.tables.statements}"
        assert db.tables.committed["customer_profiles"] == []
        assert db.tables.committed["customer_profile_vectors"] == []
        assert not any(
            isinstance(obj, (api.main.CustomerProfile, api.main.CustomerProfileVector)) for obj in db.added
        )


def test_consent_is_the_only_way_a_profile_is_created(api):
    """Section 5.2. 503 while the layer is off, 401 without the merchant's
    credential, 400 for a basis that is not a lawful basis for biometric data,
    and then exactly one row per (merchant, customer) -- with the pseudonym as
    its key and the raw reference nowhere."""
    tables = _ProfileTables()
    client = api.client(api.db(tables=tables))

    api.configure(layer=False)
    off = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "explicit_consent"}, headers=_ACME)
    assert off.status_code == 503 and off.json()["detail"] == "Musteri profili katmani bu dagitimda kapali"
    assert tables.statements == []

    api.configure()
    wrong = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "explicit_consent"}, headers={**_ACME, "X-Merchant-Key": _MERCHANT_KEY_B})
    assert wrong.status_code == 401
    for basis in ("legitimate_interest", "objected", "demo", "none", ""):
        bad = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": basis}, headers=_ACME)
        assert bad.status_code == 400 and bad.json()["detail"] == "Gecersiz hukuki dayanak", basis
    assert tables.statements == []

    created = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "explicit_consent"}, headers=_ACME)
    assert created.status_code == 201 and created.json() == {"status": "enabled"}
    [row] = tables.committed["customer_profiles"]
    assert row["profile_id"] == _pid()
    assert row["merchant_id"] == "acme"
    assert row["profiling_enabled"] is True
    assert row["consent_basis"] == "explicit_consent"
    assert row["consent_recorded_at"] is not None
    assert row["key_version"] == api.main.PROFILE_KEY_VERSION
    assert row["feature_schema_version"] == profiles.FEATURE_SCHEMA_VERSION
    assert row["is_demo"] is False and row["erased_at"] is None

    again = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "contract_necessity"}, headers=_ACME)
    assert again.status_code == 201
    [row] = tables.committed["customer_profiles"]
    assert row["consent_basis"] == "contract_necessity", "tekrarlanan onay satiri guncellemedi"

    other_merchant = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "explicit_consent"}, headers=_GLOBEX)
    assert other_merchant.status_code == 201
    ids = {r["profile_id"]: r["merchant_id"] for r in tables.committed["customer_profiles"]}
    assert ids == {_pid(): "acme", _pid("globex"): "globex"}, "iki satici ayni musteri profilini paylasiyor"
    assert _REF not in repr(tables.committed), "ham musteri referansi veritabanina yazildi"


def _seed_customer(tables, merchant="acme", ref=_REF, sessions=("s-1", "s-2", "s-3"), basis="explicit_consent"):
    pid = _pid(merchant, ref)
    tables.seed(
        "customer_profiles",
        profile_id=pid,
        merchant_id=merchant,
        consent_basis=basis,
        profiling_enabled=basis != "objected",
        escalation_count=2,
        consecutive_passed_escalations=1,
    )
    for session_id in sessions:
        tables.seed(
            "customer_profile_vectors",
            profile_id=pid,
            session_id=session_id,
            modality="mouse",
            feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
            vec={name: 0.5 for name in FEATURE_NAMES},
            disp={name: 0.1 for name in FEATURE_NAMES},
            flush_count=4,
        )
        tables.seed("sessions", id=f"{merchant}-{session_id}", profile_id=pid, profile_learned=True)
        tables.seed(
            "decision_audit",
            session_id=f"{merchant}-{session_id}",
            profile_id=pid,
            action="verify",
            reason="profile_deviation",
            candidate_vec={name: 1.5 for name in FEATURE_NAMES},
        )
    return pid


def test_erasure_removes_everything(api, caplog):
    """T21. Vectors and the profile row deleted, every link to the profile
    NULLed, all in ONE transaction, with an access-audit row that does not
    itself keep the pseudonym. Other customers and other merchants untouched.
    Erasure is not objection: a later consent may start again.

    "Every link" includes an earlier REVIEW of this customer: the review
    endpoint writes the profile id into profile_access_audit, kept 365 days,
    and through its session id that row re-linked the erased customer to the
    reviewed decision's verdict, deviation and top features."""
    import logging

    caplog.set_level(logging.DEBUG)
    tables = _ProfileTables()
    pid = _seed_customer(tables)
    other = _seed_customer(tables, ref=_OTHER_REF, sessions=("s-9",))
    globex = _seed_customer(tables, merchant="globex", sessions=("s-1",))
    tables.seed("profile_access_audit", operator_id="denetci-1", endpoint="profile.review", session_id="acme-s-1", profile_id=pid)
    tables.seed("profile_access_audit", operator_id="denetci-1", endpoint="profile.review", session_id="acme-s-9", profile_id=other)
    client = api.client(api.db(tables=tables))

    response = client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME)
    assert response.status_code == 204 and response.content == b""
    assert tables.commits == 1, "silme tek bir islemde yapilmadi"

    committed = tables.committed
    assert {r["profile_id"] for r in committed["customer_profiles"]} == {other, globex}
    assert {r["profile_id"] for r in committed["customer_profile_vectors"]} == {other, globex}
    # The session and audit rows themselves are not the profile: they stay,
    # and only their link to the person goes.
    assert len(committed["sessions"]) == 5 and len(committed["decision_audit"]) == 5
    assert pid not in {r["profile_id"] for r in committed["sessions"]}
    assert pid not in {r["profile_id"] for r in committed["decision_audit"]}
    assert sum(r["profile_id"] is None for r in committed["decision_audit"]) == 3
    # The compared session vectors on those rows are the customer's
    # behavioural data and go with the link; other customers' stay.
    assert all(r["candidate_vec"] is None for r in committed["decision_audit"] if r["profile_id"] is None)
    assert all(r["candidate_vec"] is not None for r in committed["decision_audit"] if r["profile_id"] is not None)

    review, other_review, audit = committed["profile_access_audit"]
    assert audit["operator_id"] == "acme" and audit["endpoint"] == "profile.erase"
    assert audit["profile_id"] is None and audit["session_id"] is None
    # The review stays as an accountability record -- who looked, at which
    # session, when -- without the pseudonym. Another customer's keeps it.
    assert (review["operator_id"], review["endpoint"], review["session_id"]) == ("denetci-1", "profile.review", "acme-s-1")
    assert review["profile_id"] is None, "silinen musterinin takma adi erisim kaydinda kaldi"
    assert other_review["profile_id"] == other

    assert pid not in repr(committed)
    assert pid not in caplog.text and pid[:12] not in caplog.text and _REF not in caplog.text

    recreated = client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "explicit_consent"}, headers=_ACME)
    assert recreated.status_code == 201, "silmeden sonra yeni onay reddedildi"
    fresh = next(r for r in tables.committed["customer_profiles"] if r["profile_id"] == pid)
    assert fresh["escalation_count"] == 0 and fresh["consecutive_passed_escalations"] == 0


def test_objection_survives_erasure(api):
    """T22. An objection deletes the same data as erasure but leaves a
    tombstone, and the tombstone is what refuses every later consent -- also
    after a later erasure, and also for a customer who never had a profile."""
    tables = _ProfileTables()
    pid = _seed_customer(tables)
    tables.seed("profile_access_audit", operator_id="denetci-1", endpoint="profile.review", session_id="acme-s-1", profile_id=pid)
    db = api.db(session=api.allow_session(), tables=tables)
    client = api.client(db)

    def consent(basis="explicit_consent", ref=_REF):
        return client.post("/api/profile/consent", json={"customer_ref": ref, "basis": basis}, headers=_ACME)

    objected = client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "object"}, headers=_ACME)
    assert objected.status_code == 204

    def tombstone(profile_id=pid):
        rows = [r for r in tables.committed["customer_profiles"] if r["profile_id"] == profile_id]
        assert len(rows) == 1, "itiraz kaydi yok"
        return rows[0]

    row = tombstone()
    assert row["consent_basis"] == "objected" and row["profiling_enabled"] is False
    assert row["erased_at"] is not None
    assert row["escalation_count"] == 0 and row["consecutive_passed_escalations"] == 0
    assert [r for r in tables.committed["customer_profile_vectors"] if r["profile_id"] == pid] == []
    assert pid not in {r["profile_id"] for r in tables.committed["sessions"]}
    # The tombstone keeps the pseudonym -- it is what refuses a new consent --
    # but nothing else does: the same unlinks as an erasure.
    assert pid not in {r["profile_id"] for r in tables.committed["decision_audit"]}
    assert pid not in {r["profile_id"] for r in tables.committed["profile_access_audit"]}

    refused = consent()
    assert refused.status_code == 409 and refused.json()["detail"] == "Bu musteri profillemeye itiraz etti"
    assert tombstone()["consent_basis"] == "objected", "onay itirazin uzerine yazdi"

    erased = client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME)
    assert erased.status_code == 204
    assert tombstone()["consent_basis"] == "objected", "silme itiraz kaydini da sildi"
    assert consent("contract_necessity").status_code == 409

    # An objection needs no profile to exist first.
    never_profiled = _pid(ref=_OTHER_REF)
    assert client.post("/api/profile/erase", json={"customer_ref": _OTHER_REF, "mode": "object"}, headers=_ACME).status_code == 204
    assert tombstone(never_profiled)["consent_basis"] == "objected"
    assert consent(ref=_OTHER_REF).status_code == 409

    # And a decision naming the objected customer profiles nothing.
    baseline = api.decide(api.db(session=api.allow_session()))
    before = len(tables.statements)
    decided = api.decide(db, {"customer_ref": _REF}, _ACME)
    assert decided.status_code == 200 and decided.json() == baseline.json()
    assert _profile_writes(SimpleNamespace(statements=tables.statements[before:])) == []


def test_erasure_response_is_uniform(api):
    """T23. 204 with an empty body whether or not the customer had a profile,
    in both modes: the answer must not be an oracle over which of a merchant's
    customers are profiled."""
    for mode in ("erase", "object"):
        tables = _ProfileTables()
        _seed_customer(tables)
        client = api.client(api.db(tables=tables))
        known = client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": mode}, headers=_ACME)
        unknown = client.post("/api/profile/erase", json={"customer_ref": _OTHER_REF, "mode": mode}, headers=_ACME)
        assert known.status_code == unknown.status_code == 204, mode
        assert known.content == unknown.content == b"", mode
        assert dict(known.headers) == dict(unknown.headers), mode
        # Idempotent: the same request again is the same answer.
        repeat = client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": mode}, headers=_ACME)
        assert repeat.status_code == 204 and dict(repeat.headers) == dict(known.headers)


def test_disputed_outcome_unlearns_the_session(api):
    """T24. `disputed` deletes that session's vector -- an exact unlearn, and
    the reference count drops -- `settled` marks it; both only within the
    calling merchant's profiles, and 204 whether or not anything matched."""
    tables = _ProfileTables()
    acme = _seed_customer(tables, sessions=("s-1", "s-2", "s-3"))
    globex = _seed_customer(tables, merchant="globex", sessions=("s-1",))
    client = api.client(api.db(tables=tables))

    def vectors(pid):
        return {r["session_id"]: r for r in tables.committed["customer_profile_vectors"] if r["profile_id"] == pid}

    def report(session_id, outcome, headers=_ACME):
        return client.post("/api/outcome", json={"session_id": session_id, "outcome": outcome}, headers=headers)

    assert len(vectors(acme)) == 3
    before = len(tables.statements)
    disputed = report("s-1", "disputed")
    assert disputed.status_code == 204 and disputed.content == b""
    assert set(vectors(acme)) == {"s-2", "s-3"}, "itiraz edilen oturum profilden cikarilmadi"
    # The profile row is locked (UPDATE) before the vector goes, as learning
    # locks it before choosing what to evict: the two cannot interleave.
    writes = [s for s in tables.statements[before:] if s[0] != "select"]
    assert writes == [("update", "customer_profiles"), ("delete", "customer_profile_vectors")], writes
    assert set(vectors(globex)) == {"s-1"}, "baska saticinin vektoru silindi"

    assert report("s-2", "settled").status_code == 204
    assert vectors(acme)["s-2"]["outcome"] == "settled"
    assert vectors(acme)["s-3"]["outcome"] == "pending"

    # Another merchant cannot unlearn this merchant's customer.
    assert report("s-2", "disputed", _GLOBEX).status_code == 204
    assert set(vectors(acme)) == {"s-2", "s-3"}

    nothing = report("hic-profillenmemis", "disputed")
    assert nothing.status_code == 204 and dict(nothing.headers) == dict(disputed.headers)

    bad = report("s-3", "chargeback")
    assert bad.status_code == 400 and bad.json()["detail"] == "Gecersiz islem sonucu"
    assert report("s-3", "disputed", {**_ACME, "X-Merchant-Key": _MERCHANT_KEY_B}).status_code == 401
    assert set(vectors(acme)) == {"s-2", "s-3"}


def test_erasure_does_not_depend_on_the_layer_flag(api):
    """Switching the layer off must not switch off a data subject's erasure,
    objection or a merchant's dispute. What they cannot do without is the
    profile key (no id can be derived), and then the answer is 503 -- never a
    204 confirming an erasure that did not happen."""
    tables = _ProfileTables()
    pid = _seed_customer(tables)
    _seed_customer(tables, ref=_OTHER_REF, sessions=("s-7",))
    client = api.client(api.db(tables=tables))

    api.configure(layer=False)
    assert client.post("/api/outcome", json={"session_id": "s-7", "outcome": "disputed"}, headers=_ACME).status_code == 204
    assert all(r["session_id"] != "s-7" for r in tables.committed["customer_profile_vectors"])
    assert client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME).status_code == 204
    assert all(r["profile_id"] != pid for r in tables.committed["customer_profiles"])
    assert client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "object"}, headers=_ACME).status_code == 204
    assert any(r["profile_id"] == pid and r["consent_basis"] == "objected" for r in tables.committed["customer_profiles"])

    api.configure(layer=False, key=False)
    before = len(tables.statements)
    no_key = client.post("/api/profile/erase", json={"customer_ref": _OTHER_REF, "mode": "erase"}, headers=_ACME)
    assert no_key.status_code == 503
    assert no_key.json()["detail"] == "Profil anahtari tanimli olmadigi icin silme islemi yapilamiyor"
    assert len(tables.statements) == before

    api.configure(layer=False, merchants=False)
    assert client.post("/api/profile/erase", json={"customer_ref": _OTHER_REF, "mode": "erase"}, headers=_ACME).status_code == 401


def test_customer_ref_never_reaches_a_response_or_a_log(api, caplog):
    """T26. An invalid reference is a 400 with a fixed detail on every
    endpoint that takes one, and a 422 anywhere carries no `input` or `ctx` --
    so the raw identifier appears in no response body and no log line."""
    import logging

    caplog.set_level(logging.DEBUG)
    marker = "musteri-7d1f-ref"
    invalid = [marker * 13, marker + "ş", marker + "\n", marker + "\x00", ""]
    assert len(marker * 13) > 200
    client = api.client(api.db(session=api.allow_session()))

    def assert_generic(response):
        assert response.status_code == 400, response.text
        assert response.json() == {"detail": "Gecersiz musteri referansi"}
        assert marker not in response.text

    for ref in invalid:
        assert_generic(api.decide(api.db(session=api.allow_session()), {"customer_ref": ref}, _ACME))
        assert_generic(client.post("/api/profile/consent", json={"customer_ref": ref, "basis": "explicit_consent"}, headers=_ACME))
        assert_generic(client.post("/api/profile/erase", json={"customer_ref": ref, "mode": "erase"}, headers=_ACME))

    def assert_stripped(response):
        assert response.status_code == 422, response.text
        assert marker not in response.text, f"422 govdesi referansi iceriyor: {response.text}"
        for error in response.json()["detail"]:
            assert "input" not in error and "ctx" not in error, error

    valid = marker + "-gecerli"
    assert_stripped(client.post("/api/profile/consent", json={"customer_ref": [valid], "basis": "explicit_consent"}, headers=_ACME))
    assert_stripped(client.post("/api/profile/consent", json={"customer_ref": {"telefon": valid}}, headers=_ACME))
    assert_stripped(client.post("/api/profile/erase", json={"customer_ref": valid}, headers=_ACME))
    session_id = str(uuid.uuid4())
    token = {"X-DeepCheck-Token": api.main.sign_session(session_id), **_ACME}
    assert_stripped(client.post("/api/decision", json={"session_id": session_id, "customer_ref": valid, "risk_context": {"amount_band": valid}}, headers=token))
    assert_stripped(client.post("/api/decision", json={"session_id": valid * 20, "customer_ref": valid}, headers=token))
    assert_stripped(client.post("/api/decision", content=b'{"session_id": "' + valid.encode() + b'", ', headers={**token, "Content-Type": "application/json"}))

    assert marker not in caplog.text


def test_a_failed_profile_write_is_logged_without_identifiers(api, caplog):
    """Section 11.1. A database error carries the statement's bound
    parameters -- the profile id -- in its message, so a failure is one
    Turkish line naming the exception class, the transaction is rolled back
    as a whole, and the client gets a generic 503."""
    import logging

    caplog.set_level(logging.DEBUG)
    tables = _ProfileTables(fail_on=("delete", "customer_profile_vectors"))
    pid = _seed_customer(tables)
    client = api.client(api.db(tables=tables))

    response = client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME)
    assert response.status_code == 503
    assert response.json() == {"detail": "Profil islemi su anda tamamlanamadi, lutfen tekrar deneyin"}
    assert tables.rollbacks == 1 and tables.commits == 0
    assert any(r["profile_id"] == pid for r in tables.committed["customer_profiles"])

    failures = [r for r in caplog.records if "kaydedilemedi" in r.getMessage()]
    assert len(failures) == 1 and failures[0].levelno == logging.ERROR
    assert "IntegrityError" in failures[0].getMessage()
    for leaked in (pid, pid[:12], _REF):
        assert leaked not in caplog.text
        assert leaked not in response.text


def test_profile_buckets_never_refuse_a_checkout(api, monkeypatch):
    """Section 11.3, as changed. Consent, erase and outcome share one
    per-merchant bucket and answer 429 past it. Decisions naming a customer
    have a bucket PER CUSTOMER, and exhausting it never refuses the decision:
    the profile is not read, and when enforcing the answer is step-up (told to
    the client as step_up), unlocked by a fresh step-up like any other verify.

    It used to be one bucket per MERCHANT that answered 429 for the whole
    decision, bot verdict included: one customer pressing pay took every
    other customer of that merchant down with it. And an abstention in its
    place would have been worse -- a victim's account pressed past the limit
    would then be judged without its profile, retries turned into approval."""
    main = api.main
    monkeypatch.setitem(main.RATE_LIMITS, "profile_admin", (2, 60))
    monkeypatch.setitem(main.RATE_LIMITS, "profile", (1, 3600))
    client = api.client(api.db(session=api.allow_session()))

    for _ in range(2):
        assert client.post("/api/outcome", json={"session_id": "s", "outcome": "settled"}, headers=_ACME).status_code == 204
    assert client.post("/api/outcome", json={"session_id": "s", "outcome": "settled"}, headers=_ACME).status_code == 429
    assert client.post("/api/profile/consent", json={"customer_ref": _REF, "basis": "explicit_consent"}, headers=_ACME).status_code == 429
    assert client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME).status_code == 429
    assert client.post("/api/outcome", json={"session_id": "s", "outcome": "settled"}, headers=_GLOBEX).status_code == 204

    for escalation in (False, True):
        api.configure(escalation=escalation)
        main._rate_hits.clear()
        tables = _ProfileTables()
        pid = _seed_profile(tables)

        def decide(ref=_REF, headers=_ACME, value=_MATCHING, verified=False):
            session_id = str(uuid.uuid4())
            db = _customer_session(api, tables, session_id, value=value)
            if verified:
                db.session.verified_at = main.utcnow() - timedelta(seconds=5)
            before = len(tables.statements)
            response = _post_decision(api, db, session_id, ref=ref, headers=headers)
            assert response.status_code == 200, response.text
            return response.json(), tables.statements[before:], session_id

        first, _, _ = decide()
        assert (first["action"], first["reason"]) == ("allow", "score")

        # The same customer again, matching as before: nothing about the
        # profile is read, and the decision is never refused.
        second, statements, session_id = decide()
        assert ("select", "customer_profiles") not in statements
        assert ("select", "customer_profile_vectors") not in statements
        [row] = _audit_rows(tables, session_id)
        assert row["profile_state"] == "rate_limited" and row["profile_id"] is None
        assert second["risk_score"] == first["risk_score"] and second["label"] == first["label"]
        if escalation:
            assert (second["action"], second["reason"]) == ("verify", "step_up"), second
            assert second["message"] == main.REASON_MESSAGES["step_up"]
            assert (row["reason"], row["public_reason"]) == ("profile_rate_limited", "step_up")
            unlocked, _, _ = decide(verified=True)
            assert (unlocked["action"], unlocked["reason"]) == ("allow", "verified")
        else:
            # Shadow: recorded, and the decision is exactly the plain one.
            assert second == first, second
            assert row["reason"] == "score" and row["shadow"] is True

        # Other customers -- of this merchant and of another -- are untouched,
        # and a decision naming nobody never meets the bucket.
        other, _, _ = decide(ref=_OTHER_REF)
        assert (other["action"], other["reason"]) == ("allow", "score"), other
        globex, _, _ = decide(headers=_GLOBEX)
        assert (globex["action"], globex["reason"]) == ("allow", "score"), globex
        assert api.decide(api.db(session=api.allow_session())).status_code == 200
        assert ("profile", pid) in main._rate_hits and ("profile", "acme") not in main._rate_hits
        assert not any(_REF in key for _, key in main._rate_hits), "ham referans hiz sinirlayicida tutuluyor"

    # The demo charge is limited the same way, per demo customer.
    api.configure(escalation=True)
    main._rate_hits.clear()
    tables = _ProfileTables()
    _seed_profile(tables, merchant="demo", ref=_DEMO_REF)
    statuses = []
    for _ in range(2):
        session_id = str(uuid.uuid4())
        statuses.append(_post_charge(api, _customer_session(api, tables, session_id, value=_MATCHING), session_id).json()["status"])
    assert statuses == ["charged", "declined"], statuses

    api.configure(layer=False)
    main._rate_hits.clear()
    for _ in range(3):
        assert api.decide(api.db(session=api.allow_session()), {"customer_ref": _REF}, _ACME).status_code == 200
    assert not any(bucket == "profile" for bucket, _ in main._rate_hits)


def test_unlinking_a_profile_never_refreshes_session_freshness(api, monkeypatch):
    """Session.last_seen_at has onupdate=now(), and SQLAlchemy applies it to a
    plain Core UPDATE: `UPDATE sessions SET profile_id = NULL` compiles to
    `SET last_seen_at=now(), profile_id=NULL`. That column is the freshness
    rule /api/decision reads, so an erasure (or the retention sweep) would make
    a customer's stale sessions look current again. Checked on both paths."""
    import asyncio

    from sqlalchemy import update
    from sqlalchemy.dialects import postgresql

    main = api.main
    tables = _ProfileTables()
    pid = _seed_customer(tables, sessions=("s-1",))
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    for row in tables.committed["sessions"]:
        row["last_seen_at"] = stale

    # The model sees the bug when it is there, so the assertion below means
    # something.
    naive = _ProfileTables()
    naive.committed["sessions"] = copy.deepcopy(tables.committed["sessions"])
    naive.execute(update(main.Session).where(main.Session.profile_id == pid).values(profile_id=None))
    assert naive.rows("sessions")[0]["last_seen_at"] > stale

    client = api.client(api.db(tables=tables))
    assert client.post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME).status_code == 204
    [session] = tables.committed["sessions"]
    assert session["profile_id"] is None
    assert session["last_seen_at"] == stale, "silme oturumun tazeligini yeniledi"

    # The retention sweep's unlink, rendered for Postgres exactly as it runs.
    rendered = []

    class _Rendering(_FakeConnection):
        async def execute(self, statement, params=None):
            if str(statement).startswith("UPDATE sessions"):
                rendered.append(str(statement.compile(dialect=postgresql.dialect())))
            return await super().execute(statement, params)

    database = _FakeDatabase()
    monkeypatch.setattr(main, "get_sessionmaker", lambda: (lambda: _Rendering(database)))
    asyncio.run(main._sweep_profiles_once())
    assert rendered, "temizlik oturum baglantisini kaldirmadi"
    for sql in rendered:
        assert "now()" not in sql, sql
        assert "last_seen_at=sessions.last_seen_at" in sql, sql


# --- the decision path (spec sections 6.5-6.7, 7) -------------------------------
#
# Every fixture below is SYNTHETIC: a "customer" is _reference_set(), a fixed
# centre plus per-session noise, and a "deviating" session sits a full unit away
# on every feature. Nothing here says how often a real customer deviates -- that
# needs real customers, which this project does not have. What these tests pin
# is the CONTRACT: whatever the statistic says, the layer may only ever turn
# allow or warn into verify, and a fresh step-up is what lets the customer
# through.

_DEVIATING = 1.5
_MATCHING = 0.5
_DEMO_REF = "demo-musteri-1"


def _session_flushes(value=_MATCHING, n=5, pointer="pointer_mouse", mask=_ALL_MEASURED):
    """A session's flushes, newest first, as the widened decision read returns
    them. `pointer` names the ClientSignals counter that sets the modality."""
    rows = []
    for _ in range(n):
        row = {name: value for name in FEATURE_NAMES}
        row["measured_mask"] = mask
        row["client_signals"] = {pointer: 12} if pointer else {}
        rows.append(row)
    return rows


def _seed_profile(
    tables,
    *,
    merchant="acme",
    ref=_REF,
    n=20,
    modality="mouse",
    spread=0.08,
    probation_slots=0,
    seed=5,
    **profile,
):
    """A consented profile with n reference vectors, all learned on earlier
    days (the per-day learning cap counts today's), oldest first."""
    main = _main()
    pid = _pid(merchant, ref)
    row = dict(
        profile_id=pid,
        merchant_id=merchant,
        consent_basis="demo" if merchant == "demo" else "explicit_consent",
        is_demo=merchant == "demo",
        profiling_enabled=True,
        key_version=main.PROFILE_KEY_VERSION,
        feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
        escalation_count=0,
        consecutive_passed_escalations=0,
    )
    row.update(profile)
    tables.seed("customer_profiles", **row)
    start = datetime.now(timezone.utc) - timedelta(days=40)
    references = _reference_set(n, seed=seed, spread=spread)
    for index, vec in enumerate(references):
        tables.seed(
            "customer_profile_vectors",
            profile_id=pid,
            session_id=f"ref-{merchant}-{index}",
            created_at=start + timedelta(hours=index),
            modality=modality,
            feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
            vec=vec,
            disp={name: 0.01 for name in FEATURE_NAMES},
            flush_count=5,
            # The NEWEST slots are the probation ones, so a size-only eviction
            # would take a clean vector and a probation-first one would not.
            probation=index >= n - probation_slots,
        )
    return pid


def _post_decision(api, db, session_id, ref=_REF, headers=_ACME, extra=None):
    body = {"session_id": session_id, **(extra or {})}
    sent = {"X-DeepCheck-Token": api.main.sign_session(session_id)}
    if ref is not None:
        body["customer_ref"] = ref
        sent.update(headers)
    return api.client(db).post("/api/decision", json=body, headers=sent)


def _post_charge(api, db, session_id, ref=_DEMO_REF):
    body = {"session_id": session_id, "amount": 49.9}
    if ref is not None:
        body["customer_ref"] = ref
    return api.client(db).post(
        "/api/demo/charge", json=body, headers={"X-DeepCheck-Token": api.main.sign_session(session_id)}
    )


def _post_verify(api, db, session_id):
    return api.client(db).post(
        "/api/demo/verify",
        json={"session_id": session_id, "code": api.main.DEMO_VERIFY_CODE},
        headers={"X-DeepCheck-Token": api.main.sign_session(session_id)},
    )


def _audit_rows(tables, session_id):
    return [r for r in tables.committed["decision_audit"] if r["session_id"] == session_id]


def _vectors(tables, pid, **match):
    return [
        r
        for r in tables.committed["customer_profile_vectors"]
        if r["profile_id"] == pid and all(r[k] == v for k, v in match.items())
    ]


def _profile_row(tables, pid):
    [row] = [r for r in tables.committed["customer_profiles"] if r["profile_id"] == pid]
    return row


def _customer_session(api, tables, session_id, *, value=_DEVIATING, score=12.0, label="Gerçek Kullanıcı", **kwargs):
    """A stub database for one customer session: its own session object (so a
    step-up belongs to it), its flushes, and the shared profile tables -- plus
    the sessions row the learning step links."""
    rows = tables.working if tables.working is not None else tables.committed
    if not any(r["id"] == session_id for r in rows["sessions"]):
        rows["sessions"].append(tables.with_defaults("sessions", {"id": session_id}))
    session = _scorer_tests()._stub_session(score, label)
    return api.db(session=session, tables=tables, flushes=_session_flushes(value), **kwargs)


def test_step_up_unlocks_a_profile_escalation(api):
    """T10, the verify-loop regression. The profile check sits inside the
    evidence path, BEFORE _apply_step_up, so a customer who has already passed
    step-up in this session is let through instead of being asked again:
    challenge, correct code, challenge was the measured demo loop."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    _seed_profile(tables)
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)

    first = _post_decision(api, db, session_id)
    assert first.status_code == 200, first.text
    assert (first.json()["action"], first.json()["reason"]) == ("verify", "step_up")

    db.session.verified_at = api.main.utcnow() - timedelta(seconds=5)
    second = _post_decision(api, db, session_id).json()
    assert (second["action"], second["reason"]) == ("allow", "verified"), second
    assert second["message"] == api.main.REASON_MESSAGES["verified"]
    # The upgrade changes the action, never what the evidence says.
    assert second["risk_score"] == first.json()["risk_score"] and second["label"] == first.json()["label"]

    # An expired step-up unlocks nothing. (Fresh tables, so this session's
    # budget and learning cap start clean; the rescued session above is stored
    # on probation and would not be compared against anyway.)
    tables = _ProfileTables()
    _seed_profile(tables)
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)
    db.session.verified_at = api.main.utcnow() - timedelta(seconds=api.main.VERIFICATION_VALID_S + 5)
    third = _post_decision(api, db, session_id)
    assert (third.json()["action"], third.json()["reason"]) == ("verify", "step_up")


def test_a_session_is_never_calibrated_against_its_own_vector(api, monkeypatch):
    """A session whose vector is a reference -- learned on probation and then
    settled by the merchant -- and that is decided again once its step-up has
    expired is compared with the customer's OTHER sessions. With its own copy
    in the reference set the candidate is usually no longer the most extreme
    point, p moves from 1/(n+1) to 2/(n+1) -- past alpha -- and the second
    checkout goes through without a challenge. The review endpoint shows the
    same reference set the decision used. (Left on probation, the vector
    would not be a reference at all; settled, only the session filter keeps
    it out.)"""
    main = api.main
    monkeypatch.setattr(main, "PROFILE_REVIEW_KEYS", {"denetci-1": "r" * 16 + "-review-operator-key-" + "3" * 8})
    api.configure(escalation=True)
    tables = _ProfileTables()
    # seed=0: on synthetic reference sets from _reference_set, seeds 0-11 at
    # n=19 and n=20, the copy pushes p past alpha in 24 of 24 under the
    # full-conformal rank -- by construction: the copy is scored against
    # exactly the points the candidate is, so the two tie. Under the
    # leave-one-out rank it replaced it was 23 of 24 (seed 5 at n=19 was the
    # exception), which is why this seed was chosen.
    pid = _seed_profile(tables, n=profiles.PROFILE_MIN_SESSIONS, seed=0)
    session_id = str(uuid.uuid4())
    # This session's own vector, learned an hour ago after a passed challenge
    # and since settled, so promoted: the newest reference, within the same
    # (profile, modality) buffer.
    tables.seed(
        "customer_profile_vectors",
        profile_id=pid,
        session_id=session_id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        modality="mouse",
        feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
        vec={name: _DEVIATING for name in FEATURE_NAMES},
        disp={name: 0.0 for name in FEATURE_NAMES},
        flush_count=5,
        probation=False,
        outcome="settled",
    )
    db = _customer_session(api, tables, session_id)
    db.session.verified_at = main.utcnow() - timedelta(seconds=main.VERIFICATION_VALID_S + 5)

    body = _post_decision(api, db, session_id).json()
    assert (body["action"], body["reason"]) == ("verify", "step_up"), body
    [row] = _audit_rows(tables, session_id)
    assert row["reference_n"] == profiles.PROFILE_MIN_SESSIONS, row["reference_n"]
    assert row["p_value"] == round(1 / (profiles.PROFILE_MIN_SESSIONS + 1), 4)

    # The statistic sees the difference, so the assertion above means
    # something: the same comparison with the session's own vector left in
    # does not fire.
    with_self = profiles.evaluate_profile(
        {name: _DEVIATING for name in FEATURE_NAMES},
        [r["vec"] for r in _vectors(tables, pid)],
        modality="mouse",
    )
    assert with_self.escalate is False

    review = api.client(db).get(
        f"/api/profile/review/{session_id}",
        headers={"X-Review-Operator": "denetci-1", "X-Review-Key": main.PROFILE_REVIEW_KEYS["denetci-1"]},
    )
    assert review.status_code == 200, review.text
    assert len(review.json()["references"]) == row["reference_n"]


def test_profile_escalation_is_not_named_to_the_client(api):
    """T11. The scored client hears the same generic step_up it hears for an
    ambiguous or clustered session -- nothing that confirms the reference
    exists, is mature, or which features gave it away."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    demo_pid = _seed_profile(tables, merchant="demo", ref=_DEMO_REF)
    session_id = str(uuid.uuid4())

    decision = _post_decision(api, _customer_session(api, tables, session_id), session_id)
    demo_session = str(uuid.uuid4())
    demo = _post_charge(api, _customer_session(api, tables, demo_session), demo_session, _DEMO_REF)

    for response, body in ((decision, decision.json()), (demo, demo.json()["decision"])):
        assert response.status_code == 200, response.text
        assert body["action"] == "verify"
        assert body["reason"] == "step_up"
        assert body["message"] == api.main.REASON_MESSAGES["step_up"] == "Islemi tamamlamak icin ek dogrulama gerekiyor"
        text = response.text
        for leaked in ("deviation", "sapma", "p_value", "profile", "profil", pid, pid[:12], demo_pid, _REF, _DEMO_REF):
            assert leaked not in text, f"istemci yaniti '{leaked}' iceriyor: {text}"
        for name in FEATURE_NAMES:
            assert name not in text, f"istemci yaniti ozellik adi iceriyor: {name}"
        assert set(body) == {"action", "risk_score", "label", "message", "reason"}
    assert demo.json()["status"] == "declined"


def test_collapsed_reasons_keep_their_internal_identity(api):
    """T12. What the client was told and what actually happened are both on the
    audit row: a human reviewer answering a complaint needs the second, and
    the client must only ever see the first."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    session_id = str(uuid.uuid4())
    response = _post_decision(
        api,
        _customer_session(api, tables, session_id),
        session_id,
        extra={"risk_context": {"amount_band": "high", "new_beneficiary": True}},
    )
    assert response.json()["reason"] == "step_up"

    [row] = _audit_rows(tables, session_id)
    assert (row["action"], row["reason"], row["public_reason"]) == ("verify", "profile_deviation", "step_up")
    assert row["profile_id"] == pid and row["merchant_id"] == "acme"
    assert row["profile_state"] == "evaluated" and row["shadow"] is False
    assert row["modality"] == "mouse" and row["reference_n"] == 20
    assert row["p_value"] <= profiles.PROFILE_ALPHA and row["deviation"] > 0
    assert row["p_value_low"] is not None
    assert len(row["top_features"]) == profiles.PROFILE_TOP_K
    assert row["feature_schema_version"] == profiles.FEATURE_SCHEMA_VERSION
    # Recorded, not enforced (spec section 12, item 7).
    assert (row["amount_band"], row["new_beneficiary"]) == ("high", True)
    assert row["risk_score"] == response.json()["risk_score"]

    # The same collapse for the reasons that existed before the layer did.
    ambiguous = str(uuid.uuid4())
    body = _post_decision(
        api,
        _customer_session(api, tables, ambiguous, value=_MATCHING, score=50.0, label="Şüpheli", flush_count=10),
        ambiguous,
    ).json()
    assert (body["action"], body["reason"]) == ("verify", "step_up")
    [row] = _audit_rows(tables, ambiguous)
    assert (row["reason"], row["public_reason"]) == ("ambiguous", "step_up")


def test_matching_profile_changes_nothing(api):
    """T7 over HTTP. A session that matches the customer's own history gets no
    benefit whatsoever: the decision is the one it would have been without a
    customer reference, for every verdict the evidence can reach."""
    api.configure(escalation=True)
    ts = _scorer_tests()
    for score, label, flushes in ((12.0, "Gerçek Kullanıcı", 5), (72.0, "Yüksek Risk", 5), (50.0, "Şüpheli", 10)):
        tables = _ProfileTables()
        _seed_profile(tables)
        session_id = str(uuid.uuid4())
        db = _customer_session(api, tables, session_id, value=_MATCHING, score=score, label=label, flush_count=flushes)
        profiled = _post_decision(api, db, session_id).json()
        plain = _post_decision(
            api, api.db(session=ts._stub_session(score, label), flush_count=flushes), str(uuid.uuid4()), ref=None
        ).json()
        assert profiled == plain, f"eslesen profil karari degistirdi ({score}): {profiled} != {plain}"
        [row] = _audit_rows(tables, session_id)
        assert row["profile_state"] == "evaluated" and row["p_value"] > profiles.PROFILE_ALPHA


def test_learning_is_idempotent(api):
    """T15. The unique (profile_id, session_id) index is the learn-once
    mechanism: however many allowed decisions one session produces, it
    teaches its customer's profile exactly one vector, and the session row
    says so."""
    tables = _ProfileTables()
    pid = _seed_profile(tables, n=5)
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id, value=_MATCHING)

    for _ in range(3):
        assert _post_decision(api, db, session_id).json()["action"] == "allow"
    learned = _vectors(tables, pid, session_id=session_id)
    assert len(learned) == 1, f"bir oturum {len(learned)} vektor ogretti"
    assert learned[0]["probation"] is False and learned[0]["outcome"] == "pending"
    assert learned[0]["modality"] == "mouse" and learned[0]["flush_count"] == 5
    assert learned[0]["vec"] == {name: _MATCHING for name in FEATURE_NAMES}
    [session_row] = [r for r in tables.committed["sessions"] if r["id"] == session_id]
    assert session_row["profile_learned"] is True and session_row["profile_id"] == pid
    assert len(_vectors(tables, pid)) == 6
    # A clean learn records no passed challenge, so it spends no budget.
    assert _profile_row(tables, pid)["escalation_count"] == 0
    # The profile is ACTIVE: the audit row carries its id, and the raw
    # reference is nowhere.
    assert all(r["profile_id"] == pid for r in _audit_rows(tables, session_id))
    assert _REF not in repr(tables.committed)


def test_learning_is_capped_per_istanbul_day(api):
    """Section 6.5. At most PROFILE_LEARN_PER_DAY vectors per profile per
    Europe/Istanbul calendar day, counted in SQL. The boundary is asserted on
    both sides of Istanbul midnight, which is 21:00 UTC: a cap counted on the
    UTC day fails one of the two halves at any time of day (vectors from just
    after Istanbul midnight are "yesterday" in UTC until 21:00 UTC, and
    vectors from just before it are "today" in UTC after 21:00 UTC)."""
    from zoneinfo import ZoneInfo

    now = datetime.now(timezone.utc)
    day_start = now.astimezone(ZoneInfo("Europe/Istanbul")).replace(hour=0, minute=0, second=0, microsecond=0)
    for offset, learns in ((timedelta(minutes=1), False), (-timedelta(minutes=1), True)):
        tables = _ProfileTables()
        pid = _seed_profile(tables, n=5)
        for index in range(profiles.PROFILE_LEARN_PER_DAY):
            tables.seed(
                "customer_profile_vectors",
                profile_id=pid,
                session_id=f"gun-{index}",
                created_at=day_start + offset,
                modality="mouse",
                feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
                vec={name: _MATCHING for name in FEATURE_NAMES},
                disp={name: 0.0 for name in FEATURE_NAMES},
                flush_count=5,
            )
        session_id = str(uuid.uuid4())
        body = _post_decision(api, _customer_session(api, tables, session_id, value=_MATCHING), session_id).json()
        assert body["action"] == "allow", body
        learned = _vectors(tables, pid, session_id=session_id)
        where = "Istanbul gece yarisindan sonra" if offset > timedelta(0) else "Istanbul gece yarisindan once"
        assert len(learned) == (1 if learns else 0), f"{where}: {len(learned)} vektor ogrenildi"
        # A capped learn leaves the session learnable later, not marked learned.
        [session_row] = [r for r in tables.committed["sessions"] if r["id"] == session_id]
        assert session_row["profile_learned"] is learns


def test_only_allow_teaches_the_profile(api, monkeypatch):
    """T16. verify, warn and block teach nothing. warn is an approval, but the
    one the ladder was least sure about, and a profile is built only from
    behaviour the system was confident about."""
    # A normal human calibration, so a 95 really is a block.
    monkeypatch.setattr(api.main.scorer, "_bundle", SimpleNamespace(human_calibration=[3.0 + i * 0.3 for i in range(30)]))
    cases = [
        ("verify", dict(score=72.0, label="Yüksek Risk")),
        ("warn", dict(score=50.0, label="Şüpheli", per_flush=[5.0] * 5)),
        ("block", dict(score=95.0, label="Bot Tespit Edildi")),
        ("allow", dict(score=12.0)),
    ]
    for expected, spec in cases:
        tables = _ProfileTables()
        pid = _seed_profile(tables, n=5)
        session_id = str(uuid.uuid4())
        score, label = spec.pop("score"), spec.pop("label", "Gerçek Kullanıcı")
        db = _customer_session(api, tables, session_id, value=_MATCHING, score=score, label=label, **spec)
        body = _post_decision(api, db, session_id).json()
        assert body["action"] == expected, (expected, body)
        written = _vectors(tables, pid, session_id=session_id)
        assert len(written) == (1 if expected == "allow" else 0), f"'{expected}' profil ogretti: {written}"

    # A verify that a step-up rescued teaches nothing either -- unless its
    # cause was the profile itself (probation, see T17). The payment goes
    # through; the behaviour was still never something the evidence approved.
    rescued = [
        ("ladder verify", dict(score=72.0, label="Yüksek Risk")),
        ("ambiguous", dict(score=50.0, label="Şüpheli", flush_count=10)),
        ("sequential", dict(score=12.0, per_flush=[95.0] * 7 + [10.0] * 3, flush_count=10)),
    ]
    for name, spec in rescued:
        tables = _ProfileTables()
        pid = _seed_profile(tables, n=5)
        session_id = str(uuid.uuid4())
        score, label = spec.pop("score"), spec.pop("label", "Gerçek Kullanıcı")
        db = _customer_session(api, tables, session_id, value=_MATCHING, score=score, label=label, **spec)
        assert _post_decision(api, db, session_id).json()["action"] == "verify", name
        [audit] = _audit_rows(tables, session_id)
        db.session.verified_at = api.main.utcnow() - timedelta(seconds=5)
        body = _post_decision(api, db, session_id).json()
        assert (body["action"], body["reason"]) == ("allow", "verified"), (name, body)
        assert _vectors(tables, pid, session_id=session_id) == [], f"kurtarilan '{name}' profil ogretti"
        if name == "sequential":
            assert audit["reason"] == "sequential", audit["reason"]


def test_step_up_rescued_deviation_is_learned_on_probation(api):
    """T17, and the user's own example end to end: grandmother's profile is
    mature, the grandchild is holding the phone. The layer asks for
    verification -- it does not refuse -- the right code lets the payment
    through, and the session is learned ON PROBATION: counted, but capped, and
    the first thing evicted."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables, merchant="demo", ref=_DEMO_REF)
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)

    declined = _post_charge(api, db, session_id).json()
    assert declined["status"] == "declined" and declined["charge_id"] is None
    assert (declined["decision"]["action"], declined["decision"]["reason"]) == ("verify", "step_up")
    assert declined["decision"]["message"] == api.main.REASON_MESSAGES["step_up"]
    [audit] = _audit_rows(tables, session_id)
    assert (audit["reason"], audit["public_reason"]) == ("profile_deviation", "step_up")
    # Issuing a challenge spends nothing: the budget counts PASSED challenges.
    assert _profile_row(tables, pid)["escalation_count"] == 0
    assert _vectors(tables, pid, session_id=session_id) == [], "reddedilen odeme profile ogretildi"

    assert _post_verify(api, db, session_id).status_code == 200
    charged = _post_charge(api, db, session_id).json()
    assert charged["status"] == "charged" and charged["charge_id"] is not None
    assert (charged["decision"]["action"], charged["decision"]["reason"]) == ("allow", "verified")
    assert charged["decision"]["risk_score"] == declined["decision"]["risk_score"]
    # The pass is what spends the budget -- once, by the learn that records it.
    assert _profile_row(tables, pid)["escalation_count"] == 1

    [learned] = _vectors(tables, pid, session_id=session_id)
    assert learned["probation"] is True, "adim-yukseltmeyle kurtarilan oturum denetimli ogrenilmedi"
    assert _profile_row(tables, pid)["consecutive_passed_escalations"] == 1
    assert _profile_row(tables, pid)["is_demo"] is True
    assert [r["reason"] for r in _audit_rows(tables, session_id)] == ["profile_deviation", "verified"]

    # Again for the same session: charged, still exactly one vector, and the
    # pass is not counted twice.
    assert _post_charge(api, db, session_id).json()["status"] == "charged"
    assert len(_vectors(tables, pid, session_id=session_id)) == 1
    assert _profile_row(tables, pid)["escalation_count"] == 1

    # THE ACCEPTED COST, chosen explicitly: the grandchild is not remembered.
    # Their next checkout in the same pattern is challenged again -- declined
    # until the code is entered, never blocked -- at the same p-value, because
    # the rescued session is stored on probation and is not a reference until
    # the merchant settles it or healing promotes the pattern. The alternative
    # was that one passed step-up desensitised the profile for everyone who
    # looks like the grandchild, the attacker included.
    next_session = str(uuid.uuid4())
    db = _customer_session(api, tables, next_session)
    again = _post_charge(api, db, next_session).json()
    assert again["status"] == "declined", "torunun sonraki odemesi yeniden sorgulanmadi"
    assert (again["decision"]["action"], again["decision"]["reason"]) == ("verify", "step_up")
    [again_audit] = _audit_rows(tables, next_session)
    assert again_audit["reason"] == "profile_deviation" and again_audit["p_value"] == audit["p_value"]
    assert (again_audit["reference_n"], again_audit["probation_n"]) == (20, 1)
    assert _post_verify(api, db, next_session).status_code == 200
    assert _post_charge(api, db, next_session).json()["status"] == "charged"
    assert _vectors(tables, pid, session_id=next_session)[0]["probation"] is True
    assert _profile_row(tables, pid)["consecutive_passed_escalations"] == 2
    assert _profile_row(tables, pid)["escalation_count"] == 2

    # The fifth probation vector evicts the OLDEST PROBATION vector -- never a
    # reference, although the references are older and the reference set is
    # full. Probation vectors have slots of their own beside the 20.
    tables = _ProfileTables()
    pid = _seed_profile(
        tables,
        n=profiles.PROFILE_BUFFER_MAX + profiles.PROFILE_PROBATION_MAX,
        probation_slots=profiles.PROFILE_PROBATION_MAX,
    )
    before = {r["session_id"]: r for r in _vectors(tables, pid)}
    clean = {sid for sid, r in before.items() if not r["probation"]}
    assert len(clean) == profiles.PROFILE_BUFFER_MAX
    oldest_probation = min((r for r in before.values() if r["probation"]), key=lambda r: r["created_at"])
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)
    assert _post_decision(api, db, session_id).json()["action"] == "verify"
    db.session.verified_at = api.main.utcnow()
    assert _post_decision(api, db, session_id).json()["reason"] == "verified"

    after = {r["session_id"]: r for r in _vectors(tables, pid)}
    assert len(after) == profiles.PROFILE_BUFFER_MAX + profiles.PROFILE_PROBATION_MAX
    assert clean <= set(after), "denetimli ekleme temiz bir vektoru cikardi"
    assert oldest_probation["session_id"] not in after
    assert sum(r["probation"] for r in after.values()) == profiles.PROFILE_PROBATION_MAX
    assert after[session_id]["probation"] is True


def test_repeated_passed_challenges_rebuild_the_profile(api):
    """T18, and the grandchild's accepted cost end to end. The customer's
    behaviour has really changed (or the grandchild keeps paying): the SAME
    deviation, session after session. Each rescued session is stored on
    probation and is not a reference, so the next one is challenged again at
    the same p-value -- friction, not a block, and the cost the user chose.
    Three challenges in a row, each answered correctly, say the PROFILE is
    wrong: the run is promoted, the buffer is cut to the newest
    PROFILE_HEAL_KEEP, the counter resets, and the layer abstains until it has
    re-learned who the customer is now."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    rescued, p_values = [], []

    for round_ in range(1, profiles.PROFILE_HEAL_AFTER + 1):
        session_id = str(uuid.uuid4())
        db = _customer_session(api, tables, session_id, value=_DEVIATING)
        body = _post_decision(api, db, session_id).json()
        assert (body["action"], body["reason"]) == ("verify", "step_up"), f"tur {round_}: {body}"
        [challenge] = _audit_rows(tables, session_id)
        # Nothing learned from the earlier rounds reached the statistic.
        assert challenge["reference_n"] == 20 and challenge["probation_n"] == round_ - 1, f"tur {round_}"
        p_values.append(challenge["p_value"])
        assert _post_verify(api, db, session_id).status_code == 200
        assert _post_decision(api, db, session_id).json()["reason"] == "verified", f"tur {round_}"
        rescued.append(session_id)
        if round_ < profiles.PROFILE_HEAL_AFTER:
            assert _profile_row(tables, pid)["consecutive_passed_escalations"] == round_
            assert _profile_row(tables, pid)["stats_rebuilt_at"] is None
            assert all(r["probation"] for sid in rescued for r in _vectors(tables, pid, session_id=sid)), (
                f"tur {round_}: tek basarili dogrulama referansa terfi etti"
            )
    assert len(set(p_values)) == 1, f"denetimli vektorler p-degerini degistirdi: {p_values}"

    row = _profile_row(tables, pid)
    assert row["consecutive_passed_escalations"] == 0, "sayac sifirlanmadi"
    assert row["stats_rebuilt_at"] is not None
    remaining = _vectors(tables, pid)
    assert len(remaining) == profiles.PROFILE_HEAL_KEEP, f"{len(remaining)} vektor kaldi"
    # The run was promoted, and it is the newest part of what was kept.
    assert {r["session_id"] for r in remaining} >= set(rescued)
    assert not any(r["probation"] for r in remaining), "iyilesme deneme vektorlerini terfi ettirmedi"
    newest = sorted(remaining, key=lambda r: r["created_at"])[-profiles.PROFILE_HEAL_AFTER :]
    assert [r["session_id"] for r in newest] == rescued

    # And the layer abstains on the same deviation: the rebuilt profile is
    # below maturity until it has re-learned the customer.
    session_id = str(uuid.uuid4())
    body = _post_decision(api, _customer_session(api, tables, session_id, value=_DEVIATING), session_id).json()
    assert (body["action"], body["reason"]) == ("allow", "score")
    [row] = _audit_rows(tables, session_id)
    assert (row["profile_state"], row["reference_n"], row["probation_n"]) == ("immature", profiles.PROFILE_HEAL_KEEP, 0)


def test_a_single_passed_challenge_is_never_promoted(api):
    """Healing promotes a RUN of passed challenges, not a counter. The counter
    can stand at PROFILE_HEAL_AFTER - 1 while the vectors behind it are gone
    (disputed, so deleted) or belong to another input type; one attacker who
    passes step-up once must still end up on probation, with no rebuild."""
    api.configure(escalation=True)
    for case in ("disputed_run", "other_modality_run"):
        tables = _ProfileTables()
        pid = _seed_profile(tables, consecutive_passed_escalations=profiles.PROFILE_HEAL_AFTER - 1)
        if case == "other_modality_run":
            for index in range(profiles.PROFILE_HEAL_AFTER - 1):
                tables.seed(
                    "customer_profile_vectors",
                    profile_id=pid,
                    session_id=f"klavye-{index}",
                    created_at=datetime.now(timezone.utc) - timedelta(days=2, hours=index),
                    modality="keyboard",
                    feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
                    vec={name: _DEVIATING for name in FEATURE_NAMES},
                    disp={name: 0.0 for name in FEATURE_NAMES},
                    flush_count=5,
                    probation=True,
                )
        before = {r["session_id"] for r in _vectors(tables, pid)}
        attacker = str(uuid.uuid4())
        db = _customer_session(api, tables, attacker)
        assert _post_decision(api, db, attacker).json()["action"] == "verify", case
        assert _post_verify(api, db, attacker).status_code == 200
        assert _post_decision(api, db, attacker).json()["reason"] == "verified", case

        row = _profile_row(tables, pid)
        assert row["consecutive_passed_escalations"] == profiles.PROFILE_HEAL_AFTER, case
        assert row["stats_rebuilt_at"] is None, f"{case}: tek basarili dogrulama profili yeniden kurdu"
        [learned] = _vectors(tables, pid, session_id=attacker)
        assert learned["probation"] is True, f"{case}: saldirganin vektoru terfi etti"
        assert {r["session_id"] for r in _vectors(tables, pid)} == before | {attacker}, case
        assert sum(not r["probation"] for r in _vectors(tables, pid)) == 20, case


def _report_outcome(api, tables, session_id, outcome, headers=_ACME):
    response = api.client(api.db(tables=tables)).post(
        "/api/outcome", json={"session_id": session_id, "outcome": outcome}, headers=headers
    )
    assert response.status_code == 204, response.text
    return response


def _challenge_and_pass(api, tables, session_id, value):
    """A session that is challenged by the profile, passes step-up, and is
    learned on probation -- by the grandchild, or by an attacker holding the
    OTP. Returns the challenge's audit row."""
    db = _customer_session(api, tables, session_id, value=value)
    body = _post_decision(api, db, session_id).json()
    assert (body["action"], body["reason"]) == ("verify", "step_up"), body
    [challenge] = _audit_rows(tables, session_id)
    assert _post_verify(api, db, session_id).status_code == 200
    assert _post_decision(api, db, session_id).json()["reason"] == "verified"
    return challenge


_EVIDENCE_COLUMNS = ("profile_state", "deviation", "p_value", "p_value_low", "top_features", "reference_n")


def test_a_probation_vector_does_not_shield_the_next_attacker_session(api):
    """The measured poisoning hole, closed. An attacker who passes step-up once
    (a phished OTP) is learned on probation. When a probation vector was a
    reference it shielded every later session less extreme than itself --
    48.5% -> 25.0% escalated with one such vector on synthetic identities on
    2026-09-16, and 47.5% -> 25.5% for a vector that still counts under the
    shipped rank (docs/profile-evaluation.md section 7, which also measures
    the probation curve flat end to end). Stored but not compared, it leaves
    the attacker's next session exactly where it would have been with no
    vector at all: same evidence, same p-value, same challenge.

    It is still a vector: "disputed" deletes it, and "settled" -- the merchant
    saying the payment was the customer's -- promotes it, after which it
    counts, and shields, like any other reference."""
    api.configure(escalation=True)
    first, later = _DEVIATING, _DEVIATING - 0.1

    # A just-mature profile (19 references) and a full one (20): with 19 the
    # attacker's vector would fit inside a 20-row reference read, with 20 it
    # would push the oldest reference out of it. Neither may happen.
    for n in (profiles.PROFILE_MIN_SESSIONS, profiles.PROFILE_BUFFER_MAX):
        # Control: the same later session against the untouched profile.
        control_tables = _ProfileTables()
        _seed_profile(control_tables, n=n)
        control = str(uuid.uuid4())
        body = _post_decision(api, _customer_session(api, control_tables, control, value=later), control).json()
        assert body["action"] == "verify", n
        [control_row] = _audit_rows(control_tables, control)
        assert control_row["p_value"] <= profiles.PROFILE_ALPHA

        tables = _ProfileTables()
        pid = _seed_profile(tables, n=n)
        _challenge_and_pass(api, tables, "saldirgan-1", first)
        [stored] = _vectors(tables, pid, session_id="saldirgan-1")
        assert stored["probation"] is True

        after = str(uuid.uuid4())
        body = _post_decision(api, _customer_session(api, tables, after, value=later), after).json()
        assert (body["action"], body["reason"]) == ("verify", "step_up"), f"n={n}: denetimli vektor saldirgani korudu"
        [row] = _audit_rows(tables, after)
        assert {c: row[c] for c in _EVIDENCE_COLUMNS} == {c: control_row[c] for c in _EVIDENCE_COLUMNS}, n
        assert (row["reference_n"], row["probation_n"], control_row["probation_n"]) == (n, 1, 0)

        # What the stored vector WOULD have done as a reference -- the
        # behaviour before probation vectors were excluded -- so the equality
        # above is not vacuous.
        references = [r["vec"] for r in _vectors(tables, pid, probation=False)]
        newest_first = sorted(_vectors(tables, pid), key=lambda r: r["created_at"], reverse=True)
        as_reference = profiles.evaluate_profile(
            {name: later for name in FEATURE_NAMES},
            [r["vec"] for r in newest_first][: profiles.PROFILE_BUFFER_MAX],
            modality="mouse",
        )
        assert as_reference.escalate is False and as_reference.p_value > row["p_value"], n
        assert len(references) == n

    # disputed: the vector is deleted, exactly.
    _report_outcome(api, tables, "saldirgan-1", "disputed")
    assert _vectors(tables, pid, session_id="saldirgan-1") == []
    assert len(_vectors(tables, pid)) == 20

    # settled: promoted, and from then on it counts. The references were full,
    # so the oldest one makes room; the storage cap still holds.
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    oldest = min(_vectors(tables, pid), key=lambda r: r["created_at"])["session_id"]
    _challenge_and_pass(api, tables, "saldirgan-1", first)
    _report_outcome(api, tables, "saldirgan-1", "settled")
    [promoted] = _vectors(tables, pid, session_id="saldirgan-1")
    assert (promoted["probation"], promoted["outcome"]) == (False, "settled")
    assert len(_vectors(tables, pid)) == profiles.PROFILE_BUFFER_MAX
    assert _vectors(tables, pid, session_id=oldest) == [], "terfi en eski referansi cikarmadi"

    settled_later = str(uuid.uuid4())
    body = _post_decision(api, _customer_session(api, tables, settled_later, value=later), settled_later).json()
    assert (body["action"], body["reason"]) == ("allow", "score"), body
    [row] = _audit_rows(tables, settled_later)
    assert (row["reference_n"], row["probation_n"]) == (20, 0)
    assert row["p_value"] > profiles.PROFILE_ALPHA

    # Another merchant's settlement promotes nothing.
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    _challenge_and_pass(api, tables, "saldirgan-1", first)
    _report_outcome(api, tables, "saldirgan-1", "settled", headers=_GLOBEX)
    [still] = _vectors(tables, pid, session_id="saldirgan-1")
    assert (still["probation"], still["outcome"]) == (True, "pending")


def test_maturity_counts_only_references(api, monkeypatch):
    """Probation vectors do not make a profile mature. 18 references and four
    probation vectors is 22 stored sessions and still an immature profile --
    the layer does not compare -- and the SOC block and the review surface
    report the two counts apart. One settlement makes it 19, and mature."""
    main = api.main
    monkeypatch.setattr(main, "PROFILE_REVIEW_KEYS", {"denetci-1": _REVIEW_KEY})
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(
        tables,
        n=profiles.PROFILE_MIN_SESSIONS - 1 + profiles.PROFILE_PROBATION_MAX,
        probation_slots=profiles.PROFILE_PROBATION_MAX,
    )
    probation_sessions = [r["session_id"] for r in _vectors(tables, pid, probation=True)]
    assert len(probation_sessions) == profiles.PROFILE_PROBATION_MAX

    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)
    # A session that has already taught this profile once, so this decision
    # only reads: the counts below stay the seeded ones.
    db.session.profile_learned = True
    body = _post_decision(api, db, session_id).json()
    assert (body["action"], body["reason"]) == ("allow", "score"), body
    [row] = _audit_rows(tables, session_id)
    counts = (profiles.PROFILE_MIN_SESSIONS - 1, profiles.PROFILE_PROBATION_MAX)
    assert (row["profile_state"], row["reference_n"], row["probation_n"]) == ("immature", *counts)
    assert row["p_value"] is None

    block = _score(api, _dashboard_db(api, tables), session_id).json()["profile"]
    assert (block["state"], block["reference_n"], block["probation_n"]) == ("immature", *counts)

    review = api.client(db).get(f"/api/profile/review/{session_id}", headers=_REVIEWER)
    assert review.status_code == 200, review.text
    review = review.json()
    assert (review["reference_n"], review["probation_n"]) == counts
    assert (len(review["references"]), len(review["probation_vectors"])) == counts
    assert not any(r["probation"] for r in review["references"])
    assert all(r["probation"] for r in review["probation_vectors"])
    assert all(stat["n_obs"] == counts[0] for stat in review["feature_stats"].values())

    # The rule the decision applies in SQL is the rule profiles.is_reference
    # states.
    assert sum(profiles.is_reference(r) for r in _vectors(tables, pid)) == row["reference_n"]

    _report_outcome(api, tables, probation_sessions[0], "settled")
    matured = str(uuid.uuid4())
    body = _post_decision(api, _customer_session(api, tables, matured), matured).json()
    assert (body["action"], body["reason"]) == ("verify", "step_up"), body
    [row] = _audit_rows(tables, matured)
    assert (row["profile_state"], row["reference_n"], row["probation_n"]) == (
        "evaluated",
        profiles.PROFILE_MIN_SESSIONS,
        profiles.PROFILE_PROBATION_MAX - 1,
    )


def test_per_profile_challenge_budget(api):
    """T19. No customer is challenged by this layer after PASSING
    PROFILE_MAX_ESCALATIONS of its challenges in PROFILE_BUDGET_WINDOW_DAYS: the
    next deviation in the window is recorded and not acted on. The budget
    counts passes, not challenges issued -- see
    test_an_unanswered_challenge_never_becomes_an_approval for why."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    # Two passes already this window, and none of them in a row (a clean
    # session in between reset the healing counter), so it is the budget and
    # not self-healing that stops the challenges.
    pid = _seed_profile(
        tables, escalation_count=profiles.PROFILE_MAX_ESCALATIONS - 1, escalation_window_start=datetime.now(timezone.utc)
    )
    session_id = str(uuid.uuid4())
    _challenge_and_pass(api, tables, session_id, _DEVIATING)
    row = _profile_row(tables, pid)
    assert row["escalation_count"] == profiles.PROFILE_MAX_ESCALATIONS
    assert row["stats_rebuilt_at"] is None and row["consecutive_passed_escalations"] == 1

    session_id = str(uuid.uuid4())
    body = _post_decision(api, _customer_session(api, tables, session_id), session_id).json()
    assert (body["action"], body["reason"]) == ("allow", "score"), body
    [audit] = _audit_rows(tables, session_id)
    assert audit["profile_state"] == "budget_exhausted"
    assert audit["p_value"] <= profiles.PROFILE_ALPHA, "butce durumu kanitini sildi"
    assert _profile_row(tables, pid)["escalation_count"] == profiles.PROFILE_MAX_ESCALATIONS

    # A window that has run out starts over, at the next pass.
    tables = _ProfileTables()
    pid = _seed_profile(
        tables,
        escalation_count=profiles.PROFILE_MAX_ESCALATIONS,
        escalation_window_start=datetime.now(timezone.utc) - timedelta(days=profiles.PROFILE_BUDGET_WINDOW_DAYS + 1),
    )
    session_id = str(uuid.uuid4())
    _challenge_and_pass(api, tables, session_id, _DEVIATING)
    row = _profile_row(tables, pid)
    assert row["escalation_count"] == 1
    assert datetime.now(timezone.utc) - row["escalation_window_start"] < timedelta(minutes=1)


def test_an_unanswered_challenge_never_becomes_an_approval(api):
    """The account-takeover attacker without the OTP -- the one person this
    layer exists for -- must not be able to turn challenges he cannot answer
    into a charge. When the budget was spent by ISSUING a challenge, pressing
    pay four times did exactly that: verify, verify, verify, then allow on
    budget_exhausted, for that session and for every new one for 30 days.

    Retrying one session, opening fresh sessions, and the demo charge: every
    answer stays a challenge, the budget stays untouched, and the breaker
    counts this customer once however often he retries."""
    import asyncio

    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    presses = 2 * profiles.PROFILE_MAX_ESCALATIONS + 1

    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)
    retries = [_post_decision(api, db, session_id).json() for _ in range(presses)]
    assert [(b["action"], b["reason"]) for b in retries] == [("verify", "step_up")] * presses, retries

    fresh = []
    for _ in range(profiles.PROFILE_MAX_ESCALATIONS + 1):
        sid = str(uuid.uuid4())
        fresh.append(_post_decision(api, _customer_session(api, tables, sid), sid).json()["action"])
    assert fresh == ["verify"] * (profiles.PROFILE_MAX_ESCALATIONS + 1), fresh

    row = _profile_row(tables, pid)
    assert row["escalation_count"] == 0 and row["consecutive_passed_escalations"] == 0
    assert {r["profile_state"] for r in tables.committed["decision_audit"]} == {"evaluated"}
    assert _vectors(tables, pid, probation=True) == [], "yanitlanmayan sorgulama ogretildi"

    # Every retry wrote an enforced profile_deviation row; the breaker still
    # sees one customer.
    enforced = [r for r in tables.committed["decision_audit"] if r["reason"] == "profile_deviation" and not r["shadow"]]
    assert len(enforced) == presses + profiles.PROFILE_MAX_ESCALATIONS + 1
    api.main._profile_breaker.update(checked_at=None)
    assert asyncio.run(api.main._profile_breaker_count(api.db(tables=tables))) == 1

    # The same through the demo charge: never charged without the code.
    tables = _ProfileTables()
    _seed_profile(tables, merchant="demo", ref=_DEMO_REF)
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)
    statuses = [_post_charge(api, db, session_id).json()["status"] for _ in range(presses)]
    assert statuses == ["declined"] * presses, statuses


def test_circuit_breaker_suppresses_escalations(api, monkeypatch, caplog):
    """T20. Above an absolute deployment-wide ceiling of customers given an
    enforced challenge the layer stops acting, says so once per window with no
    identifiers, and keeps recording. Shadow rows challenged nobody and do not
    count, and a customer counts once however many rows its retries wrote."""
    import logging

    caplog.set_level(logging.DEBUG)
    api.configure(escalation=True)
    monkeypatch.setattr(profiles, "PROFILE_BREAKER_MAX", 2)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    now = datetime.now(timezone.utc)
    for index in range(5):
        tables.seed(
            "decision_audit", session_id="eski", profile_id=f"golge-{index}", reason="profile_deviation", shadow=True, decided_at=now
        )
    tables.seed(
        "decision_audit", session_id="eski", profile_id="eski-musteri", reason="profile_deviation", shadow=False,
        decided_at=now - timedelta(hours=2),
    )
    # One other customer's session retried five times: one customer, not five.
    for _ in range(5):
        tables.seed("decision_audit", session_id="tekrar", profile_id="tekrarlayan", reason="profile_deviation", shadow=False, decided_at=now)

    session_id = str(uuid.uuid4())
    assert _post_decision(api, _customer_session(api, tables, session_id), session_id).json()["action"] == "verify", (
        "golge satirlar veya pencere disi satirlar devre kesiciyi acti"
    )

    # A third customer challenged in the window, after the retrying one and
    # this test's own: past the ceiling of 2.
    tables.committed["decision_audit"].append(
        tables.with_defaults(
            "decision_audit", dict(session_id="yeni", profile_id="yeni-musteri", reason="profile_deviation", shadow=False, decided_at=now)
        )
    )
    api.main._profile_breaker.update(checked_at=None)

    suppressed = []
    for _ in range(3):
        session_id = str(uuid.uuid4())
        body = _post_decision(api, _customer_session(api, tables, session_id), session_id).json()
        suppressed.append((body["action"], body["reason"], _audit_rows(tables, session_id)[0]["profile_state"]))
    assert suppressed == [("allow", "score", "breaker")] * 3, suppressed

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "devre kesici" in r.getMessage()]
    assert len(warnings) == 1, f"devre kesici uyarisi {len(warnings)} kez yazildi"
    for leaked in (pid, pid[:12], _REF):
        assert leaked not in caplog.text


def test_layer_is_off_by_default(api):
    """T31. With no profile variable set -- the shipped default -- a decision
    issues exactly the statements it issued before the layer existed: the
    session read and the sequential-test read. Nothing else is read, nothing is
    written, nothing is committed, whether or not a customer is named, on
    either endpoint."""
    results = _boot_results()
    for mode in ("debug", "production"):
        default = results[f"{mode}/defaults"]
        assert default == {**default, "enabled": False, "layer": False, "escalation": False, "has_key": False}

    from sqlalchemy import select

    main = api.main
    ts = _scorer_tests()
    sprt_read = str(
        select(main.BehaviorData.risk_score, main.BehaviorData.behavior_bucket)
        .where(main.BehaviorData.session_id == "x")
        .order_by(main.BehaviorData.created_at.desc())
        .limit(main.SPRT_MAX_FLUSHES)
    )
    # (configuration, endpoint, customer_ref). With no merchant configured a
    # named customer on /api/decision is a 401 before any statement (T13), so
    # there the reference is sent where a credential exists: the layer flag off
    # with the key and merchants in place. The demo charge takes none.
    requests = [
        (dict(layer=False, key=False, merchants=False), "decision", None),
        (dict(layer=False, key=False, merchants=False), "charge", None),
        (dict(layer=False, key=False, merchants=False), "charge", _DEMO_REF),
        (dict(layer=False, key=True, merchants=True), "decision", _REF),
        (dict(layer=False, key=True, merchants=True), "charge", _DEMO_REF),
    ]
    verdicts = ((12.0, "Gerçek Kullanıcı"), (72.0, "Yüksek Risk"), (95.0, "Bot Tespit Edildi"))
    for (config, endpoint, ref), (score, label) in itertools.product(requests, verdicts):
        api.configure(**config, escalation=False)
        db = api.db(session=ts._stub_session(score, label), flushes=_session_flushes(_DEVIATING))
        session_id = str(uuid.uuid4())
        if endpoint == "decision":
            response = _post_decision(api, db, session_id, ref=ref)
        else:
            response = _post_charge(api, db, session_id, ref=ref)
        assert response.status_code == 200, response.text
        assert db.executed == [sprt_read], f"{config}/{endpoint}/{ref}: {db.executed}"
        assert db.added == [] and db.committed is False
        assert db.tables.statements == []


def test_shadow_mode_records_but_does_not_act(api, caplog):
    """T32. PROFILE_LAYER=1 with PROFILE_ESCALATION=0 is how the layer gets
    measured before it may act: a deviating session is decided exactly as it
    would have been, and the audit row keeps the deviation, marked shadow."""
    import logging

    caplog.set_level(logging.INFO)
    api.configure(escalation=False)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    session_id = str(uuid.uuid4())
    shadow = _post_decision(api, _customer_session(api, tables, session_id), session_id).json()
    plain = _post_decision(
        api, api.db(session=_scorer_tests()._stub_session(12.0)), str(uuid.uuid4()), ref=None
    ).json()
    assert shadow == plain, f"golge mod karari degistirdi: {shadow}"

    [row] = _audit_rows(tables, session_id)
    assert row["shadow"] is True
    assert (row["action"], row["reason"], row["public_reason"]) == ("allow", "score", "score")
    assert row["profile_state"] == "evaluated" and row["profile_id"] == pid
    assert row["deviation"] > 0 and row["p_value"] <= profiles.PROFILE_ALPHA and row["top_features"]
    assert _profile_row(tables, pid)["escalation_count"] == 0, "golge mod butce harcadi"
    lines = [r.getMessage() for r in caplog.records if "shadow escalation" in r.getMessage()]
    assert lines == [f"profile shadow escalation for session {session_id} (p={row['p_value']:.3f}, n=20, modality=mouse)"]


def test_feature_schema_mismatch_invalidates_a_profile(api, monkeypatch):
    """T28. A profile stamped under another feature schema is immature: not
    compared, not learned into, and its vectors not even read. A rescaled
    feature would otherwise make every profiled customer deviate on the same
    afternoon."""
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    monkeypatch.setattr(profiles, "FEATURE_SCHEMA_VERSION", profiles.FEATURE_SCHEMA_VERSION + 1)
    session_id = str(uuid.uuid4())
    body = _post_decision(api, _customer_session(api, tables, session_id), session_id).json()
    assert (body["action"], body["reason"]) == ("allow", "score")
    [row] = _audit_rows(tables, session_id)
    assert row["profile_state"] == "immature" and row["reference_n"] == 0 and row["p_value"] is None
    assert ("select", "customer_profile_vectors") not in tables.statements
    assert ("select", "behavior_data") not in tables.statements
    assert len(_vectors(tables, pid)) == 20


def test_profile_id_is_never_logged(api, caplog):
    """T27. An escalation, a shadow escalation, a probation learn, a rebuild,
    a tripped breaker and an erasure, all at DEBUG: the pseudonym -- and any
    12-character prefix of it -- appears in no log line and no response."""
    import logging

    caplog.set_level(logging.DEBUG)
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    bodies = []
    for round_ in range(profiles.PROFILE_HEAL_AFTER):
        session_id = str(uuid.uuid4())
        # Further out each round, see test_repeated_passed_challenges_rebuild_the_profile.
        db = _customer_session(api, tables, session_id, value=_DEVIATING + round_)
        bodies.append(_post_decision(api, db, session_id).text)
        _post_verify(api, db, session_id)
        bodies.append(_post_decision(api, db, session_id).text)
    api.configure(escalation=False)
    session_id = str(uuid.uuid4())
    bodies.append(_post_decision(api, _customer_session(api, tables, session_id), session_id).text)
    bodies.append(api.client(api.db(tables=tables)).post("/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME).text)

    assert "profile escalation for session" in caplog.text and "profile rebuilt for session" in caplog.text
    for leaked in (pid, pid[:12], _REF):
        assert leaked not in caplog.text, "profil kimligi loga yazildi"
        assert not any(leaked in body for body in bodies)


def test_info_lines_reach_a_handler():
    """Section 11.1 asks for profile events at INFO, and nothing configured
    logging: Python's last-resort handler prints WARNING and above, so every
    INFO line of the deepcheck loggers was dropped. main attaches one handler
    at INFO, which the scorer's logger reaches too."""
    import logging

    _main()
    deepcheck = logging.getLogger("deepcheck")
    assert deepcheck.handlers, "deepcheck loglarini yazacak bir isleyici yok"
    assert logging.getLogger("deepcheck.scorer").isEnabledFor(logging.INFO)


def test_a_failed_profile_read_is_answered_without_identifiers(api, caplog):
    """Section 11.1 on the decision path. A database error message carries the
    statement's bound parameters -- here the profile id -- so a failed read is
    one Turkish line naming the exception class and a generic 503, and a failed
    audit or learning write is rolled back without changing the verdict."""
    import logging

    caplog.set_level(logging.DEBUG)
    api.configure(escalation=True)
    tables = _ProfileTables(fail_on=("select", "customer_profiles"))
    pid = _seed_profile(tables)
    session_id = str(uuid.uuid4())
    response = _post_decision(api, _customer_session(api, tables, session_id), session_id)
    assert response.status_code == 503
    assert response.json() == {"detail": "Profil islemi su anda tamamlanamadi, lutfen tekrar deneyin"}
    assert "Musteri profili okunamadi (IntegrityError)" in caplog.text

    tables = _ProfileTables(fail_on=("insert", "customer_profile_vectors"))
    pid = _seed_profile(tables, n=5)
    session_id = str(uuid.uuid4())
    body = _post_decision(api, _customer_session(api, tables, session_id, value=_MATCHING), session_id).json()
    assert body["action"] == "allow", "yazma hatasi karari degistirdi"
    assert "Profil karar kaydi yazilamadi (IntegrityError)" in caplog.text
    assert _audit_rows(tables, session_id) == [] and _vectors(tables, pid, session_id=session_id) == []
    for leaked in (pid, pid[:12], _REF):
        assert leaked not in caplog.text and leaked not in response.text


def test_a_decision_racing_an_erasure_does_not_keep_the_pseudonym(api):
    """An erasure that commits while a decision is being made. The decision
    read the profile at its start, and the erasure's UPDATE decision_audit
    cannot see a row inserted after it, so the audit row used to be written
    with the erased profile id and kept it for 90 days -- reproduced on
    Postgres 16 for an enforced deviation and for a shadow match. Interleaved
    here in that order: read and decide, erasure commits, then write. The row
    is still written (the decision happened) but names no profile, and nothing
    is learned into the erased customer."""
    import asyncio

    main = api.main
    for escalation, value, expected in ((True, _DEVIATING, "verify"), (False, _MATCHING, "allow")):
        api.configure(escalation=escalation)
        tables = _ProfileTables()
        pid = _seed_profile(tables)
        session_id = str(uuid.uuid4())
        db = _customer_session(api, tables, session_id, value=value)

        async def read_and_decide():
            ctx = await main._load_profile_context(db, session_id, "acme", _REF)
            return ctx, await main._decide_on_evidence(db, db.session, session_id, profile_ctx=ctx)

        ctx, verdict = asyncio.run(read_and_decide())
        assert ctx.profile_id == pid and verdict.action == expected

        erased = api.client(api.db(tables=tables)).post(
            "/api/profile/erase", json={"customer_ref": _REF, "mode": "erase"}, headers=_ACME
        )
        assert erased.status_code == 204

        final = main._apply_step_up(db.session, verdict)
        asyncio.run(main._learn_and_audit(db, db.session, session_id, final, verdict.reason, ctx, "acme", None))
        [row] = _audit_rows(tables, session_id)
        assert row["action"] == expected and row["profile_state"] == "evaluated"
        assert row["profile_id"] is None, f"{expected}: silinen profilin kimligi karar kaydina yazildi"
        assert _vectors(tables, pid) == [], f"{expected}: silinen profile vektor ogretildi"
        assert pid not in repr(tables.committed)

        # Without an erasure the same decision keeps its link.
        tables = _ProfileTables()
        pid = _seed_profile(tables)
        session_id = str(uuid.uuid4())
        _post_decision(api, _customer_session(api, tables, session_id, value=value), session_id)
        [row] = _audit_rows(tables, session_id)
        assert row["profile_id"] == pid


# The property the whole layer exists to keep, swept over every combination the
# decision path can reach: evidence outcome x profile state x step-up freshness
# x escalation mode x endpoint.

_EVIDENCE_OUTCOMES = {
    # name: (stub session kwargs, stub db kwargs, human calibration, cluster escalation)
    "allow": (dict(risk_score=12.0), dict(flush_count=5), "normal", False),
    "warn": (dict(risk_score=50.0, label="Şüpheli"), dict(flush_count=5, per_flush=[5.0] * 5), "normal", False),
    "ladder_verify": (dict(risk_score=72.0, label="Yüksek Risk"), dict(flush_count=5), "normal", False),
    "block": (dict(risk_score=95.0, label="Bot Tespit Edildi"), dict(flush_count=5), "normal", False),
    "conformal": (dict(risk_score=95.0, label="Bot Tespit Edildi"), dict(flush_count=5), "human_in_90s", False),
    "insufficient": (dict(risk_score=12.0), dict(flush_count=2), "normal", False),
    "stale": (dict(risk_score=12.0, last_seen_at="old"), dict(flush_count=5), "normal", False),
    "ambiguous": (dict(risk_score=50.0, label="Şüpheli"), dict(flush_count=10), "normal", False),
    "cluster": (dict(risk_score=9.0), dict(flush_count=4, cluster_peers=6), "normal", True),
    "unknown_session": (None, dict(), "normal", False),
}


def _seed_profile_state(state, tables, merchant, ref):
    """(flush value, flush mask, pointer) for the candidate session, after
    seeding `tables` into the named profile state."""
    now = datetime.now(timezone.utc)
    if state == "no_profile":
        return _DEVIATING, _ALL_MEASURED, "pointer_mouse"
    if state == "objected":
        _seed_profile(tables, merchant=merchant, ref=ref, n=0, consent_basis="objected", profiling_enabled=False, erased_at=now)
        return _DEVIATING, _ALL_MEASURED, "pointer_mouse"
    if state == "schema_mismatch":
        _seed_profile(tables, merchant=merchant, ref=ref, feature_schema_version=profiles.FEATURE_SCHEMA_VERSION + 1)
    elif state == "immature":
        _seed_profile(tables, merchant=merchant, ref=ref, n=profiles.PROFILE_MIN_SESSIONS - 1)
    elif state == "other_modality":
        _seed_profile(tables, merchant=merchant, ref=ref, modality="touch")
    elif state == "too_few_features":
        _seed_profile(tables, merchant=merchant, ref=ref, spread=0.0)
    elif state == "budget_exhausted":
        _seed_profile(
            tables, merchant=merchant, ref=ref, escalation_count=profiles.PROFILE_MAX_ESCALATIONS, escalation_window_start=now
        )
    elif state == "breaker":
        _seed_profile(tables, merchant=merchant, ref=ref)
        # PROFILE_BREAKER_MAX other customers: the breaker counts customers.
        for index in range(profiles.PROFILE_BREAKER_MAX):
            tables.seed(
                "decision_audit", session_id="baska", profile_id=f"baska-{index}", reason="profile_deviation", shadow=False,
                decided_at=now,
            )
    else:
        _seed_profile(tables, merchant=merchant, ref=ref)
    if state == "thin_session":
        return _DEVIATING, 0b111, "pointer_mouse"
    if state in ("match", "rate_limited"):
        # rate_limited: a mature profile the session MATCHES, and the
        # customer's decision bucket exhausted (set by the sweep itself).
        return _MATCHING, _ALL_MEASURED, "pointer_mouse"
    return _DEVIATING, _ALL_MEASURED, "pointer_mouse"


_PROFILE_SWEEP_STATES = (
    "no_profile",
    "objected",
    "schema_mismatch",
    "immature",
    "other_modality",
    "thin_session",
    "too_few_features",
    "match",
    "deviating",
    "budget_exhausted",
    "breaker",
    "rate_limited",
)


def test_profile_layer_never_blocks_and_never_changes_the_score(api, monkeypatch):
    """T9, and the non-negotiable property, over HTTP on both endpoints.

    For every combination, against the same request with no customer named:
    risk_score and label are identical; a block, a verify or an unknown session
    is returned untouched, reason and message included; the payment outcome
    of the demo charge changes only by being declined; and the ONLY change the
    layer ever makes is allow|warn -> verify (told to the client as step_up),
    enforced, on a deviating mature profile within budget and breaker -- or
    for a customer whose decision bucket is exhausted, matching or not -- with
    no fresh step-up. When the session's step-up IS fresh, _apply_step_up turns
    that verify into allow/verified at once -- the customer has already proved
    themselves in this session -- so the action stays an approval and only the
    reason says why. Nothing else, in any state, moves anything.
    """
    main = api.main
    ts = _scorer_tests()
    monkeypatch.setitem(main.RATE_LIMITS, "profile", (10**9, 3600))
    calibrations = {
        "normal": SimpleNamespace(human_calibration=[3.0 + i * 0.3 for i in range(30)]),
        "human_in_90s": SimpleNamespace(human_calibration=[92.0 + i * 0.2 for i in range(30)]),
    }
    step_ups = {
        "none": None,
        "fresh": lambda: main.utcnow() - timedelta(seconds=5),
        "expired": lambda: main.utcnow() - timedelta(seconds=main.VERIFICATION_VALID_S + 5),
    }
    old = main.utcnow() - timedelta(seconds=main.DECISION_MAX_AGE_S + 60)

    def make_db(outcome, step_up, tables=None, flushes=()):
        session_kwargs, db_kwargs, calibration, cluster = _EVIDENCE_OUTCOMES[outcome]
        monkeypatch.setattr(main.scorer, "_bundle", calibrations[calibration])
        monkeypatch.setattr(main, "CLUSTER_ESCALATION_ENABLED", cluster)
        session = None
        if session_kwargs is not None:
            kwargs = dict(session_kwargs)
            if kwargs.get("last_seen_at") == "old":
                kwargs["last_seen_at"] = old
            session = ts._stub_session(**kwargs)
            session.verified_at = step_ups[step_up]() if step_ups[step_up] else None
        return api.db(session=session, tables=tables, flushes=flushes, **db_kwargs)

    from fastapi.testclient import TestClient

    # One client for the whole sweep, pointed at each stub database in turn:
    # ~1400 requests, and building a TestClient per request was most of the
    # runtime.
    client = TestClient(main.app)

    def post(endpoint, db, ref):
        session_id = str(uuid.uuid4())
        main._profile_breaker.update(checked_at=None, count=0)
        main.app.dependency_overrides[main.get_db] = lambda: db
        headers = {"X-DeepCheck-Token": main.sign_session(session_id)}
        if endpoint == "decision":
            body = {"session_id": session_id}
            if ref is not None:
                body["customer_ref"] = ref
                headers.update(_ACME)
            response = client.post("/api/decision", json=body, headers=headers)
        else:
            body = {"session_id": session_id, "amount": 49.9}
            if ref is not None:
                body["customer_ref"] = ref
            response = client.post("/api/demo/charge", json=body, headers=headers)
        assert response.status_code == 200, response.text
        out = response.json()
        return (out["decision"], out["status"]) if endpoint == "charge" else (out, None)

    checked = changed = 0
    for endpoint, merchant, ref in (("decision", "acme", _REF), ("charge", "demo", _DEMO_REF)):
        for outcome in _EVIDENCE_OUTCOMES:
            api.configure(escalation=False)
            bases = {step_up: post(endpoint, make_db(outcome, step_up), None) for step_up in step_ups}
            # What the evidence alone decided, before any step-up.
            evidence_action = bases["none"][0]["action"]
            for step_up in step_ups:
                base, base_status = bases[step_up]
                for escalation in (False, True):
                    api.configure(escalation=escalation)
                    for state in _PROFILE_SWEEP_STATES:
                        tables = _ProfileTables()
                        value, mask, pointer = _seed_profile_state(state, tables, merchant, ref)
                        flushes = _session_flushes(value, mask=mask, pointer=pointer)
                        limit = (0, 3600) if state == "rate_limited" else (10**9, 3600)
                        monkeypatch.setitem(main.RATE_LIMITS, "profile", limit)
                        got, status = post(endpoint, make_db(outcome, step_up, tables, flushes), ref)
                        where = f"{endpoint}/{outcome}/{state}/step-up={step_up}/escalation={escalation}"
                        checked += 1

                        assert got["risk_score"] == base["risk_score"], f"{where}: skor degisti"
                        assert got["label"] == base["label"], f"{where}: etiket degisti"
                        assert set(got) == set(base)
                        assert got["action"] != "block" or base["action"] == "block", f"{where}: katman engelledi"

                        acts = (
                            escalation
                            and evidence_action in ("allow", "warn")
                            and (
                                state in ("deviating", "rate_limited")
                                or (step_up == "fresh" and state in ("budget_exhausted", "breaker"))
                            )
                        )
                        if not acts:
                            assert got == base, f"{where}: {got} != {base}"
                            assert status == base_status
                            continue
                        changed += 1
                        if step_up == "fresh":
                            assert (got["action"], got["reason"]) == ("allow", "verified"), f"{where}: {got}"
                            assert base["action"] == evidence_action, where
                            assert status == base_status == ("charged" if endpoint == "charge" else None)
                        else:
                            assert (got["action"], got["reason"]) == ("verify", "step_up"), f"{where}: {got}"
                            assert got["message"] == main.REASON_MESSAGES["step_up"]
                            if endpoint == "charge":
                                assert (base_status, status) == ("charged", "declined")
    assert checked == 2 * len(_EVIDENCE_OUTCOMES) * 3 * 2 * len(_PROFILE_SWEEP_STATES)
    # allow and warn, both endpoints: deviating and rate-limited (3 step-up
    # states each) plus budget and breaker under a fresh step-up -- all only
    # when enforcing.
    assert changed == 2 * 2 * (3 + 3 + 2), changed


# --- the SOC block and the review surface (spec sections 5.5, 5.6) --------------

_REVIEW_KEY = "r" * 16 + "-review-operator-key-" + "3" * 8
_REVIEWER = {"X-Review-Operator": "denetci-1", "X-Review-Key": _REVIEW_KEY}
_PROFILE_BLOCK_KEYS = {
    "state",
    "modality",
    "reference_n",
    "probation_n",
    "deviation",
    "p_value",
    "p_value_low",
    "top_features",
    "escalated",
    "shadow",
    "synthetic",
}


def _dashboard_db(api, tables):
    session = _scorer_tests()._stub_session(12.0)
    session.created_at = datetime.now(timezone.utc)
    return api.db(session=session, tables=tables)


def _score(api, db, session_id):
    response = api.client(db).get(
        f"/api/score/{session_id}", headers={"X-Dashboard-Key": api.main.DASHBOARD_KEY}
    )
    assert response.status_code == 200, response.text
    return response


def test_score_endpoint_profile_block_is_uniform_and_minimal(api):
    """T34. The `profile` key is on every /api/score answer with exactly the
    same keys, whatever the layer said or whether it said anything: a
    dashboard-key holder cannot tell a profiled customer's session from any
    other by the shape of the answer. And it never carries the pseudonym, the
    stored vectors, the reference, or the merchant."""
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    reference_values = {repr(v) for r in tables.committed["customer_profile_vectors"] for v in r["vec"].values()}
    blocks = {}

    api.configure(layer=False)
    blocks["disabled"] = _score(api, _dashboard_db(api, tables), "kapali").json()["profile"]

    api.configure(escalation=True)
    blocks["no_profile"] = _score(api, _dashboard_db(api, tables), "referanssiz").json()["profile"]

    escalated = str(uuid.uuid4())
    _post_decision(api, _customer_session(api, tables, escalated), escalated)
    blocks["escalated"] = _score(api, _dashboard_db(api, tables), escalated)

    api.configure(escalation=False)
    shadow = str(uuid.uuid4())
    _post_decision(api, _customer_session(api, tables, shadow, value=_DEVIATING + 4), shadow)
    blocks["shadow"] = _score(api, _dashboard_db(api, tables), shadow)

    immature_tables = _ProfileTables()
    _seed_profile(immature_tables, n=5)
    immature = str(uuid.uuid4())
    _post_decision(api, _customer_session(api, immature_tables, immature), immature)
    blocks["immature"] = _score(api, _dashboard_db(api, immature_tables), immature)

    texts = {}
    for name, value in list(blocks.items()):
        if not isinstance(value, dict):
            texts[name] = value.text
            blocks[name] = value.json()["profile"]
    for name, block in blocks.items():
        assert set(block) == _PROFILE_BLOCK_KEYS, f"{name}: {sorted(block)}"
        assert isinstance(block["top_features"], list) and isinstance(block["reference_n"], int)
        assert isinstance(block["probation_n"], int)

    assert blocks["disabled"]["state"] == "disabled" and blocks["disabled"]["escalated"] is False
    assert blocks["no_profile"]["state"] == "no_profile"
    escalated_block = blocks["escalated"]
    assert escalated_block["state"] == "evaluated" and escalated_block["escalated"] is True
    assert escalated_block["shadow"] is False and escalated_block["modality"] == "mouse"
    assert escalated_block["reference_n"] == 20 and escalated_block["p_value"] <= profiles.PROFILE_ALPHA
    assert escalated_block["probation_n"] == 0
    assert [set(f) for f in escalated_block["top_features"]] == [{"feature", "z"}] * profiles.PROFILE_TOP_K
    assert {f["feature"] for f in escalated_block["top_features"]} <= set(FEATURE_NAMES)
    assert blocks["shadow"]["shadow"] is True and blocks["shadow"]["escalated"] is False
    assert blocks["shadow"]["state"] == "evaluated"
    assert blocks["immature"]["state"] == "immature" and blocks["immature"]["p_value"] is None

    for name, text in texts.items():
        for leaked in (pid, pid[:12], _REF, "acme", "customer_ref", "profile_id", "vec"):
            assert leaked not in text, f"{name}: /api/score '{leaked}' iceriyor"
        leaked_values = [v for v in reference_values if v in text]
        assert not leaked_values, f"{name}: referans vektor degerleri sizdi: {leaked_values[:3]}"

    # The dashboard key is still required for the block, as for the rest.
    unauthenticated = api.client(_dashboard_db(api, tables)).get(f"/api/score/{escalated}")
    assert unauthenticated.status_code == 401


def test_review_endpoint_needs_its_own_credential(api, monkeypatch, caplog):
    """T33. The review surface returns the vectors a decision was made on, so
    it takes a per-operator credential: the shared dashboard key is refused,
    as is a merchant key. Every authenticated read -- a 404 included -- leaves
    a profile_access_audit row naming the operator, and a read whose audit
    row cannot be written returns nothing."""
    import logging

    caplog.set_level(logging.DEBUG)
    main = api.main
    monkeypatch.setattr(main, "PROFILE_REVIEW_KEYS", {"denetci-1": _REVIEW_KEY})
    api.configure(escalation=True)
    tables = _ProfileTables()
    pid = _seed_profile(tables)
    session_id = str(uuid.uuid4())
    db = _customer_session(api, tables, session_id)
    assert _post_decision(api, db, session_id).json()["action"] == "verify"
    client = api.client(db)
    path = f"/api/profile/review/{session_id}"

    refused = [
        {},
        {"X-Dashboard-Key": main.DASHBOARD_KEY},
        {"X-Review-Operator": "denetci-1", "X-Review-Key": main.DASHBOARD_KEY},
        {"X-Review-Operator": "acme", "X-Review-Key": _MERCHANT_KEY_A},
        {**_ACME},
        {"X-Review-Operator": "denetci-1"},
        {"X-Review-Operator": "denetci-2", "X-Review-Key": _REVIEW_KEY},
        {"X-Review-Operator": "denetci-1", "X-Review-Key": ("anahtar-" + "ş" * 30).encode("utf-8")},
    ]
    for headers in refused:
        response = client.get(path, headers=headers)
        assert response.status_code == 401, (headers, response.text)
        assert response.json() == {"detail": "Yetkisiz inceleme erisimi"}
    assert tables.committed["profile_access_audit"] == [], "yetkisiz istek erisim kaydi yazdi"

    response = client.get(path, headers=_REVIEWER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == session_id and body["modality"] == "mouse"
    assert body["candidate"] == {"source": "flushes", "vec": {name: _DEVIATING for name in FEATURE_NAMES}}
    assert len(body["references"]) == body["reference_n"] == 20
    assert set(body["references"][0]) == {"vec", "created_at", "modality", "probation", "outcome"}
    assert body["probation_vectors"] == [] and body["probation_n"] == 0
    assert set(body["feature_stats"]) == set(FEATURE_NAMES)
    assert body["participating_features"] and len(body["leave_one_out_deviations"]) == 20
    [decision] = body["decisions"]
    assert (decision["reason"], decision["public_reason"], decision["profile_state"]) == (
        "profile_deviation",
        "step_up",
        "evaluated",
    )
    # With the buffer unchanged since the decision, the calibration set a
    # reviewer sees is the one the decision ranked against: each reference
    # scored against the other references plus this session (full conformal),
    # and re-ranking the audited deviation against it reproduces the audited
    # p-values -- which "recomputed" says in one flag.
    calibration = profiles.calibration_deviations(body["candidate"]["vec"], [r["vec"] for r in body["references"]])
    assert body["leave_one_out_deviations"] == calibration
    high, low = profiles.conformal_rank(decision["deviation"], body["leave_one_out_deviations"])
    assert (round(high, 4), round(low, 4)) == (decision["p_value"], decision["p_value_low"])
    assert decision["candidate_vec"] == {name: _DEVIATING for name in FEATURE_NAMES}
    recomputed = body["recomputed"]
    assert recomputed["matches_decision"] is True and recomputed["candidate_source"] == "decision_audit"
    assert (recomputed["deviation"], recomputed["p_value"]) == (decision["deviation"], decision["p_value"])
    assert pid not in response.text, "inceleme yaniti takma adi iceriyor"

    [access] = tables.committed["profile_access_audit"]
    assert (access["operator_id"], access["endpoint"], access["session_id"], access["profile_id"]) == (
        "denetci-1",
        "profile.review",
        session_id,
        pid,
    )

    # After the 24h telemetry retention: the session row and its flushes are
    # gone, and this session -- challenged, never passed, so never learned --
    # has no stored vector. The deviating decision kept what it compared, so
    # the reviewer still sees it: the case this surface exists for.
    gone = api.db(session=None, tables=tables)
    later = api.client(gone).get(path, headers=_REVIEWER)
    assert later.status_code == 200
    assert later.json()["candidate"] == {"source": "decision_audit", "vec": {name: _DEVIATING for name in FEATURE_NAMES}}
    assert later.json()["decisions"][0]["reason"] == "profile_deviation"
    assert later.json()["recomputed"]["matches_decision"] is True

    unknown = api.client(gone).get("/api/profile/review/hic-olmamis-oturum", headers=_REVIEWER)
    assert unknown.status_code == 404
    assert len(tables.committed["profile_access_audit"]) == 3, "404 erisim kaydi yazmadi"
    assert tables.committed["profile_access_audit"][-1]["profile_id"] is None

    # No audit row, no data.
    failing = _ProfileTables(fail_on=("insert", "profile_access_audit"))
    failing.committed = copy.deepcopy(tables.committed)

    class _FailingAdd(type(db)):
        def add(self, obj):
            if getattr(obj, "__tablename__", None) == "profile_access_audit":
                raise RuntimeError("erisim kaydi yazilamadi")
            super().add(obj)

    blocked = api.client(_FailingAdd(session=db.session, tables=failing, flushes=db.flushes)).get(path, headers=_REVIEWER)
    assert blocked.status_code == 503 and "vec" not in blocked.text
    for leaked in (pid, pid[:12], _REF):
        assert leaked not in caplog.text


def test_review_says_when_it_no_longer_reproduces_the_decision(api, monkeypatch):
    """The review recomputes against the references stored when the reviewer
    asks, and those move on: here the customer checks out normally twice after
    the contested decision, and both sessions are learned. The audit row keeps
    the decision's own numbers; the review shows them beside the recomputation
    and says the two no longer agree, instead of presenting today's set as the
    one the decision used. A matching decision stores no vector at all."""
    main = api.main
    monkeypatch.setattr(main, "PROFILE_REVIEW_KEYS", {"denetci-1": _REVIEW_KEY})
    api.configure(escalation=True)
    tables = _ProfileTables()
    _seed_profile(tables)
    contested = str(uuid.uuid4())
    db = _customer_session(api, tables, contested)
    assert _post_decision(api, db, contested).json()["action"] == "verify"
    [audit] = _audit_rows(tables, contested)

    for _ in range(2):
        sid = str(uuid.uuid4())
        assert _post_decision(api, _customer_session(api, tables, sid, value=_MATCHING), sid).json()["action"] == "allow"
        [matching] = _audit_rows(tables, sid)
        assert matching["candidate_vec"] is None, "sapmayan karar vektor sakladi"

    review = api.client(api.db(session=None, tables=tables)).get(f"/api/profile/review/{contested}", headers=_REVIEWER)
    assert review.status_code == 200, review.text
    body = review.json()
    [decision] = body["decisions"]
    assert (decision["deviation"], decision["p_value"]) == (audit["deviation"], audit["p_value"])
    recomputed = body["recomputed"]
    assert recomputed["candidate_source"] == "decision_audit"
    assert recomputed["matches_decision"] is False, "degisen referanslar karari yeniden uretiyormus gibi sunuldu"
    assert recomputed["deviation"] != audit["deviation"]


def test_review_keys_are_their_own_secret():
    """Section 5.6 at boot. The review credential list parses like the
    merchant list, and a review key may not double as any other secret -- a
    merchant key, the profile key or the dashboard key -- in either mode,
    without the key text reaching the boot error."""
    results = _boot_results()
    for mode in ("debug", "production"):
        assert results[f"{mode}/review-keys"].get("reviewers") == ["denetci-1"], results[f"{mode}/review-keys"]
        assert results[f"{mode}/defaults"].get("reviewers") == []
        for case in ("review-key-is-merchant-key", "review-key-is-profile-key", "review-key-is-dashboard-key"):
            result = results[f"{mode}/{case}"]
            assert "error" in result, f"{mode}/{case} acilista reddedilmedi: {result}"
            assert "PROFILE_REVIEW_KEYS" in result["error"]
            for secret in (_REVIEW_KEY, _MERCHANT_KEY_A, _PROFILE_KEY):
                assert secret not in result["error"]


def _doc(name: str) -> str:
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", name)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _published_evaluation() -> str:
    return _doc("profile-evaluation.md")


def _published_latency():
    """Section 11 of docs/profile-evaluation.md, parsed.

    Returns {topology: {path: (p50, p95)}} in the page's row order, where path
    is one of off / shadow / escalates / learns. Every figure is read off the
    page, so the numbers below are whatever the last lab run published."""
    import re

    text = _published_evaluation()
    section = text[text.index("## 11. Latency") : text.index("## 12. ")]
    rows = re.findall(
        r"^\| ([^|]+?) \| " + r" \| ".join([r"([0-9.]+) / ([0-9.]+) ms"] * 4) + r" \|$",
        section,
        re.MULTILINE,
    )
    assert len(rows) == 4, f"bolum 11 tablosu okunamadi: {rows}"
    return section, [
        (
            row[0].strip(),
            {
                "off": (row[1], row[2]),
                "shadow": (row[3], row[4]),
                "escalates": (row[5], row[6]),
                "learns": (row[7], row[8]),
            },
        )
        for row in rows
    ]


def test_report_corrections_latency_matches_the_published_evaluation():
    """docs/rapor-duzeltmeleri.md exists to replace the pre-evaluation
    report's unsupported numbers with sourced ones, so a stale number in IT is
    the worst kind: it is the number the team pastes into the final report.

    Every duration it attributes to `docs/profile-evaluation.md` §11 -- in the
    latency table, in the summary row of claim 4, in the paste-ready
    replacement text, and in the honest-claims table -- has to be a figure
    that page actually carries, in the same role. F3 regenerated that page and
    this file was left behind: it still quoted p95 8,3 ms for the layer off
    and 43,2 ms for enforcing, which appear nowhere on the page, while
    TECHNICAL_GUIDE.md §17 and docs/juri-cevaplari.md already carried 7,4 and
    34,8 ms -- three jury-facing documents, two answers, one measurement.

    Turkish writes the decimal separator as a comma, so the figures are
    compared after normalising it."""
    import itertools
    import re

    section, published = _published_latency()
    report = _doc("rapor-duzeltmeleri.md")

    def figures(line):
        return [f.replace(",", ".") for f in re.findall(r"\d+,\d+", line)]

    container, container_repeat, windows, windows_repeat = published
    assert container[0].startswith("container") and windows[0].startswith("Windows"), published

    # 1. The latency table, row by row. The compute_risk row is sourced from
    # backend/scorer.py, not from the page, so it is not checked here.
    after_heading = report[report.index("**Bugün doğru olan: süre") :].split("\n")
    table = "\n".join(itertools.takewhile(lambda line: not line.startswith("-"), after_heading))
    lines = [line for line in table.split("\n") if line.startswith("| `/api/decision`")]
    expected = {
        "müşteri profili kapalı": "off",
        "profil gölge modda": "shadow",
        "profil uyguluyor ve öğreniyor": "learns",
        "profil uyguluyor ve ek doğrulama istiyor": "escalates",
    }
    assert len(lines) == len(expected), table
    for line in lines:
        [path] = [p for label, p in expected.items() if label in line]
        assert figures(line) == [
            *container[1][path],
            *container_repeat[1][path],
        ], f"{path}: {line}"

    # 2. The Windows row: the layer-off p95 of both runs, then the range over
    # every profile-on p95, then how many configurations broke the budget.
    [line] = [line for line in table.split("\n") if "Windows ana makinesinden" in line]
    on = [
        windows[1][path][1] for path in ("shadow", "escalates", "learns")
    ] + [windows_repeat[1][path][1] for path in ("shadow", "escalates", "learns")]
    assert figures(line) == [
        windows[1]["off"][1],
        windows_repeat[1]["off"][1],
        min(on, key=float),
        max(on, key=float),
    ], line
    over = [p95 for _, paths in published for _, p95 in paths.values() if float(p95) > 50]
    turkish = {1: "bir", 2: "iki", 3: "üç", 4: "dört", 5: "beş", 6: "altı"}
    assert f"{turkish[len(over)]} yapılandırma" in line, line
    # And the page's own prose names the same set, so the two cannot drift.
    assert section.count("ms)") >= len(over)
    for p95 in over:
        assert f"({p95} ms)" in section, p95

    # 3. The three places that quote one pair for the container: the best case
    # with the layer off, and the worst case with it on.
    best = container[1]["off"][1]
    worst = max(
        (p95 for _, paths in (container, container_repeat) for key, (_, p95) in paths.items() if key != "off"),
        key=float,
    )
    for marker in (
        "| 4 | AWS Lambda",
        "> içinde, müşteri profili kapalıyken",
        "| 10 | Skorlama",
    ):
        [line] = [line for line in report.split("\n") if line.startswith(marker)]
        quoted = [f for f in figures(line) if f != "17.7"]  # compute_risk
        assert quoted == [best, worst], f"{marker}: {quoted} != {[best, worst]}"


def _markdown_tables(text: str):
    """(table text, the first non-blank line after it) for every table."""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].startswith("|"):
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        yield "\n".join(lines[start:i]), (lines[j] if j < len(lines) else "")


def test_profile_constants_match_the_published_evaluation():
    """docs/profile-evaluation.md is generated by profile_lab.py and is the
    page a jury reads. The three constants it reports as MEASURED must be the
    ones profiles.py ships, or the page describes a layer that does not exist.

    It must also still say what every number was measured on: the verbatim
    no-real-customer sentence (or, under the browser-lab tables, the lab's own
    verbatim sentence) directly under EVERY table, and the lower-bound caveat
    under every table that carries a same-person or false-challenge figure.

    And it must describe the rank that ships: its calibration check compares
    profiles.evaluate_profile verdict for verdict with the lab's independent
    full-conformal implementation, so any disagreement means the page's rates
    belong to some other rank. The before/after table at the top has to agree
    with the sections it summarises -- a headline figure that reads one way at
    the top and another in section 5 is a page nobody can quote -- and its
    before column has to be the first run's record, profile_lab.BEFORE_S4, not
    numbers retyped by hand."""
    import re

    import profile_lab

    text = _published_evaluation()

    for name in ("PROFILE_MIN_FEATURE_OBS", "PROFILE_SCALE_FLOOR", "PROFILE_TOP_K"):
        # | `NAME` | spec starting value | in profiles.py when this ran | **measured** | rule |
        match = re.search(rf"^\| `{name}` \| [^|]+ \| [^|]+ \| \*\*([0-9.]+)\*\* \|", text, re.MULTILINE)
        assert match, f"{name} is missing from the constants table"
        assert float(match.group(1)) == float(getattr(profiles, name)), (
            f"{name}: profiles.py ships {getattr(profiles, name)}, the published evaluation measured "
            f"{match.group(1)}; re-run profile_lab.py or update the constant"
        )

    assert profile_lab.SYNTHETIC_SENTENCE == "Sentetik kimlikler üzerinde ölçüldü; gerçek müşteri verisi yoktur."
    assert "No real customer has been measured" in text
    tables = list(_markdown_tables(text))
    assert len(tables) >= 15, len(tables)
    for table, after in tables:
        header = table.split("\n")[0]
        assert after.startswith("> **") and (
            profile_lab.SYNTHETIC_SENTENCE in after or profile_lab.LAB_SENTENCE in after
        ), f"tablonun altinda sentetik veri cumlesi yok: {header}"
        if "false challenge" in table.lower() or "same person" in table.lower():
            assert "**lower bound**" in after, f"tablo alt sinir uyarisi tasimiyor: {header}"

    # The rank the page measured is the rank that ships.
    section5 = text[text.index("## 5. ") : text.index("## 6. ")]
    differ = {
        m: (int(n), int(total))
        for m, n, total in re.findall(
            r"^- (mouse|keyboard): verdicts that differ between the shipped and the independent implementation: "
            r"(\d+) of (\d+)\.$",
            section5,
            re.MULTILINE,
        )
    }
    assert set(differ) == {"mouse", "keyboard"}, differ
    assert all(n == 0 and total > 0 for n, total in differ.values()), differ

    # The before/after table: first on the page, its after column equal to the
    # sections', its before column equal to the recorded first run.
    before_after = text[text.index("## Before and after") : text.index("## 1. ")]
    headline = {
        m: (same, different)
        for m, same, different in re.findall(
            r"^\| (mouse|keyboard) \| ([0-9.]+% \([^)]*\)) \| [0-9.]+% \| ([0-9.]+% \([^)]*\)) \| ",
            section5,
            re.MULTILINE,
        )
    }
    assert set(headline) == {"mouse", "keyboard"}, headline
    b4 = profile_lab.BEFORE_S4
    for modality, (same, different) in headline.items():
        row = re.search(
            rf"^\| same person: false challenge, {modality} \(\*\*lower bound\*\*\) \| ([^|]+) \| ([^|]+) \|",
            before_after,
            re.MULTILINE,
        )
        assert row, f"{modality}: same-person row missing, or no longer labelled a lower bound"
        assert (row.group(1).strip(), row.group(2).strip()) == (b4["same"][modality], same)
        row = re.search(
            rf"^\| different person: escalated, {modality} \| ([^|]+) \| ([^|]+) \|", before_after, re.MULTILINE
        )
        assert row and (row.group(1).strip(), row.group(2).strip()) == (b4["different"][modality], different)
        row = re.search(
            rf"^\| calibration check, {modality}: [^|]+ \| ([^|]+) \| shipped rank is full conformal; (\d+) of (\d+) "
            r"verdicts differ",
            before_after,
            re.MULTILINE,
        )
        assert row and row.group(1).strip() == b4["calibration"][modality], modality
        assert (int(row.group(2)), int(row.group(3))) == differ[modality]
    assert profile_lab.SYNTHETIC_SENTENCE in before_after
    for label in b4["latency"]:
        assert f"latency p50 / p95 ms, {label}:" in before_after, label

    section7 = text[text.index("## 7. Poisoning") : text.index("## 8. ")]
    cap = profiles.PROFILE_PROBATION_MAX
    probation = dict(re.findall(r"^\| (\d+) \| \d+ \| \d+ / \d+ \| ([0-9.]+%) \(", section7, re.MULTILINE))
    row = re.search(
        rf"^\| attacker escalated with 0 / 1 / {cap} of his sessions learned after a passed step-up \(mouse\) \| "
        r"([^|]+) \| ([^|]+) \|",
        before_after,
        re.MULTILINE,
    )
    assert row, "before/after: the probation poisoning row is missing"
    assert row.group(1).strip().startswith(
        " / ".join(b4["poisoning"][k][0].split(" ")[0] for k in (0, 1, cap))
    )
    assert row.group(2).strip().startswith(" / ".join(probation[str(k)] for k in (0, 1, cap)))
    rescued = re.findall(
        r"^\| (mouse|keyboard) \| [0-9.]+% \([^)]*\) \| \d+ \| ([0-9.]+%) \([^)]*\) \| ([0-9.]+%) \(",
        section7,
        re.MULTILINE,
    )
    row = re.search(r"^\| rescued, then returning: [^|]+ \| ([^|]+) \| ([^|]+) \|", before_after, re.MULTILINE)
    assert row and len(rescued) == 2, rescued
    assert row.group(1).strip().endswith(" / ".join(counted for _, _, counted in rescued))
    assert row.group(2).strip() == " / ".join(again for _, again, _ in rescued)


def test_published_poisoning_section_says_probation_vectors_are_not_references():
    """Section 7 of docs/profile-evaluation.md is where a jury reads that one
    passed step-up used to shield an attacker. Its prose is rendered by
    profile_lab's own functions from the published tables, so the page cannot
    go on telling a reader that probation vectors count -- or that the
    grandchild is remembered -- after the code stopped doing either.

    The page must also show the curve FLAT. The lab builds each buffer through
    profiles.admit_vector and reads the references back by the decision path's
    rule, so a probation vector reaching the statistic would show up here as a
    rate that moves with k or a reference set that is not identical to the one
    with no attacker session -- and poisoning_reading would then say so in
    words this test would not find."""
    import re

    import profile_lab

    text = _published_evaluation()
    section = text[text.index("## 7. Poisoning") : text.index("## 8. ")]

    probation_rows = re.findall(r"^\| (\d+) \| (\d+) \| (\d+) / (\d+) \| ([0-9.]+%) \(", section, re.MULTILINE)
    probation = {int(k): rate for k, _, _, _, rate in probation_rows}
    identical = all(same == total for _, _, same, total, _ in probation_rows)
    promoted = {int(k): rate for k, rate in re.findall(r"^\| (\d+) \| ([0-9.]+%) \(", section, re.MULTILINE)}
    rescued = {
        m: (again, counted)
        for m, again, counted in re.findall(
            r"^\| (mouse|keyboard) \| [0-9.]+% \([^)]*\) \| \d+ \| ([0-9.]+%) \([^)]*\) \| ([0-9.]+%) \(",
            section,
            re.MULTILINE,
        )
    }
    cap = profiles.PROFILE_PROBATION_MAX
    assert {0, 1, cap} <= set(probation), probation
    assert max(probation) > cap, "the storage cap itself was not exercised"
    assert all(int(stored) <= cap for _, stored, _, _, _ in probation_rows)
    assert {0, 1, cap} <= set(promoted), promoted
    assert set(rescued) == {"mouse", "keyboard"}, rescued

    assert identical and len(set(probation.values())) == 1, (
        f"the published probation curve is not flat: {probation_rows}"
    )

    intro = profile_lab.POISONING_INTRO.format(
        probation_max=cap, heal_after=profiles.PROFILE_HEAL_AFTER, heal_keep=profiles.PROFILE_HEAL_KEEP
    )
    reading = profile_lab.poisoning_reading(
        probation, promoted, identical, cap, profiles.PROFILE_HEAL_AFTER, profiles.PROFILE_MAX_ESCALATIONS
    )
    grandchild = profile_lab.grandchild_reading(
        {m: again for m, (again, _) in rescued.items()},
        {m: counted for m, (_, counted) in rescued.items()},
        profiles.PROFILE_HEAL_AFTER,
        profiles.PROFILE_MAX_ESCALATIONS,
    )
    assert intro in section, "bolum 7 girisi profile_lab.POISONING_INTRO ile uyusmuyor"
    assert reading in section, "bolum 7 yorumu profile_lab.poisoning_reading ile uyusmuyor"
    assert grandchild in section, "bolum 7 torun yorumu profile_lab.grandchild_reading ile uyusmuyor"
    assert "the curve is flat" in reading
    assert "(cap)" not in section


def test_frontend_profile_labels_match_the_backend():
    """The SOC card and the Demo page's leak test carry copies of backend
    names, because the profile block deliberately ships no constants. Each copy
    is pinned here, so a change on either side fails a test instead of shipping
    a card that prints "7 / 20" for a layer that matures at 19, a state with no
    Turkish label, or a leak test that no longer knows a renamed feature."""
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(*parts):
        with open(os.path.join(root, *parts), encoding="utf-8") as fh:
            return fh.read()

    panel = read("frontend", "src", "components", "ProfilePanel.jsx")
    match = re.search(r"^export const PROFILE_MIN_SESSIONS = (\d+);", panel, re.MULTILINE)
    assert match, "PROFILE_MIN_SESSIONS is missing from ProfilePanel.jsx"
    assert int(match.group(1)) == profiles.PROFILE_MIN_SESSIONS

    def object_keys(name, text):
        block = re.search(rf"^export const {name} = \{{(.*?)^\}};", text, re.MULTILINE | re.DOTALL)
        assert block, f"{name} is missing from ProfilePanel.jsx"
        return re.findall(r"^\s*([a-z_]+):", block.group(1), re.MULTILINE)

    state_keys = object_keys("PROFILE_STATE_LABELS", panel)
    assert sorted(state_keys) == sorted(profiles.PROFILE_STATES)
    modality_keys = object_keys("MODALITY_LABELS", panel)
    assert sorted(modality_keys) == sorted(profiles.MODALITIES)

    demo_test = read("frontend", "src", "pages", "Demo.test.jsx")
    names = re.search(r"^const FEATURE_NAMES = \[(.*?)^\];", demo_test, re.MULTILINE | re.DOTALL)
    assert names, "FEATURE_NAMES is missing from Demo.test.jsx"
    assert re.findall(r'"([a-z_]+)"', names.group(1)) == list(FEATURE_NAMES)
