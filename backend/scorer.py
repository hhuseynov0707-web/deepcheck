"""Feature extraction, model inference, SHAP explanation and risk scoring."""

import bisect
import hashlib
import logging
import math
import os
import time
from collections import Counter

import joblib
import numpy as np
import shap
import sklearn

from lstm_model import FEATURE_NAMES

logger = logging.getLogger("deepcheck.scorer")

# Where the trained artifacts live. Defaults to this directory, which is what
# the container and every existing caller already assume.
#
# DEEPCHECK_MODEL_DIR exists so a training or evaluation run can write and read
# a bundle somewhere ELSE without touching the one being served. Two concrete
# needs, both met by the same one-line override:
#
#   * Measuring a change to the features or the training distribution means
#     retraining, and retraining in place swaps the model out from under a
#     running container mid-request -- and under two parallel workstreams, out
#     from under whoever else is running the test suite.
#   * CI can train into a scratch directory and assert on the result without a
#     build step that leaves an untracked 4 MB pickle in the working tree.
#
# Read once at import, like the paths below it, so a process cannot be scoring
# against two different bundles depending on when it looked.
MODEL_DIR = os.getenv("DEEPCHECK_MODEL_DIR") or os.path.dirname(os.path.abspath(__file__))

# The artifact names carry the scikit-learn version that wrote them.
#
# backend/ is bind-mounted into the container, so the host and the container
# share this directory while pinning DIFFERENT scikit-learn versions (the host
# has whatever is installed there; the container has the pin from
# requirements.txt). With one fixed "model.pkl" they overwrite each other's
# work, and each side then loads a pickle the other wrote -- which sklearn
# permits with a warning and "possibly invalid results". Version-scoped names
# let both sides keep a correct model of their own, and cost nothing: the files
# are reproducible from a fixed seed and are not in git.
MODEL_PATH = os.path.join(MODEL_DIR, f"model-sklearn{sklearn.__version__}.pkl")
# Written only by `TRAIN_LSTM=1 python train_model.py`, for re-measurement.
# Nothing on the request path reads it -- see the note above compute_risk.
LSTM_PATH = os.path.join(MODEL_DIR, f"lstm_model-sklearn{sklearn.__version__}.pt")

CLICK_DENSITY_WINDOW_MS = 5000

# Features whose RAW value spans orders of magnitude: two variances and a mean
# duration. They are normalised by mapping log10(raw) onto the 1st..99th
# percentile of the training distribution, with the endpoints stored in
# model.pkl under "feature_scaling".
#
# What this replaces, and why. Each of these used to be divided by a
# hand-picked constant and clipped to [0, 1]: variance/5.0, ms/1500.0, and
# variance/2.2e-6. Measured against real browser traffic and against
# adversarial sessions, all three sat on the ceiling:
#
#     ivme_degisimi     1.000 for human motion, 1.000 for a Bezier bot,
#                       0.002 for a straight line
#     tereddut_skoru    1.000 whenever mean hesitation exceeded 1.5 s
#     etkilesim_entropisi  1.000
#
# A feature pinned at its maximum carries one bit at best. ivme_degisimi had
# collapsed into "does the pointer wobble at all?", and an attacker flipped it
# by adding two pixels of gaussian noise -- one line of code that halved the
# risk score and turned a refused session into an approved one. Nothing about
# that is a modelling subtlety; the divisor was simply three orders of
# magnitude off the real distribution, and clipping hid it.
#
# Percentiles rather than min/max so a single freak session cannot set the
# scale, and log10 because the underlying quantities are multiplicative: the
# difference between 1e-9 and 1e-8 of acceleration variance matters exactly as
# much as the difference between 1e-6 and 1e-5.
LOG_SCALED_FEATURES = ("scroll_hizi_varyansi", "tereddut_skoru", "ivme_degisimi")

# Raw values below this are treated as "immeasurably small" rather than fed to
# log10, which would return -inf for a perfectly constant signal.
RAW_FLOOR = 1e-10

# Fallback used only by a bundle written before feature_scaling existed, or
# before training has computed it. Wide enough to be harmless, not tuned.
FEATURE_SCALING = {
    "scroll_hizi_varyansi": (-4.0, 1.0),
    "tereddut_skoru": (2.3, 3.6),
    "ivme_degisimi": (-9.0, -4.0),
}

# Counts, not measurements. They were never the problem and are left alone.
CLICK_DENSITY_DIVISOR = 10.0
FOCUS_CHANGE_DIVISOR = 5.0

# --- Small-sample gates for the structural features -------------------------
#
# These are correctness requirements, not tuning. An autocorrelation estimated
# from four speed samples is dominated by its own sampling error, and feeding
# that to the model as though it were a measurement is how a mostly-typing
# human ends up scored as a bot. Below each threshold there is no measurement,
# so extract_raw returns None and the neutral fallback applies.
MIN_AUTOCORRELATION_SAMPLES = 8  # speed samples, i.e. >=9 trajectory points
MIN_DIRECTION_SAMPLES = 6  # consecutive-vector pairs, i.e. >=8 trajectory points
MIN_TIMING_GAPS = 5
MIN_CLICKS_FOR_MOTION_RATIO = 2
MIN_CHANNEL_TRANSITIONS = 5

# A click counts as "preceded by motion" if any mousemove landed within this
# window before it.
CLICK_MOTION_LOOKBACK_MS = 1500

# Hand travel between keyboard and pointer costs a person a few hundred ms.
TRANSITION_LAG_DIVISOR = 800.0

# The coefficient of variation of human inter-event gaps sits near 0.5
# (lognormal-ish), uniform-random script delays near 0.3, fixed delays at 0.
# Dividing by 1.5 keeps the human range off the clip ceiling, so the feature
# still discriminates ABOVE the human mean instead of saturating there.
DISPERSION_DIVISOR = 1.5

# Inter-event gaps are binned on a FIXED log10-millisecond grid rather than
# over each session's own [min, max].
#
# The old binning rescaled itself per session, which inverted the feature on
# realistic input. A person pausing once to read stretches the range from
# ~10 ms to ~5 s, so every ordinary gap falls into the first bin and the
# entropy collapses toward zero -- while a metronomic bot, whose gaps are all
# alike, keeps a narrow range and scores HIGHER. Measured: 0.174 for a human
# model against 0.548 for a headless script, precisely backwards from what the
# training data assumes. Fixed edges make the number mean the same thing in
# every session and restore the intended reading.
ENTROPY_LOG_EDGES = np.linspace(0.5, 4.0, 15)  # ~3 ms .. 10 s

