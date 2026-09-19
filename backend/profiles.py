"""Per-customer behavioural profile: the statistic, and nothing else.

THE CONTRACT, in one sentence: a customer's own behavioural history may ask for
EXTRA VERIFICATION and nothing else. It never blocks, never approves, never
lowers a risk score, never raises a risk score, and a session that *matches* the
profile gets no benefit whatsoever -- a bot replaying the victim's own recorded
behaviour matches the victim's profile perfectly, so trust-on-match would hand
the attacker the win.

What this layer is for, stated so a jury hears it from us first: it is the only
control in DeepCheck aimed at human ACCOUNT TAKEOVER. It contributes nothing
against card-testing bots, which arrive at guest checkout with no customer
reference and never reach this code. Its measurable output is challenges, and
its fraud-prevention value is bounded by the strength of the merchant's step-up
channel: if the attacker has already phished the OTP, this layer asks a question
he can answer.

This module is pure: no database, no FastAPI, no model bundle. The same shape as
scorer.py -- constants carrying the reasoning that set them, and functions that
can be measured and tested without infrastructure. The persistence, the decision
wiring and the endpoints live elsewhere; what is here is only "how far is this
session from this customer's own past, and is that far enough to be surprising".

HONESTY NOTE, which applies to every number in this file: there is no real
customer data anywhere in this project. Nothing here has been validated against
one person across many sessions and devices. Values marked MEASURED were set by
backend/profile_lab.py (docs/profile-evaluation.md, spec section 10.2) against
SYNTHETIC identities, and a synthetic identity is self-consistent in a way no
real person is -- so any false-challenge rate measured there is a LOWER BOUND,
biased in the direction that flatters the product. The layer therefore ships
off.

Which code those numbers were measured on: the rates quoted in the comments
below are from the 2026-09-18 run, which measured the code that ships -- the
full-conformal rank (calibration_deviations) with probation vectors excluded
from the references (PROFILE_PROBATION_MAX). Figures dated 2026-09-16 are the
first run's, measured under the leave-one-out rank while probation vectors
still counted; they are quoted only where they explain why the code changed,
and the page's before/after table holds both columns. The same seeds drew the
same synthetic identities in both runs. The knee behind
PROFILE_MIN_FEATURE_OBS and the percentile behind PROFILE_SCALE_FLOOR do not
depend on the rank and came out identical.
"""

import hashlib
import hmac
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# FEATURE_SCHEMA_VERSION lives next to FEATURE_NAMES in lstm_model.py and is
# re-exported from here, never redefined: one home, so the version a profile is
# stamped with and the version it is compared under cannot drift apart. Stored
# customer profiles are compared only within one version -- see the note there.
from lstm_model import FEATURE_NAMES, FEATURE_SCHEMA_VERSION


# --- Pseudonymous identity ---------------------------------------------------

PROFILE_ID_CONTEXT = b"deepcheck-profile-v1"


def _lp(s: str) -> bytes:
    """Length-prefixed encoding of one field."""
    b = s.encode("utf-8")
    return len(b).to_bytes(4, "big") + b


def derive_profile_id(merchant_id: str, customer_ref: str, key: bytes) -> str:
    """HMAC-SHA256 hex, 64 chars, namespaced per merchant.

    Length-prefixed fields, never concatenation: merchant "a|b" with reference
    "c" must not derive the same id as merchant "a" with reference "b|c". With a
    separator-based encoding that collision is reachable by a merchant simply
    choosing an id containing the separator, and it would link one person's
    profile across two merchants -- the exact cross-merchant linkability this
    namespacing exists to prevent.

    The raw customer_ref is never stored, never logged, never returned, and
    never placed in a URL path or query string. It exists only inside POST
    bodies and inside this function's arguments.

    Note the honest trade, because it is not free: an HMAC pseudonym cannot be
    re-keyed without the raw references, which this system deliberately never
    stores. This key is therefore long-lived BY DESIGN, and its compromise
    re-identifies the whole table. That is exactly why it must not also be the
    token-signing secret or the dashboard key.
    """
    if not isinstance(key, (bytes, bytearray)) or not key:
        raise ValueError("profil anahtari gerekli")
    msg = PROFILE_ID_CONTEXT + _lp(merchant_id) + _lp(customer_ref)
    return hmac.new(bytes(key), msg, hashlib.sha256).hexdigest()


# --- Input modality ----------------------------------------------------------

MODALITY_TOUCH = "touch"
MODALITY_MOUSE = "mouse"
MODALITY_KEYBOARD = "keyboard"
MODALITIES = (MODALITY_TOUCH, MODALITY_MOUSE, MODALITY_KEYBOARD)


def session_modality(client_signals: list[dict] | None) -> str:
    """Input modality from the pointer mix the SDK already records.

    Behaviour is a property of (person x device x input modality), not of a
    person alone. Touch produces no mousemove at all, so hiz_otokorelasyonu,
    yon_tutarliligi and tiklama_oncesi_hareket are drawn from an entirely
    different distribution on a phone than on a mouse. Pooling the two gives a
    profile that is either permanently blind -- the within-person variance is
    swamped by the mixture -- or permanently hostile to whichever device the
    customer uses less often. So the profile is compared only against reference
    sessions of the SAME modality, with no pooled fallback anywhere: a modality
    with too few references is immature, and the layer abstains.

    SELF-REPORTED, and therefore evadable: an attacker who knows this layer
    exists claims a fresh modality and the layer abstains for him. That is
    inherent to any escalation-only control keyed on attacker-controlled
    attributes -- it is the same limitation already written down for the
    behaviour-bucket cluster rule -- and it is a reason to write it down, not a
    reason to pool.
    """
    touch = 0
    pointer = 0
    for signals in client_signals or []:
        if not isinstance(signals, dict):
            continue
        touch += _as_count(signals.get("pointer_touch"))
        pointer += _as_count(signals.get("pointer_mouse"))
        pointer += _as_count(signals.get("pointer_pen"))
    if touch + pointer == 0:
        return MODALITY_KEYBOARD
    return MODALITY_TOUCH if touch > pointer else MODALITY_MOUSE


