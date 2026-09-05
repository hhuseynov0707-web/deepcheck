"""Feature extraction, model inference, SHAP explanation and risk scoring."""

import bisect
import logging
import math
import os
import time
from collections import Counter

import joblib
import numpy as np
import shap
import sklearn
import torch

from lstm_model import FEATURE_NAMES, SEQUENCE_LENGTH, BehaviorLSTM, build_sequence

logger = logging.getLogger("deepcheck.scorer")

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))

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

# Features that never need a neutral fallback: they are simple counts (clicks
# in window, focus-loss count) that are always well-defined, including as a
# legitimate 0.
NEUTRAL_FEATURES = tuple(NEUTRAL_DEFAULTS)

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
        # Both artifacts or neither. The LSTM used to be loaded only "if the
        # file happens to exist", which meant a deployment with model.pkl but
        # no lstm_model.pt ran the sequence model with RANDOM initial weights
        # -- contributing 30% of every risk score as noise, with nothing
        # logged and /api/health still reporting the model as loaded. A
        # missing weight file is a broken install, not a degraded mode.
        if not os.path.exists(LSTM_PATH):
            raise ModelUnavailableError(
                "lstm_model.pt bulunamadı. Model dosyaları eksik; "
                "`python train_model.py` çalıştırın."
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

        self.lstm = BehaviorLSTM()
        self.lstm.load_state_dict(torch.load(LSTM_PATH, map_location="cpu"))
        self.lstm.eval()


# Same rationale as rf.n_jobs above, for torch: the LSTM forward pass on a
# single 10-step sequence is ~1.4ms of work, far too little to be worth
# splitting across threads. Left at the default, each uvicorn worker would
# spawn a thread per core, and with multiple workers those pools oversubscribe
# the machine and slow every request down.
torch.set_num_threads(1)


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


def extract_features(raw: dict) -> dict:
    """Raw SDK payload -> the six model features, each on 0..1.

    Normalisation comes from the training distribution (see
    normalize_feature and LOG_SCALED_FEATURES), not from hand-picked
    divisors, so the features use their range instead of sitting on the
    ceiling.
    """
    raw_values = extract_raw(raw)
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


def _sanitize_row(row, defaults: dict) -> list[float]:
    """One historical feature row -> a clean float vector in FEATURE_NAMES
    order. History comes out of Postgres, where a column can be NULL and a
    pre-validation row can hold a non-finite value; either would poison the
    whole sequence with NaN."""
    clean = []
    for name, value in zip(FEATURE_NAMES, row):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = float("nan")
        if not math.isfinite(value):
            value = defaults.get(name, 0.0)
        clean.append(float(np.clip(value, 0.0, 1.0)))
    return clean


def compute_risk(raw: dict, history: list[list[float]] | None = None) -> dict:
    """Scores one flush.

    `history` is this session's earlier flushes, oldest first, each a feature
    vector in FEATURE_NAMES order (main.py reads them back from Postgres).
    They are the LSTM's time-series context: without them the sequence model
    sees SEQUENCE_LENGTH copies of one instant and cannot react to a session
    whose behavior *changes*, which is the only thing a sequence model is
    there to catch. Passing nothing is still valid and reproduces the old
    single-observation behavior.
    """
    start = time.perf_counter()
    bundle = get_bundle()

    features = extract_features(raw)

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

    rf_proba = float(bundle.rf.predict_proba(scaled)[0][1])

    iso_raw = bundle.iso_forest.decision_function(scaled)[0]
    # decision_function: higher = more normal. Flip + squash to a 0-1 anomaly score.
    iso_anomaly = float(np.clip(0.5 - iso_raw, 0.0, 1.0))

    current_row = [features[name] for name in FEATURE_NAMES]
    defaults = get_neutral_defaults()
    # Only the most recent SEQUENCE_LENGTH - 1 earlier flushes matter; slicing
    # here keeps a caller that hands over a whole session from paying for rows
    # build_sequence would discard anyway.
    past_rows = [_sanitize_row(row, defaults) for row in (history or [])[-(SEQUENCE_LENGTH - 1):]]

    with torch.no_grad():
        seq = build_sequence(past_rows + [current_row])
        lstm_proba = float(bundle.lstm(seq).item())

    fraud_probability = float(np.clip(0.5 * rf_proba + 0.2 * iso_anomaly + 0.3 * lstm_proba, 0.0, 1.0))
    # np.clip propagates NaN rather than clamping it, so an upstream NaN would
    # survive the clip above. Degrade to "unknown" (0.5) instead of persisting
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
        "confidence": round(max(fraud_probability, 1 - fraud_probability), 2),
        "shap_explanation": top_3,
        "response_time_ms": response_time_ms,
        "features": features,
    }