# When a feature can't be mathematically computed because a request carries
# too little raw signal (e.g. a 2s window where the user was only typing, not
# moving the mouse -- variance/entropy/acceleration all need >=2-3 samples),
# falling back to 0.0 is actively wrong: 0.0 sits at or below the trained
# *bot* mean, so "no data" was being scored as "more suspicious than an
# actual bot". These neutral fallbacks are the midpoint between the human/bot
# training means in train_model.py -- "no evidence" should not count as
# evidence toward either class.
#
# These are now COMPUTED AT TRAINING TIME and stored in model.pkl under
# "neutral_defaults" (see train_model.py) -- the constant below is only the
# fallback for a pickle written before that existed, and loading such a
# pickle logs a warning. Hand-maintaining these numbers is what let them
# drift stale once already: a training-data rebalance moved tereddut_skoru's
# true midpoint from ~0.26 to ~0.44 while this constant stayed put, silently
# reintroducing a false positive on sparse-data sessions.
#
# Note the one unavoidable bootstrap: the training run that computes these
# values is itself extracting features with the fallback in force, since no
# bundle exists yet at that point. That only affects rows sparse enough to
# need a fallback, and the next training run converges on the new values.
#
# tiklama_yogunlugu and odak_degisimi are deliberately excluded: they are
# simple counts (clicks in window, focus-loss count) that are always
# well-defined, including as a legitimate 0 -- "zero clicks happened" is
# real information, not a missing measurement, so no neutral fallback applies.
NEUTRAL_DEFAULTS = {
    "scroll_hizi_varyansi": 0.17,
    "tereddut_skoru": 0.45,
    "etkilesim_entropisi": 0.66,
    "ivme_degisimi": 0.35,
    # Structural features. Fallbacks measured on the browser lab's captures:
    # human / bot means were 0.70/0.51, 0.87/0.85, 0.13/0.24, 0.72/0.18,
    # 0.75/0.61 and 0.49/0.30 respectively; these are the midpoints. Training
    # recomputes them, so these are the legacy-bundle fallback only.
    "hiz_otokorelasyonu": 0.60,
    "yon_tutarliligi": 0.86,
    "zaman_kuantasyonu": 0.18,
    "duraklama_dagilimi": 0.45,
    "tiklama_oncesi_hareket": 0.68,
    "kanal_gecis_gecikmesi": 0.39,
}

# How many of the twelve features must be genuinely MEASURED, rather than
# filled in with a neutral fallback, before the resulting score is worth
# showing to anyone.
#
# A flush carrying three pointer samples and three keystrokes -- which is what
# the opening seconds of any real session look like -- produces a number, and
# that number is not wrong so much as unsupported: most of the vector is
# fallbacks and the rest is estimated from a handful of samples. Measured in
# benchmark.py's opening-seconds slice, 35% of legitimate sessions scored above
# the block threshold on a window like that.
#
# Six is where the measurement separates cleanly. Every other legitimate
# interaction style, including keyboard-only users who touch the pointer barely
# at all, measures at least six; the opening-seconds slice never exceeds five.
#
# This does NOT change the score or the model. It marks the score provisional,
# so the interface can say "still working it out" instead of showing a red
# badge to a customer who has done nothing wrong. The stored score is
# unaffected, because the history chart and any later analysis should still see
# what the model actually said.
MIN_MEASURED_FOR_CONFIDENT_SCORE = 6


def measured_feature_count(raw_values: dict) -> int:
    """How many of the twelve features this flush genuinely MEASURED.

    extract_raw() returns None for a feature it could not compute, and
    extract_features() then substitutes NEUTRAL_DEFAULTS, so a full twelve-
    number vector says nothing about how much of it is real. Three callers
    need that distinction and must agree on it: compute_risk (provisional
    scores), record_session (which flushes may enter the training set) and
    train_model (the same gate applied to a recorded archive). It lives here
    because divergence between them is silent -- a looser gate in one path
    fills the training set with vectors made of fallbacks, which is exactly
    what a naive headless bot sends.
    """
    return sum(1 for name in FEATURE_NAMES if raw_values.get(name) is not None)


# Features that never need a neutral fallback: they are simple counts (clicks
# in window, focus-loss count) that are always well-defined, including as a
# legitimate 0.
NEUTRAL_FEATURES = tuple(NEUTRAL_DEFAULTS)

# --- Session smoothing and level shifts -------------------------------------
#
# A session's official score is the median of its last SMOOTHING_WINDOW
# flushes, so one odd reading -- an incidental pause in otherwise robotic
# activity -- cannot flip a verdict on its own.
#
# A mid-session handover is not one odd reading, it is a level shift, and a
# median hides a level shift for as long as it takes three of five windows to
# turn. So when the current flush jumps LEVEL_SHIFT_POINTS or more above the
# median of the flushes before it, smoothing may not pull the session below
# that reading. Upward only: a bot that produces one calm window is exactly the
# single odd reading smoothing exists to ignore.
#
# This replaces the RandomForest/LSTM disagreement term, which was the same idea
# with the LSTM standing in for "the past". Measured on 185 simulated handovers
# with the RandomForest alone: plain median smoothing caught 0 on the first
# automated flush, this rule caught 185. It moved at most 6 of 14,790 legitimate
# flushes across 60, and none of the 94 browser-lab human flushes. The result
# was identical at 25, 35 and 50 points; 35 keeps the old threshold's meaning.
#
# The downward direction has no such rule here, and the median alone would let
# a bot that turns human-looking reach a low score within three flushes. That
# case is caught in main.py's decision path rather than here: while the
# sequential statistic over the newest ten per-flush scores still crosses the
# bot bound, the session is held at verify whatever this smoothed score says
# (internal reason "sequential"; test_crossing_the_bot_bound_is_never_charged).
#
# The bypass asks for a flush that OBSERVED THE GENERATOR, and that
# qualification cost a real person a payment before it was added.
#
# A window in which almost nothing happened still produces a full twelve-number
# vector, because every feature has a neutral fallback -- and the forest has an
# opinion about that fallback coordinate like any other. In the one recorded
# human session three windows carried no pointer, click, scroll or key event
# worth the name (3 of 12 features measured, none of them structural). The
# forest scored that coordinate 88.5, 97.3 and 88.4. One of the three was the
# LAST flush, the one "Onayla" is decided on: against a session sitting at 0.0
# it read as an 88-point level shift, took the bypass, and set the stored
# session score to 88.4 -- "Bot Tespit Edildi" for somebody who had stopped
# moving the mouse while the window was open. The per-flush median for that
# session is 0.0; the number the payment is decided on was 88.4.
#
# The gate is the STRUCTURAL features (BUCKET_FEATURES), not `provisional`, and
# the difference is the whole point. `provisional` is also true of a naive
# headless bot -- measured on the fixtures: headless_bot measures 4 of 12 and
# fast_keyboard_only_no_mouse 5 of 12 -- so gating on it would reopen the
# handover hole this rule exists to close. What separates them is measured and
# clean: the human's idle windows measured 0 of the 6 structural features,
# while every bot fixture measured at least one (headless_bot 1, keyboard-only
# 2, scripted_motion 4). The structural features are the ones that describe the
# GENERATOR rather than the sample, which is exactly why behavior_bucket uses
# only them -- and a level shift is a claim that the generator changed. With
# none of them measurable there is no observation of a generator in this flush
# to make that claim from.
#
# The median still carries such a flush: one of five cannot flip a verdict,
# which is what a median is for. A session that has not produced enough
# observed flushes is not decided on this median at all: main.
# _decide_on_evidence holds any decision resting on fewer than three observed
# flushes at verify("insufficient_evidence") -- neither approved nor blocked --
# and its sequential statistic reads observed flushes only. That is also why a
# bot sending only unobserved windows is not let through here. An unobserved
# flush's score is NOT reliably high: it can be steered low through the
# marginal features and the client-supplied hesitation_intervals (measured,
# see the block above main._structural_bits), so "it will score 99" is not a
# safety net and nothing downstream relies on it.
#
# Nothing here was retuned. LEVEL_SHIFT_POINTS is still 35 and the window is
# still 5; the rule now checks that the reading it reacts to was observed.
SMOOTHING_WINDOW = 5
LEVEL_SHIFT_POINTS = 35.0