def _as_count(value) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


# --- Session vector ----------------------------------------------------------
#
# One session becomes ONE vector: the per-feature median over that session's
# non-provisional flushes. Not a mean -- one odd flush must not set the
# customer's centre, and a customer's own sessions are exactly where one odd
# flush is most likely to be innocent.

# Mirrors main.MIN_FLUSHES_FOR_DECISION (3). A session the decision path was not
# willing to judge is not a session worth learning a person from.
PROFILE_MIN_FLUSHES = 3

# Per feature, within one session: measured in at least this many surviving
# flushes before the session is allowed to have an opinion about it. A median
# over a single observation is that observation.
PROFILE_MIN_FLUSH_OBS = 2

# Mirrors scorer.BUCKET_MIN_MEASURED (4), and for the same measured reason: when
# the behaviour bucket was built from vectors that were mostly neutral
# fallbacks, every thin flush landed in one enormous cell and 40% of legitimate
# sessions were escalated on it. Two sessions cannot be said to behave alike
# when neither one's behaviour was observed.
PROFILE_MIN_FEATURES = 4

# Mirrors scorer.MIN_MEASURED_FOR_CONFIDENT_SCORE (6). Kept as a literal rather
# than imported: scorer pulls shap and torch, i.e. ~18 seconds of import for two
# integers, and this module is meant to stay importable by a test that touches
# no model stack at all. test_profiles.py imports scorer once and asserts the
# two values are equal, so the duplication cannot drift silently.
PROVISIONAL_MEASURED_MIN = 6

_MAD_TO_SIGMA = 1.4826  # MAD -> standard-deviation scale for a normal sample


def session_vector(rows: list[dict]) -> dict | None:
    """Collapse a session's flushes into one vector, or abstain.

    `rows` are the session's most recent flushes (at most main.SPRT_MAX_FLUSHES
    of them), each a mapping carrying `measured_mask` plus the twelve feature
    columns.

    Two rules do all the work here:

    1. A flush whose measured_mask popcount is below PROVISIONAL_MEASURED_MIN is
       dropped ENTIRELY. scorer already calls such a flush provisional; a vector
       built from them is a vector of NEUTRAL_DEFAULTS, and a per-customer
       profile built over defaults measures "how much telemetry did this session
       produce", not "is this the same person".
    2. A feature contributes only from the flushes whose mask bit is actually
       set. Without the mask nothing downstream can tell a measured 0.3 from a
       fallback 0.3, so a row with measured_mask missing (NULL, i.e. written
       before the column existed) is dropped rather than guessed at.

    Returns {"vec", "disp", "flush_count"} or None. `disp` is the per-feature
    MAD across this session's flushes: RECORDED, NOT SCORED. The mid-session
    handover it would catch is already caught, measured 185/185, by scorer's
    per-flush LEVEL_SHIFT_POINTS rule, and adding a second unmeasured term to
    the statistic would be inventing a threshold.
    """
    observations: dict[str, list[float]] = {name: [] for name in FEATURE_NAMES}
    kept = 0
    for row in rows or []:
        mask = row.get("measured_mask")
        if mask is None:
            continue
        try:
            mask = int(mask)
        except (TypeError, ValueError):
            continue
        if mask < 0 or bin(mask).count("1") < PROVISIONAL_MEASURED_MIN:
            continue
        kept += 1
        for index, name in enumerate(FEATURE_NAMES):
            if not (mask >> index) & 1:
                continue
            value = _finite(row.get(name))
            if value is not None:
                observations[name].append(value)

    if kept < PROFILE_MIN_FLUSHES:
        return None

    vec: dict[str, float | None] = {}
    disp: dict[str, float | None] = {}
    for name in FEATURE_NAMES:
        values = observations[name]
        if len(values) < PROFILE_MIN_FLUSH_OBS:
            vec[name] = None
            disp[name] = None
            continue
        centre = _median(values)
        vec[name] = centre
        disp[name] = _median([abs(v - centre) for v in values])

    if sum(1 for value in vec.values() if value is not None) < PROFILE_MIN_FEATURES:
        return None
    return {"vec": vec, "disp": disp, "flush_count": kept}


# --- Reference statistics ----------------------------------------------------
#
# There is no mean/variance state anywhere in this design. The reference set IS
# the statistic: at most PROFILE_BUFFER_MAX stored session vectors per (profile,
# modality), with centre and scale recomputed on every read.
#
# Welford / incremental statistics were considered and deliberately NOT built.
# A bounded buffer is exactly reversible (unlearning a disputed session is one
# row delete, not an inverse update that does not exist), self-forgetting (the
# oldest reference falls out by construction, so the profile cannot be anchored
# to who the customer was two years ago), immune to the lost-update race that a
# read-modify-write of a JSON summary has under four uvicorn workers, and gives
# "reduced weight" for free: one absorbed vector is 1/20 of a reference set,
# which is a bound rather than a tuning parameter. Weighted Welford would also
# have been simply wrong -- the standard update assumes frequency weights, so
# feeding it a "reliability" weight produces a variance that is neither the
# weighted nor the unweighted one.