def smooth_session_score(
    previous_scores: list[float], current: float, current_observed_structure: bool = True
) -> float:
    """The session score /api/analyze stores and /api/decision reads.

    `previous_scores` are this session's earlier per-flush scores, oldest
    first. Non-finite values are skipped: statistics.median over a list
    containing NaN returns a meaningless value rather than raising.

    `current_observed_structure` is whether this flush measured at least one
    of BUCKET_FEATURES -- compute_risk() returns it as `observed_structure`.
    A flush that measured none of them may join the median but may not trigger
    the level-shift bypass; see the note above. It defaults to True so a
    caller that has not got the flag keeps the old behaviour rather than
    silently getting a softer rule.
    """
    previous = [s for s in previous_scores if s is not None and math.isfinite(s)][-(SMOOTHING_WINDOW - 1):]
    smoothed = float(np.median(previous + [current]))
    if (
        previous
        and current_observed_structure
        and current - float(np.median(previous)) >= LEVEL_SHIFT_POINTS
    ):
        smoothed = max(smoothed, current)
    return round(smoothed, 1)

LABELS = [
    (40, "Gerçek Kullanıcı"),
    (60, "Şüpheli"),
    (80, "Yüksek Risk"),
    (101, "Bot Tespit Edildi"),
]


class ModelUnavailableError(FileNotFoundError):
    """The model cannot be used and the caller must not fall back to scoring.

    Subclasses FileNotFoundError so every existing `except FileNotFoundError`
    (the 503 in /api/analyze, the model_loaded flag in /api/health) keeps
    working, while the name says what actually happened.
    """


class ModelBundle:
    def __init__(self):
        if not os.path.exists(MODEL_PATH):
            raise ModelUnavailableError(
                "model.pkl bulunamadı. Önce `python train_model.py` çalıştırın."
            )
        bundle = joblib.load(MODEL_PATH)

        # A pickle is only loadable by the scikit-learn that wrote it.
        # Loading one written by a different version makes sklearn print
        # "InconsistentVersionWarning ... may lead to breaking code or invalid
        # results" and then carry on scoring, which is the worst of both
        # worlds: a warning nobody reads and risk scores nobody can trust.
        #
        # This is not hypothetical here. backend/ is bind-mounted into the
        # container, so a model trained on the host (whatever Python and
        # scikit-learn happen to be installed there) is the exact file the
        # container loads with the pinned scikit-learn from requirements.txt.
        # Refusing is safe because the artifacts are reproducible: entrypoint.sh
        # retrains automatically when this check fails.
        trained_with = bundle.get("sklearn_version")
        if trained_with != sklearn.__version__:
            raise ModelUnavailableError(
                "model.pkl farkli bir scikit-learn surumuyle egitilmis "
                f"(model: {trained_with or 'bilinmiyor'}, calisan: {sklearn.__version__}). "
                "Skorlar guvenilmez olurdu; `python train_model.py` ile yeniden egitin."
            )

        self.scaler = bundle["scaler"]
        self.rf = bundle["rf"]
        # Loaded but no longer scored against -- see the note in compute_risk.
        # Kept on the object so the decision can be re-measured against real
        # recordings without retraining, and so an older bundle still loads.
        self.iso_forest = bundle["iso_forest"]
        self.feature_names = bundle["feature_names"]
        # The feature set is part of the contract between a bundle and the code
        # that serves it. Adding a feature changes the width and the order of
        # every vector the forest was fitted on, so a bundle trained against a
        # different list cannot be used -- it would read column 7 as though it
        # were column 3 and score confidently on nonsense. Same reasoning as the
        # scikit-learn and feature_scaling checks; entrypoint.sh retrains.
        if list(self.feature_names) != list(FEATURE_NAMES):
            raise ModelUnavailableError(
                "model.pkl farkli bir ozellik kumesiyle egitilmis "
                f"({len(self.feature_names)} ozellik, kod {len(FEATURE_NAMES)} bekliyor). "
                "`python train_model.py` ile yeniden egitin."
            )

        # Computed by train_model.py from the generated dataset (the midpoint
        # between the human and bot means of each feature). Missing only in a
        # pickle written before that was added -- say so rather than silently
        # using numbers that may no longer match the training distribution.
        # Refuse, do not warn. A bundle written before feature_scaling existed
        # was trained on features normalised by the old fixed divisors. Serving
        # it under the new normalisation feeds the forest a different
        # coordinate system than it learned, which produces confident and
        # meaningless scores -- the same failure mode as loading a pickle from
        # another scikit-learn, and it deserves the same answer.
        # entrypoint.sh retrains when this raises.
        self.feature_scaling = bundle.get("feature_scaling") or {}
        if not self.feature_scaling:
            raise ModelUnavailableError(
                "model.pkl 'feature_scaling' icermiyor (eski surumle egitilmis). "
                "Ozellik olcekleme degisti; eski model yanlis normalize edilmis "
                "veriyle skorlar. `python train_model.py` ile yeniden egitin."
            )

        # Scores the trained model gave to held-out human sessions from the
        # real-BROWSER lab file -- scripted Playwright personas, not customers
        # (see the conformal section below) -- used by main.py's conformal
        # guard. Absent on a synthetic-only run, in which case the guard is
        # simply inactive.
        self.human_calibration = list(bundle.get("human_calibration") or [])

        # Said once, at load, because this guard was described as a safety net
        # while the served calibration made it inert. Whether it can soften any
        # block is one number: the p-value of the lowest blocked score, the
        # lower edge of "Bot Tespit Edildi", where main.ACTION_LADDER starts to
        # block. The p-value never rises with the score, so if it is <= alpha
        # there, no block at any score is softened; if even 1/(n+1) exceeds
        # alpha, every block is. Observability only: the guard is unchanged.
        block_at, n = LABELS[-2][0], len(self.human_calibration)
        p_block = conformal_p_value(block_at, self.human_calibration)
        working = False
        if p_block is None:
            state = f"kalibrasyon yok, koruma devre disi: {block_at} ve ustu dogrudan bloklanir"
        elif p_block <= CONFORMAL_ALPHA:
            state = "ETKISIZ: su an hicbir blok ek dogrulamaya indirilemez"
        elif 1.0 / (n + 1.0) > CONFORMAL_ALPHA:
            state = "kalibrasyon cok kucuk: HER blok ek dogrulamaya iner, hicbir oturum bloklanmaz"
        else:
            working = True
            state = f"etkin: {block_at} ve ustundeki bazi bloklar ek dogrulamaya indirilebilir"
        measured = (
            f", en yuksek skor {max(self.human_calibration):.1f}, "
            f"{block_at} skorunda p={p_block:.3f} (alfa {CONFORMAL_ALPHA})"
            if n
            else ""
        )
        logger.log(
            logging.INFO if working else logging.WARNING,
            "Konformal koruma: insan kalibrasyonu n=%d%s; %s",
            n,
            measured,
            state,
        )

        self.neutral_defaults = bundle.get("neutral_defaults") or {}
        if not self.neutral_defaults:
            logger.warning(
                "UYARI: model.pkl 'neutral_defaults' icermiyor (eski surum). "
                "scorer.py icindeki sabit degerler kullanilacak; egitim "
                "dagilimi degistiyse bunlar guncel olmayabilir. "
                "`python train_model.py` ile yeniden egitin."
            )

        # n_jobs=-1 is a *training* setting that gets serialized into model.pkl
        # and then silently reused for inference. At training time it
        # parallelizes 50k rows across all cores; at inference time every
        # request predicts exactly ONE row, so joblib's per-call thread pool
        # setup/teardown costs far more than the work it distributes --
        # measured 42.1ms vs 14.4ms for rf.predict_proba, ~2.9x. It also makes
        # each request fan out over every core, so concurrent requests fight
        # for the same CPUs and the contention compounds. Pin to 1 here rather
        # than in train_model.py so existing pickles are fixed on load too.
        self.rf.n_jobs = 1
        self.iso_forest.n_jobs = 1

        self.explainer = shap.TreeExplainer(self.rf)


_bundle: ModelBundle | None = None


def get_bundle() -> ModelBundle:
    global _bundle
    if _bundle is None:
        _bundle = ModelBundle()
    return _bundle


def get_neutral_defaults() -> dict:
    """The neutral fallback values in force right now.

    Reads them off the loaded bundle when there is one; falls back to the
    module constant otherwise. Deliberately does NOT call get_bundle(), which
    would raise during training -- train_model.py imports extract_features()
    long before any model.pkl exists.
    """
    if _bundle is not None and _bundle.neutral_defaults:
        return _bundle.neutral_defaults
    return NEUTRAL_DEFAULTS


def get_human_calibration() -> list[float]:
    """Held-out human scores from the loaded bundle, or [] if there are none."""
    if _bundle is not None:
        return _bundle.human_calibration
    return []


def get_feature_scaling() -> dict:
    """Log-percentile endpoints in force right now: from the loaded bundle
    when there is one, otherwise the module fallback. Deliberately does not
    call get_bundle(), which would raise during training."""
    if _bundle is not None and _bundle.feature_scaling:
        return _bundle.feature_scaling
    return FEATURE_SCALING


def normalize_feature(name: str, raw_value: float, scaling: dict | None = None) -> float:
    """Raw quantity -> the 0..1 value the model sees."""
    if name in LOG_SCALED_FEATURES:
        scaling = scaling or get_feature_scaling()
        lo, hi = scaling.get(name, FEATURE_SCALING[name])
        if hi - lo < 1e-9:
            return 0.5
        value = math.log10(max(float(raw_value), RAW_FLOOR))
        return float(np.clip((value - lo) / (hi - lo), 0.0, 1.0))
    if name == "tiklama_yogunlugu":
        return float(np.clip(raw_value / CLICK_DENSITY_DIVISOR, 0.0, 1.0))
    if name == "odak_degisimi":
        return float(np.clip(raw_value / FOCUS_CHANGE_DIVISOR, 0.0, 1.0))
    # etkilesim_entropisi is already a 0..1 quantity by construction.
    return float(np.clip(raw_value, 0.0, 1.0))


# --- Cross-session behaviour bucket ----------------------------------------
#
# Per-session scoring cannot catch competent mimicry: a bot that reproduces
# human statistics is human-shaped by construction, and the adversarial run
# measured exactly that (an independently written humanised bot scored 11.4
# against a human 11.3). What that bot cannot hide is that it ran twenty-five
# times and produced twenty-five nearly identical behavioural signatures.
# People do not repeat themselves that way.
#
# So each flush is quantised onto a coarse grid and hashed. Sessions landing in
# the same cell are behaving alike; counting how many DISTINCT sessions share a
# cell over a short window turns per-session invisibility into a population
# signal. This is the layer the fraud industry reaches for once velocity rules
# have been defeated, and it needs no model.
#
# Only the structural features are used. The marginal statistics (variance,
# entropy, hesitation) drift with how much data a flush happens to carry, so
# including them scatters one script across many cells. The structural features
# describe the generator rather than the sample, which is what should repeat.
BUCKET_FEATURES = (
    "hiz_otokorelasyonu",
    "yon_tutarliligi",
    "zaman_kuantasyonu",
    "duraklama_dagilimi",
    "tiklama_oncesi_hareket",
    "kanal_gecis_gecikmesi",
)

# Grid spacing. Coarse enough that one script's run-to-run noise stays in one
# cell, fine enough that unrelated people do not collide. 0.2 gives five levels
# per feature; the value is measured in the adversarial harness rather than
# guessed, and BUCKET_RESOLUTION is the knob if the population changes.
BUCKET_RESOLUTION = 0.2


# How many of the bucket features must have been genuinely MEASURED, rather
# than filled in with a neutral fallback, before a flush gets a bucket at all.
#
# This is the difference between the mechanism working and actively harming.
# Built from the normalised features it produced a single enormous cell: every
# flush too thin to measure took the same fallbacks, so people, mimics and
# scripts all landed together. Measured on the adversarial harness, the largest
# cluster contained 37 sessions drawn equally from humans and bots, and 40% of
# legitimate sessions were escalated on it. Two sessions cannot be said to
# behave alike when neither one's behaviour was observed.
BUCKET_MIN_MEASURED = 4


def behavior_bucket(raw_values: dict) -> str | None:
    """A short, stable key for 'this flush behaves like that flush'.

    Takes RAW values, where None means the feature could not be measured, and
    returns None when too few of them were -- no bucket rather than a shared
    one.
    """
    cells = []
    measured = 0
    for name in BUCKET_FEATURES:
        value = raw_values.get(name)
        if value is None or not math.isfinite(value):
            cells.append("x")
        else:
            measured += 1
            cells.append(str(int(round(float(value) / BUCKET_RESOLUTION))))
    if measured < BUCKET_MIN_MEASURED:
        return None
    return hashlib.sha256("|".join(cells).encode("utf-8")).hexdigest()[:16]


# --- Conformal human-plausibility guard -------------------------------------
#
# Designed as a one-directional safety net. Given the scores the trained model
# assigns to held-out human sessions, the conformal p-value of a new score is
# the fraction of those humans who scored at least as high. A large p-value
# means "this score is unremarkable for the calibration humans", and main.py
# then refuses to BLOCK on it, downgrading to step-up verification instead. It
# can only ever soften a decision, never harden one.
#
# AS SERVED IT SOFTENS NOTHING, and calling it a safety net without saying so
# was wrong. Both served bundles (model-sklearn1.5.0.pkl and
# model-sklearn1.8.0.pkl, retrained 2026-09-25) carry n=28 calibration values
# with a maximum of 33.2. Every blocked score is >= 80, so no calibration human
# reaches it, p = 1/(n+1) = 0.034 < CONFORMAL_ALPHA for every block, and the
# guard never fires. Those 28 values are not customers either. The one recorded
# person is on the TRAINING side -- with a single person there is nobody to
# hold out -- so the calibration comes from lab/real_telemetry.json, whose only
# human scenario in this holdout is H1_human: the lab's SCRIPTED Playwright
# persona, one author's idea of a human, driven on one machine.
# The guard therefore protects no real user today, and the existing test that
# shows it softening a block does so with a hand-made calibration in the 90s.
# ModelBundle logs this state once at load, so it is visible without reading
# this comment.
#
# The mechanism is kept because the data it needs is the data the project is
# collecting: calibrated on recordings of real customers on their own
# hardware, any customers the forest scores at 80 or above raise the p-value
# of those scores, and the guard would then soften blocks at the scores real
# people reach -- for whoever scores there, bots included, which is the price
# of the guarantee. That guarantee is distribution-free and independent of the
# model -- a statement about the calibration sample, only as good as how well
# that sample represents the people being scored.
CONFORMAL_ALPHA = 0.05