# A feature must have been measured in at least this many reference sessions
# before it is allowed to participate. Below it the median and the MAD are
# estimated from too little to carry a customer's challenge.
# MEASURED on SYNTHETIC identities (backend/profile_lab.py, 200 identities x
# mouse/keyboard; docs/profile-evaluation.md section 3): the relative RMS error
# of the 1.4826*MAD scale estimate against observation count, knee taken by a
# rule fixed before the run (largest distance below the chord, n = 2..19). The
# error falls 0.659 -> 0.511 -> 0.440 -> 0.388 over n = 2, 4, 6, 8 and only
# 0.348 -> 0.250 over the next eleven; the knee is 8, which is where the spec's
# starting value already sat. Even at 8 the scale is ~39% off, and it is the
# rank against the customer's own sessions, not this constant, that has to
# absorb that -- whose measured cost is the false-challenge table (section 5).
PROFILE_MIN_FEATURE_OBS = 8

# Below this scale a feature is EXCLUDED, never floored. This is the single most
# important false-positive fix in the layer.
#
# odak_degisimi is an integer count that is 0 for most people in most sessions.
# Floor its scale and a customer who alt-tabs ONCE to read the SMS code this
# very system just sent them produces an enormous z, lands in the top 3 every
# time, and is challenged for doing exactly what the previous challenge asked.
# "Never varied in 19 sessions" is not evidence that varying once is fraud. A
# feature with no spread carries no information about this customer and must not
# be allowed to decide.
#
# MEASURED on SYNTHETIC identities (backend/profile_lab.py; docs/profile-
# evaluation.md section 4): the 5th percentile of the NON-ZERO per-feature
# 1.4826*MAD over 3547 synthetic 20-session reference sets was 0.00630, rounded
# down to two significant figures. Zero scales (698 of the 3547 -- odak_degisimi
# everywhere, tiklama_yogunlugu on keyboard) are left out of the percentile:
# they are excluded by any positive floor, and counting them would put the
# floor at zero, which is the flooring failure described above.
#
# The spec's starting value, 0.02, excluded zaman_kuantasyonu in 100% of the
# reference sets (its within-person spread has a median of 0.006-0.008),
# although that spread is small rather than absent. On the same synthetic data
# (docs/profile-evaluation.md section 2, the shipped rank) the change moved
# keyboard sessions from 88.1% abstained / 3.2% of different people escalated
# to 19.6% / 26.4%, at a same-person false-challenge rate of 0.3% -> 3.8%;
# mouse sessions moved 5.0% -> 4.9% false challenge and 47.9% -> 47.5%
# escalation. Those are lower bounds on real false challenges, like every
# number measured on synthetic people.
PROFILE_SCALE_FLOOR = 0.0062

# The deviation score is the mean of the K largest per-feature z values.
# MEASURED on SYNTHETIC identities (backend/profile_lab.py; docs/profile-
# evaluation.md section 6), by a rule fixed before the run: the K in
# {1, 2, 3, 12} with the highest rate of escalating a DIFFERENT synthetic person
# at alpha 0.05, but staying at 3 -- what the SOC panel can show a reviewer --
# unless another K beats it with a paired identity-bootstrap interval excluding
# zero. Nothing did: K = 1, 2, 3, 12 escalated 29.4%, 34.8%, 36.9% and 37.2% of
# different-person sessions (both modalities pooled). K = 1 and 2 lay wholly
# below 3; K = 12 came out 0.3 pp above it with an interval of -0.8 to +1.3 pp,
# which includes zero, so by the rule 3 stays -- and 3 is what the SOC panel
# shows. The same-person rate barely moves with K (mouse 4.2-4.9%), because
# the rank, not K, sets it.
PROFILE_TOP_K = 3


@dataclass(frozen=True)
class FeatureStat:
    """Centre and scale for one feature over one reference set."""

    centre: float
    scale: float
    n_obs: int


def feature_stats(references: list[dict]) -> dict[str, FeatureStat]:
    """Median and MAD-derived scale per feature, recomputed on read.

    `references` are the stored session vectors ({feature: float | None}), all
    of one profile, one modality and one feature_schema_version. A null means
    the feature was not measured in that session and contributes nothing --
    neither to the centre nor to n_obs.

    Median and MAD rather than mean and standard deviation because a single
    adversarial or accidental reference must not be able to set the customer's
    scale: widening the profile is the cheap poisoning attack, and the mean and
    the standard deviation are both unbounded in one observation.
    """
    stats: dict[str, FeatureStat] = {}
    for name in FEATURE_NAMES:
        values = []
        for reference in references:
            value = _finite(reference.get(name))
            if value is not None:
                values.append(value)
        if not values:
            stats[name] = FeatureStat(centre=0.0, scale=0.0, n_obs=0)
            continue
        centre = _median(values)
        scale = _MAD_TO_SIGMA * _median([abs(v - centre) for v in values])
        stats[name] = FeatureStat(centre=centre, scale=scale, n_obs=len(values))
    return stats


def participating_features(candidate: dict, stats: dict[str, FeatureStat]) -> list[str]:
    """Features allowed to decide: measured in this session, measured often
    enough in the profile, and with real spread in the profile."""
    chosen = []
    for name in FEATURE_NAMES:
        if _finite(candidate.get(name)) is None:
            continue
        stat = stats.get(name)
        if stat is None or stat.n_obs < PROFILE_MIN_FEATURE_OBS:
            continue
        if not math.isfinite(stat.scale) or stat.scale < PROFILE_SCALE_FLOOR:
            continue
        chosen.append(name)
    return chosen


def deviation(
    values: dict,
    stats: dict[str, FeatureStat],
    features: list[str],
) -> tuple[float | None, list[dict]]:
    """Top-K deviation score, plus the features that produced it.

    z_f = |x_f - centre_f| / scale_f, and the score is the mean of the largest
    min(PROFILE_TOP_K, len(features)) of them.

    `mean of top-3 z` is a poor THRESHOLD and an acceptable SCORE, and this
    design uses it only as the latter. As a threshold it fails three ways:
    correlated features triple-count one departure, the null distribution of a
    maximum moves with the number of features, and a feature sitting on a
    boundary is pinned. As a score it is fine precisely because the threshold is
    a full-conformal rank against the profile's OWN sessions (see
    calibration_deviations): the identical statistic is computed the identical
    way on the reference points, so rank calibration absorbs the scale, the
    correlation and the n-dependence. What rank calibration does NOT absorb is a degenerate
    zero-spread feature, which is why participating_features excludes those
    rather than flooring them.

    It is also the explanation the SOC panel shows a human, which is the other
    reason to keep it: "these three things were unusual" is reviewable, and a
    single opaque distance is not.
    """
    zs = []
    for name in features:
        value = _finite(values.get(name))
        stat = stats.get(name)
        if value is None or stat is None or stat.scale < PROFILE_SCALE_FLOOR:
            continue
        zs.append({"feature": name, "z": abs(value - stat.centre) / stat.scale})
    if not zs:
        return None, []
    zs.sort(key=lambda item: item["z"], reverse=True)
    top = zs[: min(PROFILE_TOP_K, len(zs))]
    score = sum(item["z"] for item in top) / len(top)
    return score, [{"feature": item["feature"], "z": round(item["z"], 3)} for item in top]


# --- Maturity, calibration and the verdict -----------------------------------

# The target false-challenge rate. Conformal validity needs exchangeability, and
# sessions by one person months apart are NOT exactly exchangeable -- devices
# change, people age, hands shake -- so alpha is a bound under an assumption
# that drift violates. The self-healing rule and the population breaker are the
# practical answers to that; the honest statement is that alpha is a target, not
# a guarantee, on real traffic.
PROFILE_ALPHA = 0.05

# NOT a tuned number: arithmetic. With n references the smallest attainable
# p-value is 1/(n+1), so at alpha 0.05 the layer literally CANNOT fire below
# n = 19. A "MIN_PROFILE_SESSIONS = 5" would be pure judgement dressed as
# statistics -- the threshold could never be reached and the code around it
# would look calibrated while doing nothing.
#
# The consequence is stated up front rather than discovered later: with
# per-modality maturity, most customers in a pilot will NEVER mature. That is
# the honest cost of a calibrated layer, and it is one more reason the layer
# ships off.
PROFILE_MIN_SESSIONS = math.ceil(1 / PROFILE_ALPHA) - 1  # == 19

# The ring buffer's size, per (profile, modality). One above the maturity floor:
# large enough that a mature profile survives losing one reference to a dispute,
# small enough that the profile forgets a customer's older self within a few
# months of normal use. It is also what bounds a single absorbed vector's
# influence to 1/20.
PROFILE_BUFFER_MAX = 20

# Probation vectors -- sessions learned only because a step-up rescued a profile
# escalation -- are STORED but are NOT REFERENCES. They never enter the
# reference set the deviation and the conformal rank are computed over, and
# they do not count toward maturity, until they are promoted (see
# promotable_run and POST /api/outcome). At most this many are kept per
# (profile, modality), in slots of their own beside the PROFILE_BUFFER_MAX
# references, so one (profile, modality) stores at most 20 + 4 vectors and a
# probation vector can never evict a reference (admit_vector).
#
# Why they are excluded rather than merely capped. MEASURED on SYNTHETIC
# identities, mouse profiles, alpha 0.05 (docs/profile-evaluation.md section 7
# and its before/after table). On 2026-09-16, while probation vectors still
# counted, another synthetic person's sessions were escalated 48.5% of the time
# with none of their own vectors in the buffer, 25.0% with ONE, and 12.5% with
# four (this cap); the median p-value moved 0.095 -> 0.238. Under the shipped
# rank a vector that counts -- today only a PROMOTED one -- still does exactly
# that: 47.5% -> 25.5% with one, 13.7% with four (2026-09-18). The rank explains
# it: at alpha 0.05 with 20 references a session is challenged only when it is
# further out than EVERY reference, so one learned outlier shields every later
# session less extreme than itself. "One absorbed vector moves the p-value by
# at most 1/(n+1)" is true, and 1/(n+1) is exactly the step between challenged
# and not. A cap bounds the share of centre and scale an attacker controls; it
# cannot bound that loss of sensitivity, and an attacker who passes step-up
# ONCE was buying it. Stored on probation instead, through admit_vector and
# read back by the decision path's rule, his vectors change nothing: with 0, 1,
# 2, 4 and 6 of them learned (4 kept) all 200 victims' reference sets came back
# identical to the one with none, and his sessions were escalated 47.5% of the
# time at every k -- the curve is flat.
#
# The cost, accepted explicitly: the grandchild is not remembered either. The
# NEXT session in the same pattern is challenged again -- friction, not a block
# -- until the merchant settles the earlier payment (POST /api/outcome
# "settled") or three consecutive passed challenges promote the pattern
# (PROFILE_HEAL_AFTER). A legitimate probation vector used to desensitise the
# profile exactly as an attacker's did, and the statistic cannot tell the two
# apart; only a settlement or a run of passed challenges can. MEASURED on the
# same synthetic identities (section 7, "rescued, then returning"): a different
# person who was challenged, passed, and came back was challenged again 71.7%
# of the time on mouse and 62.4% on keyboard, against 34.9% and 30.4% had the
# rescued session counted as a reference. Like every rate measured on
# synthetic people these describe the generator, not real grandchildren; they
# give the size of the cost, and PROFILE_MAX_ESCALATIONS caps it at three
# challenges a month.
PROFILE_PROBATION_MAX = 4