def conformal_p_value(risk_score: float, calibration: list[float] | None) -> float | None:
    """Fraction of calibration humans scoring at least this high.

    The +1 in numerator and denominator is the standard finite-sample
    correction: with n calibration points the smallest achievable p-value is
    1/(n+1), so the guarantee never claims more precision than the sample
    supports.
    """
    if not calibration:
        return None
    at_least = sum(1 for value in calibration if value >= risk_score)
    return (1.0 + at_least) / (len(calibration) + 1.0)


def get_label(risk_score: float) -> str:
    # A non-finite score must never reach the threshold ladder: every
    # `NaN < threshold` comparison is False, so NaN would fall through the
    # whole chain and silently return the harshest label ("Bot Tespit
    # Edildi") -- blocking a user on the strength of a broken measurement.
    # Treat it as "no verdict" (mid-scale) instead; compute_risk() clamps
    # upstream so this should be unreachable, but the ladder must not be the
    # thing that decides what NaN means.
    if not math.isfinite(risk_score):
        risk_score = 50.0
    for threshold, label in LABELS:
        if risk_score < threshold:
            return label
    return LABELS[-1][1]


def _safe_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(np.var(values))


def _entropy(gaps: list[float]) -> float:
    """Shannon entropy of inter-event gaps over FIXED log-millisecond bins.

    See the ENTROPY_LOG_EDGES note: binning over each session's own range made
    one long human pause collapse the measure to ~0 while a metronomic bot
    scored high.
    """
    if len(gaps) < 2:
        return 0.0
    logs = np.log10(np.clip(np.asarray(gaps, dtype=float), 1.0, None))
    counts = np.histogram(logs, bins=ENTROPY_LOG_EDGES)[0]
    counts = counts[counts > 0]
    if counts.size < 2:
        return 0.0
    probs = counts / counts.sum()
    ent = float(-(probs * np.log2(probs)).sum())
    # Against the most a sample of this size could show, so a short window is
    # not penalised for having fewer gaps than bins.
    max_ent = math.log2(min(len(ENTROPY_LOG_EDGES) - 1, len(gaps)))
    return float(np.clip(ent / max_ent, 0.0, 1.0)) if max_ent > 0 else 0.0


def _channel_entropy(times: list[float]) -> tuple[float, int] | None:
    """Entropy of one channel's own inter-arrival gaps, plus the gap count
    (used as a reliability weight). Returns None if there's not enough data
    (<2 gaps) to measure anything."""
    ts = sorted(times)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    if len(gaps) < 2:
        return None
    return _entropy(gaps), len(gaps)


def _channel_gaps(times: list[float]) -> list[float]:
    ts = sorted(times)
    return [b - a for a, b in zip(ts, ts[1:])]


def _weighted_channel_mean(channel_times, metric, min_gaps: int) -> float | None:
    """Applies `metric` to each channel's own gaps and averages by gap count.

    Per channel and then averaged, for the same reason the entropy is: merging
    several independently regular channels into one stream produces
    beat-frequency artefacts that look irregular even when each channel is
    perfectly robotic on its own.
    """
    values, weights = [], []
    for times in channel_times:
        gaps = _channel_gaps(times)
        if len(gaps) < min_gaps:
            continue
        value = metric(gaps)
        if value is None or not math.isfinite(value):
            continue
        values.append(value)
        weights.append(len(gaps))
    if not values:
        return None
    return float(np.average(values, weights=weights))


def _autocorrelation(values: list[float], lag: int = 1) -> float | None:
    """Lag-1 autocorrelation in [-1, 1], or None when it is undefined.

    A near-constant series returns None rather than a fake 0.0: that case is a
    fixed-velocity script, which ivme_degisimi already answers, and folding it
    in here would collide with the genuinely different "IID noise" case.
    """
    if len(values) < max(lag + 2, MIN_AUTOCORRELATION_SAMPLES):
        return None
    v = np.asarray(values, dtype=float)
    v = v - v.mean()
    denom = float(np.dot(v, v))
    # Relative epsilon: an absolute one misjudges series whose values are all
    # tiny, and pointer speeds in px/ms routinely are.
    if denom <= 1e-12 * max(len(v), 1) or denom <= 1e-18:
        return None
    return float(np.clip(float(np.dot(v[:-lag], v[lag:])) / denom, -1.0, 1.0))


def _modal_repeat_ratio(gaps: list[float]) -> float | None:
    """Fraction of gaps taking the single most common millisecond value.

    A scripted timer (`t += 80`, setInterval, a fixed sleep) emits the same gap
    repeatedly, driving this toward 1.0. Human input sits near 1/n.
    """
    if not gaps:
        return None
    counts = Counter(round(g) for g in gaps)
    return counts.most_common(1)[0][1] / len(gaps)


def _coefficient_of_variation(gaps: list[float]) -> float | None:
    """std/mean of gaps: a shape statistic, not a scale one."""
    if len(gaps) < 2:
        return None
    arr = np.asarray(gaps, dtype=float)
    mean = float(arr.mean())
    if mean <= 1e-9:
        return None
    return float(arr.std() / mean)


def _click_motion_ratio(mouse: list[dict], clicks: list[dict]) -> float | None:
    """Fraction of clicks preceded by pointer motion.

    A person moves the cursor to a target and then clicks. A scripted click
    (`element.click()`, `dispatchEvent`) fires with no pointer motion at all.
    A ratio of counts rather than a distribution statistic, so it degrades
    gracefully on thin flushes: whether motion preceded a click is a fact
    about that click, not an estimate.
    """
    if len(clicks) < MIN_CLICKS_FOR_MOTION_RATIO:
        return None
    mouse_times = sorted(m.get("t", 0) for m in mouse)
    if not mouse_times:
        return 0.0  # clicks with no pointer motion anywhere: a real observation
    with_motion = 0
    for click in clicks:
        t = click.get("t", 0)
        lo = bisect.bisect_left(mouse_times, t - CLICK_MOTION_LOOKBACK_MS)
        if bisect.bisect_right(mouse_times, t) > lo:
            with_motion += 1
    return with_motion / len(clicks)


def _channel_transition_lag(mouse: list[dict], keys: list[dict]) -> float | None:
    """Median pause when the actor switches between keyboard and pointer.

    Moving a hand costs a person a few hundred milliseconds. A script
    alternating between dispatching keydowns and mousemoves pays nothing. This
    is a relationship BETWEEN channels, so imitating typing rhythm and pointer
    motion separately does not satisfy it.
    """
    events = sorted([(m.get("t", 0), "m") for m in mouse] + [(k.get("t", 0), "k") for k in keys])
    lags = [
        b_t - a_t
        for (a_t, a_c), (b_t, b_c) in zip(events, events[1:])
        if a_c != b_c and b_t >= a_t
    ]
    if len(lags) < MIN_CHANNEL_TRANSITIONS:
        return None
    return float(np.median(lags))