# Per profile per Europe/Istanbul day. Bounds how fast a profile can be taught,
# so an attacker with a valid session cannot rebuild a customer's envelope in an
# afternoon. Three is a guess at "more than one legitimate purchase, fewer than
# a shopping spree"; nothing has measured how many times a real customer checks
# out in a day, because there are no real customers.
PROFILE_LEARN_PER_DAY = 3

# Self-healing. Three challenges in a row, each answered correctly by the
# customer, is evidence that the PROFILE is wrong -- not that the customer is a
# fraudster. It is the same rule for the grandchild paying on grandmother's
# behalf, the new laptop, the traveller, the hand tremor and the switch to
# assistive input. A fraud control that challenges disabled or elderly users
# indefinitely needs an explicit bound, not an apology.
#
# Since probation vectors are not references, healing is also the second of
# the two ways one becomes a reference: the rebuild PROMOTES the run of passed
# challenges that triggered it (promotable_run). Without that, a customer whose
# behaviour genuinely changed would be challenged, pass, and be challenged
# again for as long as no merchant settles anything.
PROFILE_HEAL_AFTER = 3
# What survives the rebuild: the newest 10, which leaves the profile BELOW
# maturity on purpose, so the layer abstains until it has re-learned who the
# customer is now. The promoted run is the newest part of the buffer and at
# most PROFILE_PROBATION_MAX long, so it always survives the cut.
PROFILE_HEAL_KEEP = 10

# Per-profile challenge budget. Even with self-healing, no single person may be
# challenged by this layer more than three times a month. A per-modality
# discrimination risk (inferred disability, tremor, assistive input) is bounded
# by a hard ceiling far more reliably than by a statistic.
PROFILE_BUDGET_WINDOW_DAYS = 30
PROFILE_MAX_ESCALATIONS = 3

# Population circuit breaker: an ABSOLUTE ceiling on enforced profile
# escalations deployment-wide per hour. Absolute rather than a ratio because a
# ratio needs a denominator that is itself unreliable at low traffic, and 50
# challenges an hour from one control is already far past the point where
# someone should be looking at it. The thresholds in this file were calibrated
# on synthetic identities and WILL be wrong on first contact with real traffic;
# the only question is whether that is discovered by a metric or by a merchant
# complaining about checkout abandonment.
PROFILE_BREAKER_WINDOW_S = 3600
PROFILE_BREAKER_MAX = 50
PROFILE_BREAKER_CACHE_S = 60

# Verdict states. Everything except STATE_EVALUATED is an abstention, and
# abstention is the default in every ambiguous case: this layer's failure mode
# must be "said nothing", never "challenged someone it had not measured".
STATE_DISABLED = "disabled"
STATE_NO_PROFILE = "no_profile"
STATE_SUPPRESSED = "suppressed"
STATE_IMMATURE = "immature"
STATE_THIN_SESSION = "thin_session"
STATE_TOO_FEW_FEATURES = "too_few_features"
STATE_BUDGET_EXHAUSTED = "budget_exhausted"
STATE_BREAKER = "breaker"
STATE_EVALUATED = "evaluated"

PROFILE_STATES = (
    STATE_DISABLED,
    STATE_NO_PROFILE,
    STATE_SUPPRESSED,
    STATE_IMMATURE,
    STATE_THIN_SESSION,
    STATE_TOO_FEW_FEATURES,
    STATE_BUDGET_EXHAUSTED,
    STATE_BREAKER,
    STATE_EVALUATED,
)


@dataclass(frozen=True)
class ProfileVerdict:
    """What the profile layer has to say. `escalate` is the ONLY actionable
    field, and acting on it may only ever turn allow/warn into verify."""

    state: str
    escalate: bool = False
    deviation: float | None = None
    p_value: float | None = None
    p_value_low: float | None = None
    reference_n: int = 0
    modality: str | None = None
    top_features: list[dict] = field(default_factory=list)

    def as_public_block(self) -> dict:
        """The uniformly shaped block the SOC endpoint returns. Always the same
        keys whatever the state, so a dashboard-key holder cannot test whether a
        session belongs to a profiled customer by the presence of a field.
        Never carries profile_id, the raw reference, or the stored vectors."""
        return {
            "state": self.state,
            "modality": self.modality,
            "reference_n": self.reference_n,
            "deviation": None if self.deviation is None else round(self.deviation, 3),
            "p_value": self.p_value,
            "p_value_low": self.p_value_low,
            "top_features": list(self.top_features),
            "escalated": self.escalate,
        }