def extract_raw(raw: dict) -> dict:
    """Raw, unnormalised quantities from one flush.

    A value of None means the flush carries too little signal to measure that
    feature at all -- fewer than two scroll samples, no hesitation gaps, fewer
    than three trajectory points. None is not zero: zero is the most robotic
    value every one of these can take, so scoring "no data" as zero was
    scoring absence of evidence as evidence of guilt. extract_features()
    substitutes the neutral fallback instead.

    Splitting this out from normalisation is what lets train_model.py measure
    the real distribution of each quantity before choosing a scale for it.
    """
    mouse_trajectory = raw.get("mouse_trajectory") or []
    click_timing = raw.get("click_timing") or []
    scroll_events = raw.get("scroll_events") or []
    hesitation_intervals = raw.get("hesitation_intervals") or []
    focus_changes = raw.get("focus_changes") or []
    key_events = raw.get("key_events") or []

    # scroll_hizi_varyansi: variance of scroll speed (px/ms), needs >=2 samples.
    scroll_speeds = []
    for a, b in zip(scroll_events, scroll_events[1:]):
        dt = max(b.get("t", 0) - a.get("t", 0), 1)
        dy = b.get("scrollY", 0) - a.get("scrollY", 0)
        scroll_speeds.append(dy / dt)
    scroll_raw = _safe_variance(scroll_speeds) if len(scroll_speeds) >= 2 else None

    # tereddut_skoru: mean pause before an action, in milliseconds.
    hesitation_raw = float(np.mean(hesitation_intervals)) if hesitation_intervals else None

    # etkilesim_entropisi: regularity of event spacing, measured PER CHANNEL
    # and then combined -- not by merging all timestamps into one stream first.
    # Merging is tempting but wrong: interleaving several independently regular
    # channels (mouse every 80 ms, clicks every 150 ms, scroll every 90 ms)
    # produces a merged gap sequence that looks irregular even though every
    # channel is perfectly robotic on its own, a beat-frequency artefact.
    # Three period-regular zero-entropy channels merged this way measured ~0.92.
    channel_results = [
        _channel_entropy([m.get("t", 0) for m in mouse_trajectory]),
        _channel_entropy([c.get("t", 0) for c in click_timing]),
        _channel_entropy([s.get("t", 0) for s in scroll_events]),
        _channel_entropy([k.get("t", 0) for k in key_events]),
    ]
    available = [r for r in channel_results if r is not None]
    if available:
        entropy_raw = float(
            np.average([e for e, _ in available], weights=[w for _, w in available])
        )
    else:
        entropy_raw = None

    # ivme_degisimi: variance of pointer ACCELERATION, not of speed. Constant
    # velocity has zero acceleration variance at any speed; a hand never does.
    # Needs >=3 trajectory points to yield >=2 acceleration samples.
    speed_samples = []
    for a, b in zip(mouse_trajectory, mouse_trajectory[1:]):
        dt = max(b.get("t", 0) - a.get("t", 0), 1)
        dx = b.get("x", 0) - a.get("x", 0)
        dy = b.get("y", 0) - a.get("y", 0)
        mid_t = (a.get("t", 0) + b.get("t", 0)) / 2
        speed_samples.append((mid_t, math.hypot(dx, dy) / dt))

    accelerations = []
    for (t1, s1), (t2, s2) in zip(speed_samples, speed_samples[1:]):
        dt = max(t2 - t1, 1)
        accelerations.append((s2 - s1) / dt)
    accel_raw = _safe_variance(accelerations) if len(accelerations) >= 2 else None

    # Counts. Always well defined, including as a legitimate zero: "no clicks
    # happened" is a real observation, not a missing measurement.
    click_times = [c.get("t", 0) for c in click_timing]
    if click_times:
        window_end = max(click_times)
        clicks_in_window = sum(1 for t in click_times if t >= window_end - CLICK_DENSITY_WINDOW_MS)
    else:
        clicks_in_window = 0

    # --- Structural features ------------------------------------------
    # These target the evasion the marginal statistics above cannot see:
    # independent per-step gaussian noise, which reproduces human-looking
    # spread while having none of the temporal structure motor control
    # produces.

    # Speed autocorrelation, mapped from [-1, 1] onto [0, 1]. Real motion
    # accelerates, peaks and decelerates, so consecutive speeds are related;
    # IID jitter gives ~0, landing at the 0.5 midpoint.
    speed_autocorr = _autocorrelation([s for _, s in speed_samples])
    autocorr_raw = None if speed_autocorr is None else (speed_autocorr + 1.0) / 2.0

    # Direction consistency: mean cosine between consecutive move vectors,
    # mapped from [-1, 1] onto [0, 1]. Target-directed motion keeps pointing
    # the same way within a sub-movement; IID jitter re-rolls direction each
    # step (~0.5); a straight-line script never turns (~1). Both extremes are
    # informative and the forest handles the non-monotonic relationship.
    vectors = [
        (b.get("x", 0) - a.get("x", 0), b.get("y", 0) - a.get("y", 0))
        for a, b in zip(mouse_trajectory, mouse_trajectory[1:])
    ]
    cosines = []
    for (ax, ay), (bx, by) in zip(vectors, vectors[1:]):
        na, nb = math.hypot(ax, ay), math.hypot(bx, by)
        if na < 1e-9 or nb < 1e-9:
            continue  # zero-length step: direction undefined, not opposed
        cosines.append((ax * bx + ay * by) / (na * nb))
    direction_raw = (
        (float(np.mean(cosines)) + 1.0) / 2.0 if len(cosines) >= MIN_DIRECTION_SAMPLES else None
    )

    channel_times = [
        [m.get("t", 0) for m in mouse_trajectory],
        [c.get("t", 0) for c in click_timing],
        [s.get("t", 0) for s in scroll_events],
        [k.get("t", 0) for k in key_events],
    ]
    quantization_raw = _weighted_channel_mean(
        channel_times, _modal_repeat_ratio, MIN_TIMING_GAPS
    )
    dispersion = _weighted_channel_mean(
        channel_times, _coefficient_of_variation, MIN_TIMING_GAPS
    )
    dispersion_raw = None if dispersion is None else dispersion / DISPERSION_DIVISOR

    motion_ratio_raw = _click_motion_ratio(mouse_trajectory, click_timing)
    lag = _channel_transition_lag(mouse_trajectory, key_events)
    transition_raw = None if lag is None else lag / TRANSITION_LAG_DIVISOR

    return {
        "scroll_hizi_varyansi": scroll_raw,
        "tereddut_skoru": hesitation_raw,
        "etkilesim_entropisi": entropy_raw,
        "ivme_degisimi": accel_raw,
        "tiklama_yogunlugu": float(clicks_in_window),
        "odak_degisimi": float(len(focus_changes)),
        "hiz_otokorelasyonu": autocorr_raw,
        "yon_tutarliligi": direction_raw,
        "zaman_kuantasyonu": quantization_raw,
        "duraklama_dagilimi": dispersion_raw,
        "tiklama_oncesi_hareket": motion_ratio_raw,
        "kanal_gecis_gecikmesi": transition_raw,
    }


def extract_features(raw: dict, raw_values: dict | None = None) -> dict:
    """Raw SDK payload -> the six model features, each on 0..1.

    Normalisation comes from the training distribution (see
    normalize_feature and LOG_SCALED_FEATURES), not from hand-picked
    divisors, so the features use their range instead of sitting on the
    ceiling.
    """
    raw_values = extract_raw(raw) if raw_values is None else raw_values
    scaling = get_feature_scaling()
    defaults = get_neutral_defaults()

    features = {}
    for name in FEATURE_NAMES:
        value = raw_values[name]
        if value is None or not math.isfinite(value):
            features[name] = float(defaults.get(name, 0.0))
        else:
            features[name] = normalize_feature(name, value, scaling)
    return features