def is_mature(reference_n: int) -> bool:
    """The maturity gate. Below PROFILE_MIN_SESSIONS the layer does not compare
    at all -- not "compares and rarely fires", does not compare."""
    return reference_n >= PROFILE_MIN_SESSIONS


def conformal_rank(candidate_d: float, reference_ds: list[float]) -> tuple[float, float]:
    """Full-conformal p-values, high tail and low tail, from the candidate's
    deviation and the calibration deviations (calibration_deviations).

    The same finite-sample correction as scorer.conformal_p_value: with n
    calibration points the smallest attainable p-value is 1/(n+1), so the
    guarantee never claims more precision than the reference set supports.
    Ties count against escalation (>=), which keeps the p-value conservative
    when two sessions score exactly alike.

    p_high answers "how many of this customer's own sessions were at least this
    far from the customer's own centre" -- small means surprising.

    p_low is the mirror: a session IMPLAUSIBLY CLOSE to the stored centre is
    what a replay of a recorded session looks like. It is computed and stored
    and deliberately NOT acted on in v1, because enforcing it needs the lab
    replay measurement that does not exist yet, and a threshold nobody has
    measured is a threshold that was invented. One replay it cannot see: an
    exact copy of a session that is still a reference ties with it, the tie
    counts against the candidate here too, and p_low stays >= 2/(n+1) --
    0.0% of 200 synthetic replays reached 0.05 (see calibration_deviations).
    """
    n = len(reference_ds)
    at_least = sum(1 for d in reference_ds if d >= candidate_d)
    at_most = sum(1 for d in reference_ds if d <= candidate_d)
    return (1.0 + at_least) / (n + 1.0), (1.0 + at_most) / (n + 1.0)


def evaluate_profile(
    candidate: dict | None,
    references: list[dict],
    *,
    modality: str | None = None,
    alpha: float = PROFILE_ALPHA,
) -> ProfileVerdict:
    """Compare one session vector against this customer's own history.

    `candidate` is a session_vector()["vec"] (or None when the session was too
    thin to produce one); `references` are the stored vectors for the SAME
    profile, the SAME modality and the SAME feature_schema_version, with
    disputed ones already excluded by the caller.

    Escalation is `p_high <= alpha` and nothing else. Every other outcome is an
    abstention with a named state, so the SOC panel and the audit row can always
    say WHY the layer had no opinion.

    The p-value is FULL CONFORMAL: the candidate and every reference are scored
    by one function, point_deviation(point, the other n points), so the
    candidate's score is computed exactly the way each calibration score is.
    See calibration_deviations for why that, and not the leave-one-out rank it
    replaced, is what the alpha bound needs.
    """
    if candidate is None:
        return ProfileVerdict(state=STATE_THIN_SESSION, modality=modality)

    reference_n = len(references)
    if not is_mature(reference_n):
        # No comparison is performed at all: nothing is computed, nothing is
        # stored, no top features are named. An immature profile is not a weak
        # opinion, it is the absence of one.
        return ProfileVerdict(
            state=STATE_IMMATURE, reference_n=reference_n, modality=modality
        )

    # The candidate against the n references. Its top features are the
    # explanation the SOC panel shows, and they are unchanged by the
    # calibration method: the candidate was always scored against exactly the
    # references.
    candidate_d, top = point_deviation(candidate, references)
    if candidate_d is None:
        return ProfileVerdict(
            state=STATE_TOO_FEW_FEATURES, reference_n=reference_n, modality=modality
        )

    reference_ds = calibration_deviations(candidate, references)
    # A reference set that loses too many points to the drop rule (a reference
    # that cannot be scored on PROFILE_MIN_FEATURES features) is no longer a
    # calibration set; abstain rather than calibrate on a handful.
    if not is_mature(len(reference_ds)):
        return ProfileVerdict(
            state=STATE_IMMATURE, reference_n=reference_n, modality=modality
        )

    p_high, p_low = conformal_rank(candidate_d, reference_ds)
    return ProfileVerdict(
        state=STATE_EVALUATED,
        escalate=p_high <= alpha,
        deviation=candidate_d,
        p_value=round(p_high, 4),
        p_value_low=round(p_low, 4),
        reference_n=reference_n,
        modality=modality,
        top_features=top,
    )


def point_deviation(point: dict, others: list[dict]) -> tuple[float | None, list[dict]]:
    """THE nonconformity score: one session vector against a set of others.

    Centre and scale come from `others` only (feature_stats), the features are
    the ones that participate for THIS point (participating_features), and the
    score is the top-K deviation. (None, []) when fewer than
    PROFILE_MIN_FEATURES features participate -- the point cannot be scored,
    which for the candidate is the too_few_features abstention and for a
    reference is the calibration drop rule. One function for both, on purpose:
    see calibration_deviations."""
    stats = feature_stats(others)
    features = participating_features(point, stats)
    if len(features) < PROFILE_MIN_FEATURES:
        return None, []
    return deviation(point, stats, features)


def calibration_deviations(candidate: dict, references: list[dict]) -> list[float]:
    """The full-conformal calibration set: every reference scored as if IT were
    the candidate -- against the other references PLUS the actual candidate --
    by point_deviation, the function the candidate itself is scored with.

    Why full conformal, and not the leave-one-out rank this replaced. The rank
    guarantee ("an exchangeable session is challenged with probability at most
    alpha") holds only when the candidate and its calibration points are scored
    by the SAME function of (the point, the set of the other points). The
    leave-one-out rank did not do that: it scored the candidate against all n
    references on the candidate's own participating features, and each
    reference against the other n - 1 only, on the subset of the CANDIDATE's
    features that reference could be scored on. Two different statistics, so
    alpha was not the rate even on synthetic people. MEASURED on SYNTHETIC
    identities on 2026-09-16, same sessions under both ranks (the calibration
    check of that run, now the before column of docs/profile-evaluation.md's
    before/after table; alpha 0.05, 20 references): mouse 6.0%
    same-person challenges under leave-one-out against 4.9% full conformal,
    paired difference +1.2 pp (95% +0.2 to +2.4); keyboard 2.9% against 3.8%,
    -0.9 pp (95% -1.4 to -0.4). Wrong in BOTH directions, which is what "not
    the same function" predicts: the sign depends on which features happen to
    participate for whom. Those are lower bounds, like every rate measured on
    synthetic people -- the point here is the gap, not the level.

    Full conformal puts the candidate among the references: n + 1 points, and
    each is scored against the other n. Point i's score is then
    point_deviation(z_i, all points but z_i) whichever point plays the
    candidate, so when the n + 1 sessions are exchangeable the candidate's rank
    among the n + 1 scores is uniform and P(p_high <= alpha) <= alpha holds in
    finite samples -- not asymptotically, not approximately. Equivalently:
    rotate the candidate role through all n + 1 points and at most
    floor(alpha * (n + 1)) of the rotations escalate; test_profiles asserts
    that identity directly. Ties (>= in conformal_rank) only make it more
    conservative. Exchangeability itself remains an assumption that a real
    customer's drift violates -- see PROFILE_ALPHA -- but it is no longer
    compounded by scoring the candidate differently from the points it is
    ranked against.

    What including the candidate does to detection. Each reference's centre
    and scale now see the candidate too, so a candidate far from the customer
    is one extra outlier in every reference's comparison set. The median moves
    by at most one order statistic for it, and the MAD, like the median, has a
    50% breakdown point, so one outlier among twenty cannot carry either far. A
    reference is still never compared with itself. On the same synthetic sessions the whole
    change moved different-person escalation 48.1% -> 47.5% on mouse and
    23.5% -> 26.4% on keyboard, and same-person challenges 6.0% -> 4.9% and
    2.9% -> 3.8% (the 2026-09-18 run reproduced those full-conformal figures
    with the shipped code, and found 0 of 16000 verdicts differing from the
    lab's independent implementation): the calibration fix was not
    paid for with detection. The candidate's own deviation and top features
    are unchanged: it was always scored against exactly the references.

    What including the candidate does to replay. An exact replay of a session
    that is a reference ties with it, and conformal_rank counts ties against
    the candidate, so its p-values cannot fall below 2/(n+1): it can never be
    escalated at alpha 0.05, and p_value_low no longer sees it. MEASURED
    (docs/profile-evaluation.md section 9, 200 synthetic mouse profiles):
    escalated 0.0% and p_value_low <= 0.05 in 0.0% -- 1.5% and 8.5% under the
    leave-one-out rank, which scored the candidate with its twin among its
    references but not the twin with the candidate.

    A reference that cannot be scored (fewer than PROFILE_MIN_FEATURES
    participating features) is dropped and n drops with it -- the same rule
    that makes the candidate abstain as too_few_features. Whether a point can
    be scored is itself a function of (the point, the others), so the
    guarantee holds among the points that can.

    Also what the human-review endpoint shows: a reviewer answering a contested
    challenge sees the exact calibration set the decision ranked against, not
    just the p-value. Order follows `references`.
    """
    out = []
    for index, reference in enumerate(references):
        others = references[:index] + references[index + 1 :] + [candidate]
        d, _ = point_deviation(reference, others)
        if d is not None:
            out.append(d)
    return out


# --- The bounded reference list ----------------------------------------------


def admit_vector(buffer: list[dict], vector: dict) -> tuple[list[dict], list[dict]]:
    """Add one learned vector to a (profile, modality) buffer.

    Returns (kept, evicted). Entries are mappings carrying at least `created_at`
    and `probation`; ordering is by created_at, oldest first. See
    enforce_buffer_caps for the two caps.
    """
    return enforce_buffer_caps(list(buffer) + [vector])