def compute_risk(raw: dict) -> dict:
    """Scores one flush.

    What happens across flushes -- smoothing, level shifts, the sequential
    test -- is session logic and lives in smooth_session_score() and main.py's
    decision path, not in the model.
    """
    start = time.perf_counter()
    bundle = get_bundle()

    raw_values = extract_raw(raw)
    features = extract_features(raw, raw_values)
    measured = measured_feature_count(raw_values)
    # Bit i set when FEATURE_NAMES[i] was genuinely measured in this flush,
    # rather than filled in with NEUTRAL_DEFAULTS by extract_features. Stored
    # on behavior_data.measured_mask. Nothing below reads it: it is computed
    # from raw_values alone and never touches `features`, so the score cannot
    # move (test_measured_mask_does_not_change_the_score). It exists for the
    # per-customer profile, which must not mistake a default 0.3 for a
    # measured 0.3 -- a profile built over defaults measures "how much
    # telemetry did this session produce", not "is this the same person".
    #
    # The finiteness check matches extract_features' own fallback rule, so a
    # set bit means exactly "the model saw a measured value here".
    measured_mask = sum(
        1 << i
        for i, name in enumerate(FEATURE_NAMES)
        if raw_values.get(name) is not None and math.isfinite(raw_values[name])
    )

    # Defence in depth against non-finite values. The API layer rejects NaN /
    # Infinity at the boundary (see main.py's typed payload models), which is
    # where this belongs -- but if one ever slips through, /api/analyze commits
    # the row to Postgres BEFORE serializing the response, so a NaN would be
    # made durable and then break JSON serialization on every subsequent read
    # of that session. Sanitizing here keeps a bad measurement from ever
    # reaching the model or the database.
    for name, value in features.items():
        if not math.isfinite(value):
            features[name] = NEUTRAL_DEFAULTS.get(name, 0.0)

    feature_vector = np.array([[features[name] for name in FEATURE_NAMES]])
    scaled = bundle.scaler.transform(feature_vector)

    fraud_probability = float(bundle.rf.predict_proba(scaled)[0][1])

    # The score is the RandomForest alone. Two former ensemble members -- an
    # LSTM and an Isolation Forest -- were removed from it, each on
    # measurement, and both have now been PUT BACK ON TRIAL and measured
    # again. `model_selection.py` reproduces every number below.
    #
    # WHY THEY WERE RETRIED. The measurements that dropped them were taken
    # against a training distribution now known to be wrong about real
    # browsers: the simulator drew event timestamps from continuous
    # distributions, while a real browser delivers pointer events on renderer
    # frame boundaries and therefore repeats the same millisecond gap by
    # construction. The first real person this project scored came out at a
    # per-flush median of 68.0 because of it. A model judged on that
    # distribution was judged unfairly, so the study was re-run end to end on
    # the corrected simulator, with the browser lab re-captured so its rows
    # carry raw telemetry and can be re-extracted under the scale in force.
    #
    # WHAT THE RETRIAL RETRACTED. The previous version of this comment said
    # gradient boosting was rejected because, with the H1 human scenario held
    # out of training, LightGBM blocked 74% of those unseen humans at 80 while
    # the forest blocked none. That number does not reproduce, and it should
    # not have been quoted: it was measured on 234 lab rows stored as
    # already-normalised vectors, frozen to a superseded scale and superseded
    # neutral fallbacks. Re-captured with raw and re-extracted, the same
    # scenario gives LightGBM 0.04 and the forest 0.00.
    #
    # WHAT THE RETRIAL FOUND, on 301 real rows (252 scripted browser-lab
    # flushes across 46 runs, 49 flushes from the one recorded person) blended
    # with 6,400 simulated sessions. Protocol D holds one SCENARIO out of
    # training; it is the column a payment product is chosen on, because every
    # real customer is unseen by construction. "worst unseen human" is the
    # human scenario each model treats worst when it never trained on it:
    #
    #                   C.auc  C.tpr@.8  worst unseen  unseen A2   1-row
    #                                     human  >=.6/.8  bot >=.8   latency
    #     RandomForest  0.996   0.81      0.05 (H2) 0.00    0.00      13.3 ms
    #     ExtraTrees    0.998   0.92      0.63 (H2) 0.00    0.00      20.8 ms
    #     HistGB        0.995   0.95      0.15 (H1) 0.02    0.00       4.6 ms
    #     LightGBM      0.994   0.93      0.30 (H2) 0.04    0.00       0.5 ms
    #     XGBoost       0.996   0.94      0.14 (H2) 0.00    0.00       0.5 ms
    #     LogReg        0.982   0.79      0.42 (H2) 0.14    0.12       0.2 ms
    #     MLP           0.996   0.92      0.04 (H1) 0.02    0.75       0.2 ms
    #
    # The boosting families do catch more bots: +0.11 to +0.15 on tpr@0.8, and
    # a bootstrap resampling the 47 real GROUPS separates that from zero.
    # Their extra false-challenge cost on the lab (+0.014 to +0.020 on
    # fpr@0.6) does NOT separate from zero.
    #
    # THE LAST COLUMN IS THE UNCOMFORTABLE ONE, and it is about this forest,
    # not about them. With the randomised-bot scenario held out of training,
    # the forest catches NONE of it -- 0.00 at both the step-up and the block
    # line -- and the MLP catches 0.75. That is not one lucky seed: refitted at
    # five random_states the forest gives 0.00 every time and the MLP 0.75
    # every time. The same holds on the naive-bot scenario (forest 0.57, MLP
    # 1.00 at every seed). docs/evaluation.md has long said this detector
    # "catches attack techniques it has samples of, and does not generalise to
    # techniques it has not seen". This measures that sentence, and it says the
    # limitation is a property of the MODEL FAMILY, not of the data.
    #
    # WHY THE MLP IS STILL NOT ADOPTED, measured rather than asserted. Refit at
    # those same five seeds, its share of an UNSEEN keyboard-only human
    # scenario challenged at 60 runs 0.00 / 0.00 / 0.07 / 0.16 / 0.33, and its
    # recall on the unseen evasive family at 80 runs 0.06 / 0.12 / 0.33 / 0.44
    # / 0.61. The forest's corresponding spreads are 0.00-0.05 and 0.00-0.00.
    # A model whose false-challenge rate on unseen legitimate users is a coin
    # flip over random_state cannot hold a payment gate, and picking the seed
    # that looks best is the exact move this project's honesty rule exists to
    # stop. Seed-averaging or ensembling it is the obvious next experiment; it
    # needs real people to be measured against, not another lab run.
    #
    # And the one real person cannot break the tie either. Held out BY PERSON
    # -- protocol R -- the smoothed session score /api/decision reads comes out
    # at 0.0 for LogReg, 1.1 for the MLP, 39.2 for LightGBM, 40.9 for the
    # forest, 46.2 for ExtraTrees, 72.6 for XGBoost and 97.9 for HistGB: three
    # allows, three verifies and a block. One person, one machine, one session,
    # seven verdicts. It is not stable within a family either -- running this
    # same study against the PREVIOUS bundle, a scale differing by 0.04 on two
    # of twelve features, moved LightGBM's number on that session from 0.0 to
    # 39.2 while leaving its lab metrics unchanged to three decimals.
    #
    # So the forest stays, and the reason is not that it won. It is the most
    # STABLE model here on the axis a payment gate cannot be wrong about, and
    # the measurement that would justify trading that stability for the MLP's
    # generalisation -- a false-challenge rate on real people, with an interval
    # on it -- does not exist yet.
    #
    # THE LSTM. Still out, and now for a better reason than "it collapses".
    # Trained the way it used to be shipped (simulated full sequences only) it
    # scores ROC-AUC 0.848 with a Brier of 0.440 on the real rows; trained on
    # the shape the SERVING path actually produces -- padded prefixes, which
    # build_sequence() emits from the first flush -- it improves to 0.871 with
    # the Brier still at 0.424, i.e. it ranks far better than it calibrates.
    # Blending the real rows in out of fold finally fixes the calibration
    # (Brier 0.113) without closing the gap in rank. All three sit below the
    # forest's 0.996, and every blend tested is worse than the forest alone:
    #
    #     RandomForest alone                     auc 0.996  brier 0.026  tpr@.8 0.81
    #     0.6 RF + 0.4 LSTM (best variant)       auc 0.990  brier 0.039  tpr@.8 0.66
    #     the former 0.6/0.4 + escalation rule   auc 0.996  brier 0.033  tpr@.8 0.77
    #     the former 0.5 RF / 0.2 LSTM / 0.3 IF  auc 0.977  brier 0.094  tpr@.8 0.12
    #
    # The one case it exists for is a mid-session handover, and it still loses
    # there. On simulated drift_to_bot sessions, where the generator changes at
    # flush 6, the share caught at the flush AFTER the switch is: forest
    # reading the current flush alone 1.00; LSTM on padded prefixes 0.63, and
    # 1.00 one flush later; LSTM as formerly shipped 0.00 until flush 10.
    # Neither variant raises a single pure-human session at any k, so this is
    # not a threshold effect -- the sequence model is simply one flush, or
    # four, behind a forest that looks at nothing but now. smooth_session_
    # score()'s level-shift rule already covers the case it was hired for.
    # Handing the forest the previous flushes as extra columns adds nothing
    # either (auc 0.996 against 0.996, tpr@0.8 0.80 against 0.81).
    #
    # THE ISOLATION FOREST. Still out; the old reason is RETRACTED and a new
    # one measured. The old reason was inversion: fitted on human rows only,
    # it scored ROC-AUC 0.340 on real browser rows -- worse than chance,
    # systematically voting for the attacker. On the corrected data it is no
    # longer inverted. It is merely useless in the direction that matters:
    #
    #     ROC-AUC on the real rows                     0.657  (forest 0.996)
    #     share of legitimate flushes it puts >=0.6      0.38  (forest 0.00)
    #     share of the recorded person's flushes >=0.6   0.76  (forest 0.00)
    #     unseen H1_human flushes it puts >=0.6          0.87  (forest 0.00)
    #     stored-card checkout, median session score     54.0  (forest 0.8)
    #
    # That shape is not a tuning problem, it is what a one-class detector has
    # to be here: it learns "normal" as the human distribution, and in this
    # product the attack IS looking human, so the only thing it can be
    # confident about is that an unusual human is unusual. Every blend that
    # gives it weight loses -- RF 0.9 + IsoF 0.1 drops tpr@0.8 from 0.81 to
    # 0.77 while raising the recorded person's out-of-fold session score from
    # 45.7 to 51.1, and max(RF, IsoF) takes the legitimate-flush challenge
    # rate to 0.38.
    #
    # It is also 44% of the scoring budget: compute_risk measured 31.7 ms with
    # the decision_function call and 17.7 ms without it. The model is still
    # trained and still stored in the bundle so the choice stays
    # re-measurable, but nothing calls it per request, and test_scorer.py
    # booby-traps decision_function to keep it that way.
    #
    # WHAT WOULD CHANGE ANY OF THIS. Not a better ensemble -- more people. The
    # deciding column is protocol R, and it has n=1 person, one machine, one
    # 60 Hz monitor, one hand; that person is on the TRAINING side because
    # with one person there is nobody to hold out. Twenty to thirty recorded
    # people with several sessions each, split by person, would turn "the
    # smoothed score is somewhere between 0.0 and 97.9 depending on which
    # model you pick" into a rate with an interval on it, and that rate is
    # what should choose the family. Devices matter as much as headcount: no
    # trackpad, touch, pen or throttled session has ever been recorded, and
    # the simulator's 0.70/0.20/0.10 refresh-rate mix is an assumption fitted
    # to the only machine there is.

    # What this number is. A RandomForest's predict_proba is the mean over
    # trees of the bot fraction in the leaf each tree lands in: a ranking
    # score, not a calibrated P(fraud | behaviour). Nothing here recalibrates
    # it (no Platt or isotonic step), the class balance it learned is the
    # training set's (simulated sessions, scripted lab runs and one recorded
    # person), not any real traffic's fraud rate, and the Brier score of 0.026
    # above was measured on those same rows. So
    # "Risk Score = 100 x P(fraud | behavior)" states the intent, not a
    # measured property. Two consequences, stated where they bite: the
    # 40/60/80 ladder and the conformal guard are thresholds on a ranking, and
    # main.py's sequential test treats each flush's logit as an evidence
    # weight rather than a log-likelihood ratio, so its bounds are an operating
    # point chosen by measurement, not Wald's error rates. "confidence" below
    # is the same vote share folded at 0.5, not a probability of being right.
    #
    # Degrade a non-finite probability to "unknown" (0.5) instead of persisting
    # a value that cannot be serialized or compared.
    if not math.isfinite(fraud_probability):
        fraud_probability = 0.5
    risk_score = round(100 * fraud_probability, 1)
    label = get_label(risk_score)

    shap_values = np.array(bundle.explainer.shap_values(scaled))
    # Newer SHAP: (n_samples, n_features, n_classes). Older SHAP: (n_classes, n_samples, n_features).
    # Single-output fallback: (n_samples, n_features).
    if shap_values.ndim == 3:
        if shap_values.shape[0] == scaled.shape[0]:
            fraud_class_shap = shap_values[0, :, -1]
        else:
            fraud_class_shap = shap_values[-1][0]
    else:
        fraud_class_shap = shap_values[0]

    impacts = [
        {
            "feature": FEATURE_NAMES[i],
            "value": round(float(features[FEATURE_NAMES[i]]), 2),
            "impact": round(float(abs(fraud_class_shap[i])) * 100, 1),
        }
        for i in range(len(FEATURE_NAMES))
    ]
    impacts.sort(key=lambda x: x["impact"], reverse=True)
    top_3 = impacts[:3]

    response_time_ms = round((time.perf_counter() - start) * 1000, 1)

    return {
        "risk_score": risk_score,
        "label": label,
        "measured_features": measured,
        "measured_mask": measured_mask,
        # Whether anything about the GENERATOR was observed in this flush, as
        # opposed to counts and neutral fallbacks. smooth_session_score()
        # requires it before a single flush may override the session's
        # history; see the note on LEVEL_SHIFT_POINTS.
        "observed_structure": any(
            raw_values.get(name) is not None for name in BUCKET_FEATURES
        ),
        # True when too little was measured for the score to mean much. The
        # score is still returned and stored; this says how much weight it can
        # carry.
        "provisional": measured < MIN_MEASURED_FOR_CONFIDENT_SCORE,
        "behavior_bucket": behavior_bucket(raw_values),
        "confidence": round(max(fraud_probability, 1 - fraud_probability), 2),
        "shap_explanation": top_3,
        "response_time_ms": response_time_ms,
        "features": features,
    }