def enforce_buffer_caps(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply the storage caps of one (profile, modality). Returns (kept, evicted).

    Two caps, each counted within its own kind:

    1. References (probation false): at most PROFILE_BUFFER_MAX, oldest
       evicted first. Only a reference -- a newly learned clean vector, or a
       promoted one -- can push another reference out.
    2. Probation vectors: at most PROFILE_PROBATION_MAX, OLDEST PROBATION entry
       evicted first. A probation vector never evicts a reference, whatever the
       reference count: the references are what a mature profile's statistic
       stands on, and letting unconfirmed sessions push them out would let an
       attacker who passes step-up twice drop a 20-reference profile below
       maturity and switch the layer off for himself -- or, before probation
       vectors were excluded from the statistic, evict the customer's real
       behaviour one clean vector at a time and end up owning the profile.

    This is the whole "reduced weight" mechanism, and it needs no weighted
    Welford, no probabilistic acceptance and no weight_sum column: a reference
    is one of at most twenty, its influence on the centre is bounded by the
    median's breakdown point, and its influence on the p-value is at most
    1/(n+1) by construction -- which is exactly why an unconfirmed vector is
    given no reference slot at all (see PROFILE_PROBATION_MAX).
    """
    ordered = sorted(entries, key=_created_key)
    drop: set[int] = set()
    for is_probation, cap in ((True, PROFILE_PROBATION_MAX), (False, PROFILE_BUFFER_MAX)):
        same_kind = [entry for entry in ordered if bool(entry.get("probation")) is is_probation]
        for entry in same_kind[: max(0, len(same_kind) - cap)]:
            drop.add(id(entry))
    # Identity, not equality: two stored entries may compare equal as dicts.
    kept = [entry for entry in ordered if id(entry) not in drop]
    evicted = [entry for entry in ordered if id(entry) in drop]
    return kept, evicted


def is_reference(entry: dict) -> bool:
    """Whether a stored vector may be compared against: not on probation, not
    disputed. The decision path applies the same rule in SQL
    (main._read_profile_context), and test_profiles asserts the two agree."""
    return not entry.get("probation") and entry.get("outcome") != "disputed"


def promotable_run(buffer: list[dict], consecutive_passed_escalations: int) -> list[dict]:
    """The probation vectors a self-healing rebuild promotes to references, or
    [] when this is not a rebuild.

    `buffer` is one (profile, modality) as stored, probation vectors included.
    The run is the probation vectors NEWER than the newest reference of that
    modality -- every session in this modality since the customer last passed
    unchallenged was challenged, and passed. It is promoted only when BOTH:

    * the profile's consecutive_passed_escalations has reached
      PROFILE_HEAL_AFTER (a clean learn in ANY modality resets it), and
    * the run itself holds at least PROFILE_HEAL_AFTER vectors that still
      exist. A counter that reached three over passes whose vectors were since
      disputed and deleted, or that were spread over another modality, promotes
      nothing: a single pass is never promoted, whatever the counter says.

    Why the two promotion paths do not reopen the poisoning hole that excluding
    probation vectors closed. The hole was that ONE passed step-up bought the
    attacker a reference, i.e. a shield for every later session less extreme
    than his own. Neither path sells that:

    * Settlement (POST /api/outcome "settled") needs the merchant credential,
      which the scored client does not hold, and it records the merchant's
      statement that the payment was legitimate. A merchant that settles a
      fraud has lost the money already; the dispute path still deletes the
      vector exactly when the chargeback arrives.
    * Healing needs PROFILE_HEAL_AFTER passed challenges in a row, in separate
      sessions (a session is learned once), with no session the customer
      passed unchallenged in between, and at most PROFILE_LEARN_PER_DAY learns
      a day. An attacker who can pass the merchant's step-up three sessions running
      already owns the only thing this layer can ask for -- its strength is the
      step-up channel's, which is written down as a limitation, not engineered
      around. What he gains is the end of challenges he was passing anyway,
      and the rebuild does not hand him a mature profile to hide in: it cuts
      the buffer to PROFILE_HEAL_KEEP, below maturity, so the layer abstains
      until the customer has been re-learned.
    """
    if not needs_healing(consecutive_passed_escalations):
        return []
    run: list[dict] = []
    for entry in reversed(sorted(buffer, key=_created_key)):
        if not entry.get("probation"):
            break
        run.append(entry)
    if len(run) < PROFILE_HEAL_AFTER:
        return []
    return list(reversed(run))


def heal_buffer(buffer: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rebuild a profile that keeps being right-challenged: keep the newest
    PROFILE_HEAL_KEEP vectors, drop the rest. The caller promotes the run
    (promotable_run) first; that run is the newest part of the buffer, so it
    is always among what is kept.

    The profile is then BELOW maturity, which is the point -- the layer abstains
    until it has re-learned the customer, instead of continuing to challenge
    them against a picture of who they used to be."""
    ordered = sorted(buffer, key=_created_key)
    if len(ordered) <= PROFILE_HEAL_KEEP:
        return ordered, []
    cut = len(ordered) - PROFILE_HEAL_KEEP
    return ordered[cut:], ordered[:cut]


def needs_healing(consecutive_passed_escalations: int) -> bool:
    return consecutive_passed_escalations >= PROFILE_HEAL_AFTER


def budget_window_expired(window_start: datetime | None, now: datetime) -> bool:
    if window_start is None:
        return True
    return now - window_start >= timedelta(days=PROFILE_BUDGET_WINDOW_DAYS)


def budget_allows(escalation_count: int, window_start: datetime | None, now: datetime) -> bool:
    """False once this profile has spent its challenges for the window."""
    if budget_window_expired(window_start, now):
        return True
    return escalation_count < PROFILE_MAX_ESCALATIONS


def breaker_allows(escalations_in_window: int) -> bool:
    return escalations_in_window < PROFILE_BREAKER_MAX


# --- helpers -----------------------------------------------------------------


def _finite(value) -> float | None:
    """A feature value, or None when it was not measured. None and NaN mean the
    same thing here -- nothing was observed -- and both must stay out of the
    statistic rather than being silently read as 0.0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("bos listenin medyani yok")
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def _created_key(entry: dict):
    """Oldest-first ordering. A missing created_at sorts as "oldest known",
    because an entry the database could not timestamp is the one we least want
    deciding a customer's challenge."""
    return (entry.get("created_at") is not None, entry.get("created_at") or datetime.min)
