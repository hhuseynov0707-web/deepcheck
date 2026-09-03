"""Feature extraction, model inference, SHAP explanation and risk scoring."""

import math
import os
import time
from collections import Counter

import joblib
import numpy as np
import shap
import torch

from lstm_model import (
    FEATURE_NAMES,
    SEQUENCE_LENGTH,
    BehaviorLSTM,
    build_sequence_from_features,
)

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(MODEL_DIR, "model.pkl")
LSTM_PATH = os.path.join(MODEL_DIR, "lstm_model.pt")

CLICK_DENSITY_WINDOW_MS = 5000

# Must match sdk/deepcheck.js's HESITATION_THRESHOLD_MS and train_model.py's:
# it defines what counts as a "pause" when hesitations are re-derived from
# event gaps while slicing a flush into LSTM timesteps.
HESITATION_THRESHOLD_MS = 400

# LSTM windowing (see build_sequence). Each timestep spans this fraction of
# the flush, so consecutive windows overlap heavily and every step keeps
# enough events for the variance/entropy/autocorrelation estimators.
SEQUENCE_WINDOW_FRACTION = 0.4
MIN_EVENTS_FOR_SEQUENCE = 12
MIN_SPAN_MS_FOR_SEQUENCE = 500

# Minimum sample counts before the kinematics features are treated as
# measurements rather than noise. Estimating an autocorrelation or a mean
# turning angle from a handful of points produces a number, but not evidence;
# below these counts the feature falls back to neutral. Bots that actually
# simulate motion emit far more than this (the evasion these features target
# used 30 trajectory points), so the gate costs no detection.
MIN_AUTOCORRELATION_SAMPLES = 8  # speed samples, i.e. >=9 trajectory points
MIN_DIRECTION_SAMPLES = 6  # consecutive-vector pairs, i.e. >=8 trajectory points
# Same rule for the two timing-shape statistics. A coefficient of variation or
# a modal-repeat ratio over 3-4 gaps is dominated by sampling noise: a human
# typing five keys at a steady rhythm produced a "too regular" reading and
# scored 81.7 ("Bot Tespit Edildi"), which is a blocked customer, not a bot.
MIN_TIMING_GAPS = 5

# The same rule, applied to the three original distribution-shape features.
# They were gated at >=2 samples, which is enough to *compute* a variance or an
# entropy but nowhere near enough to trust one, and that gap turned out to be
# the real cause of the sparse-human false positive: a 5-mousemove session
# produced a 3-sample acceleration variance (0.065, deep in bot territory) and
# a 4-gap entropy of exactly 1.0 -- small-sample entropy saturates at its
# maximum whenever the few values it sees are distinct, so "not much data"
# read as "maximally irregular". Together those scored an ordinary typing
# session 89.2 ("Bot Tespit Edildi").
#
# This was survivable while the human training persona was itself a random
# walk with similarly degenerate statistics. Once the human persona became
# physically realistic, these thin estimates stopped resembling the human
# distribution and started reading as bots. The fix is not to weaken the
# features but to stop treating an under-sampled estimate as a measurement.
MIN_ACCELERATION_SAMPLES = 6  # i.e. >=8 trajectory points
MIN_ENTROPY_GAPS = 5
MIN_SCROLL_SPEED_SAMPLES = 4  # scrolling is naturally sparser than pointer motion

# Thresholds for signal_sufficiency(). A full 2s SDK flush from an active user
# carries dozens of events; these mark the point below which the feature
# vector is mostly neutral fallbacks rather than measurement.
MIN_EVENTS_FOR_CONFIDENT_VERDICT = 25
MIN_SPAN_MS_FOR_CONFIDENT_VERDICT = 1500

# Empirically calibrated: raw variance(acceleration) from real mouse coordinates
# lands around 1e-6 to 1e-7 (px/ms^2, squared) for natural human jitter, since
# acceleration divides by elapsed-ms twice. The previous /2.0 divisor assumed
# speed-delta-sized magnitudes and silently collapsed every real session to
# ~0.0 regardless of how human or robotic the movement actually was. This
# divisor maps a natural-human trajectory's acceleration variance to ~0.45,
# matching the training distribution's human mean (see train_model.py).
ACCELERATION_VARIANCE_DIVISOR = 2.2e-6

# When a feature can't be mathematically computed because a request carries
# too little raw signal (e.g. a 2s window where the user was only typing, not
# moving the mouse -- variance/entropy/acceleration all need >=2-3 samples),
# falling back to 0.0 is actively wrong: 0.0 sits at or below the trained
# *bot* mean, so "no data" was being scored as "more suspicious than an
# actual bot". These neutral fallbacks are the midpoint between the human/bot
# training means in train_model.py -- "no evidence" should not count as
# evidence toward either class.
#
# IMPORTANT: these are derived from train_model.py's persona distributions
# (average of human/human_rushed vs average of bot/bot_sophisticated,
# midpoint of the two) and MUST be recomputed any time those persona
# distributions change meaningfully -- they drifted stale once already (a
# training-data rebalance shifted tereddut_skoru's true midpoint from ~0.26
# to ~0.44 without this constant being updated, silently reintroducing a
# false positive on sparse-data sessions). See the P0 diagnosis in this
# conversation for how the drift was found.
#
# tiklama_yogunlugu and odak_degisimi are deliberately excluded: they are
# simple counts (clicks in window, focus-loss count) that are always
# well-defined, including as a legitimate 0 -- "zero clicks happened" is
# real information, not a missing measurement, so no neutral fallback applies.
# The four kinematics/timing values below are maintained the same way and for
# the same reason. They are regenerated (not hand-tuned) by
# `python train_model.py --print-neutral-defaults`, which simulates every
# persona and prints the human/bot midpoint for each feature -- run it after
# any change to the personas and paste the result here.
NEUTRAL_DEFAULTS = {
    "scroll_hizi_varyansi": 0.09,  # human 0.10 / bot 0.08
    "tereddut_skoru": 0.54,  # human 0.54 / bot 0.54
    "etkilesim_entropisi": 0.68,  # human 0.64 / bot 0.73
    "ivme_degisimi": 0.64,  # human 0.84 / bot 0.44
    # (tiklama_yogunlugu and odak_degisimi are intentionally absent -- see the
    # note above. --print-neutral-defaults prints a midpoint for every feature;
    # only the ones that can genuinely be *unmeasurable* belong in this dict.)
    "hiz_otokorelasyonu": 0.61,  # human 0.72 / bot 0.51
    "yon_tutarliligi": 0.86,  # human 0.87 / bot 0.85
    "zaman_kuantasyonu": 0.18,  # human 0.12 / bot 0.23
    "duraklama_dagilimi": 0.32,  # human 0.45 / bot 0.18
}

LABELS = [
    (40, "Gerçek Kullanıcı"),
    (60, "Şüpheli"),
    (80, "Yüksek Risk"),
    (101, "Bot Tespit Edildi"),
]


class ModelBundle:
    def __init__(self):
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                "model.pkl bulunamadı. Önce `python train_model.py` çalıştırın."
            )
        bundle = joblib.load(MODEL_PATH)
        self.scaler = bundle["scaler"]
        self.rf = bundle["rf"]
        self.iso_forest = bundle["iso_forest"]
        self.feature_names = bundle["feature_names"]

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
        if os.path.exists(LSTM_PATH):
            # weights_only=True: torch.load() otherwise unpickles arbitrary
            # objects, so a writable checkpoint is remote code execution at
            # import time. The default flipped to True in torch 2.6, but this
            # project pins 2.3 -- so it must be passed explicitly. Restricting
            # to tensors costs nothing here: this file only ever holds a
            # state_dict.
            self.lstm.load_state_dict(
                torch.load(LSTM_PATH, map_location="cpu", weights_only=True)
            )
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


def _entropy(values: list[float], bins: int = 10) -> float:
    if len(values) < 2:
        return 0.0
    counts = Counter(np.digitize(values, np.linspace(min(values), max(values) + 1e-9, bins)))
    total = sum(counts.values())
    probs = [c / total for c in counts.values()]
    ent = -sum(p * math.log2(p) for p in probs if p > 0)
    max_ent = math.log2(min(bins, len(values))) or 1.0
    return float(np.clip(ent / max_ent, 0.0, 1.0))


def _channel_gaps(times: list[float]) -> list[float]:
    ts = sorted(times)
    return [b - a for a, b in zip(ts, ts[1:])]


def _channel_entropy(times: list[float]) -> tuple[float, int] | None:
    """Entropy of one channel's own inter-arrival gaps, plus the gap count
    (used as a reliability weight). Returns None if there's not enough data
    (<2 gaps) to measure anything."""
    gaps = _channel_gaps(times)
    if len(gaps) < MIN_ENTROPY_GAPS:
        return None
    return _entropy(gaps), len(gaps)


def _weighted_channel_mean(
    channel_times: list[list[float]],
    metric,
    min_gaps: int,
) -> float | None:
    """Applies `metric` to each channel's own inter-arrival gaps and averages
    the results, weighted by how many gaps each channel contributed.

    Per-channel-then-average (rather than merging every timestamp into one
    stream first) for the same reason _channel_entropy does it: interleaving
    several independently-regular channels produces beat-frequency artifacts
    that look irregular even when each channel is perfectly robotic.
    """
    values: list[float] = []
    weights: list[int] = []
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
    """Lag-`lag` autocorrelation of a series, in [-1, 1].

    Returns None when the coefficient is undefined -- too few samples, or a
    series with (near-)zero variance. A perfectly constant series is exactly
    what a fixed-velocity script produces; that case is deliberately left to
    ivme_degisimi rather than being folded in here as a fake "0.0", which
    would collide with the genuinely different "IID noise" case.

    MIN_AUTOCORRELATION_SAMPLES is a correctness requirement, not tuning. An
    autocorrelation estimated from a handful of points is dominated by its own
    sampling error: a mostly-typing human with 5 mousemove events yields 4
    speed samples, whose "autocorrelation" is essentially noise. Feeding that
    to the model as if it were a measurement scored such a session 85.7
    ("Bot Tespit Edildi") -- a false positive on an ordinary card-form user,
    which is precisely the failure this module's neutral-fallback design
    exists to prevent. Below the threshold there is no measurement, so the
    caller must fall back to neutral rather than act on a coin flip.
    """
    if len(values) < max(lag + 2, MIN_AUTOCORRELATION_SAMPLES):
        return None
    v = np.asarray(values, dtype=float)
    v = v - v.mean()
    denom = float(np.dot(v, v))
    # Relative epsilon: an absolute one would misjudge series whose values are
    # all tiny (speeds in px/ms are routinely ~1e-2).
    if denom <= 1e-12 * max(len(v), 1) or denom <= 1e-18:
        return None
    num = float(np.dot(v[:-lag], v[lag:]))
    return float(np.clip(num / denom, -1.0, 1.0))


def _modal_repeat_ratio(gaps: list[float]) -> float | None:
    """Fraction of inter-event gaps that take the single most common value.

    A scripted timer (`t += 80`, setInterval, a fixed sleep) emits the same
    millisecond gap over and over, driving this to ~1.0. Human input is
    dispatched on real hardware/OS timing and effectively never repeats an
    exact millisecond gap, so this sits near 1/n.
    """
    if not gaps:
        return None
    counts = Counter(round(g) for g in gaps)
    return counts.most_common(1)[0][1] / len(gaps)


def _coefficient_of_variation(gaps: list[float]) -> float | None:
    """std/mean of inter-event gaps -- a shape statistic, not a scale one.

    Human pauses are heavy-tailed (mostly quick, occasionally very long),
    giving a high CV. Both of the easy script behaviours land lower: a fixed
    delay has CV ~0, and a `uniform(a, b)` delay -- the usual first attempt at
    "looking human" -- is capped at the uniform distribution's CV.
    """
    if len(gaps) < 2:
        return None
    arr = np.asarray(gaps, dtype=float)
    mean = float(arr.mean())
    if mean <= 1e-9:
        return None
    return float(arr.std() / mean)


def extract_features(raw: dict) -> dict:
    """Turns raw SDK payload into the 6 model features, each normalized to ~0-1."""
    mouse_trajectory = raw.get("mouse_trajectory") or []
    click_timing = raw.get("click_timing") or []
    scroll_events = raw.get("scroll_events") or []
    hesitation_intervals = raw.get("hesitation_intervals") or []
    focus_changes = raw.get("focus_changes") or []
    key_events = raw.get("key_events") or []

    # scroll_hizi_varyansi: variance of scroll speed, normalized.
    # Needs >=2 scroll samples to compute a variance at all -- with fewer,
    # there is no measurement to make, so fall back to neutral (not 0.0).
    scroll_speeds = []
    for a, b in zip(scroll_events, scroll_events[1:]):
        dt = max(b.get("t", 0) - a.get("t", 0), 1)
        dy = b.get("scrollY", 0) - a.get("scrollY", 0)
        scroll_speeds.append(dy / dt)
    if len(scroll_speeds) >= MIN_SCROLL_SPEED_SAMPLES:
        scroll_hizi_varyansi = float(np.clip(_safe_variance(scroll_speeds) / 5.0, 0.0, 1.0))
    else:
        scroll_hizi_varyansi = NEUTRAL_DEFAULTS["scroll_hizi_varyansi"]

    # tereddut_skoru: normalized average pause before actions (ms / 1500).
    # An empty list here usually means too few tracked events fired to even
    # measure a gap, not that the user paused zero times -- neutral fallback.
    if hesitation_intervals:
        tereddut_skoru = float(np.clip(np.mean(hesitation_intervals) / 1500.0, 0.0, 1.0))
    else:
        tereddut_skoru = NEUTRAL_DEFAULTS["tereddut_skoru"]

    # etkilesim_entropisi: entropy of event spacing across mouse+click+scroll+
    # keydown, measured PER CHANNEL and then combined -- not by merging all
    # timestamps into one stream first. Merging first is tempting but wrong:
    # interleaving several independently-regular channels (e.g. mouse every
    # 80ms, clicks every 150ms, scroll every 90ms) produces a merged gap
    # sequence that looks highly irregular even though every channel is
    # perfectly robotic on its own (a beat-frequency artifact of combining
    # different periods) -- empirically, three period-regular 0.0-entropy
    # channels merged into one stream measured ~0.92. Scoring each channel's
    # own regularity and averaging (weighted by how many gaps each channel
    # contributed) avoids this entirely.
    channel_results = [
        _channel_entropy([m.get("t", 0) for m in mouse_trajectory]),
        _channel_entropy([c.get("t", 0) for c in click_timing]),
        _channel_entropy([s.get("t", 0) for s in scroll_events]),
        _channel_entropy([k.get("t", 0) for k in key_events]),
    ]
    available = [r for r in channel_results if r is not None]
    if available:
        entropies = [e for e, _ in available]
        weights = [w for _, w in available]
        etkilesim_entropisi = float(np.average(entropies, weights=weights))
    else:
        etkilesim_entropisi = NEUTRAL_DEFAULTS["etkilesim_entropisi"]

    # ivme_degisimi: variance of mouse acceleration (d(speed)/dt), not just speed delta.
    # Needs >=3 trajectory points (>=2 acceleration samples) to compute at all.
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
    if len(accelerations) >= MIN_ACCELERATION_SAMPLES:
        ivme_degisimi = float(np.clip(_safe_variance(accelerations) / ACCELERATION_VARIANCE_DIVISOR, 0.0, 1.0))
    else:
        ivme_degisimi = NEUTRAL_DEFAULTS["ivme_degisimi"]

    # tiklama_yogunlugu: click density in the most recent 5s window
    click_times = [c.get("t", 0) for c in click_timing]
    if click_times:
        window_end = max(click_times)
        window_start = window_end - CLICK_DENSITY_WINDOW_MS
        clicks_in_window = sum(1 for t in click_times if t >= window_start)
        tiklama_yogunlugu = float(np.clip(clicks_in_window / 10.0, 0.0, 1.0))
    else:
        tiklama_yogunlugu = 0.0

    # odak_degisimi: how often the tab/window lost focus (visibilitychange events)
    odak_degisimi = float(np.clip(len(focus_changes) / 5.0, 0.0, 1.0))

    # --- Evasion-resistant features -------------------------------------
    # These target the specific, cheap evasion that defeats the variance /
    # entropy features above: emitting independent per-step Gaussian noise.
    # Such a stream reproduces human-looking *marginal* statistics while
    # having none of the temporal structure real motor control produces.

    # hiz_otokorelasyonu: does speed correlate with its own previous value?
    # Real pointer motion has momentum (accelerate -> peak -> decelerate), so
    # consecutive speeds are strongly related. IID jitter gives ~0 (mapped to
    # the 0.5 midpoint below). Mapped from [-1, 1] to [0, 1].
    speeds = [s for _, s in speed_samples]
    speed_autocorr = _autocorrelation(speeds)
    if speed_autocorr is None:
        hiz_otokorelasyonu = NEUTRAL_DEFAULTS["hiz_otokorelasyonu"]
    else:
        hiz_otokorelasyonu = float(np.clip((speed_autocorr + 1.0) / 2.0, 0.0, 1.0))

    # yon_tutarliligi: mean cosine between consecutive movement vectors.
    # Target-directed human motion keeps pointing roughly the same way within
    # a sub-movement (high); IID jitter re-rolls direction every step (~0 ->
    # 0.5 after mapping); a straight-line script never turns (~1). Both
    # extremes are informative, and the tree ensemble handles the
    # non-monotonic relationship directly. Mapped from [-1, 1] to [0, 1].
    vectors = []
    for a, b in zip(mouse_trajectory, mouse_trajectory[1:]):
        vectors.append((b.get("x", 0) - a.get("x", 0), b.get("y", 0) - a.get("y", 0)))
    cosines = []
    for (ax, ay), (bx, by) in zip(vectors, vectors[1:]):
        na, nb = math.hypot(ax, ay), math.hypot(bx, by)
        # Skip zero-length steps: direction is undefined, not "opposed".
        if na < 1e-9 or nb < 1e-9:
            continue
        cosines.append((ax * bx + ay * by) / (na * nb))
    # Same small-sample rule as the autocorrelation above: the mean of two or
    # three turning angles says nothing reliable about whether motion is
    # target-directed.
    if len(cosines) >= MIN_DIRECTION_SAMPLES:
        yon_tutarliligi = float(np.clip((float(np.mean(cosines)) + 1.0) / 2.0, 0.0, 1.0))
    else:
        yon_tutarliligi = NEUTRAL_DEFAULTS["yon_tutarliligi"]

    # zaman_kuantasyonu / duraklama_dagilimi: timing-shape statistics, measured
    # per channel and averaged by gap count (see _weighted_channel_mean).
    channel_times = [
        [m.get("t", 0) for m in mouse_trajectory],
        [c.get("t", 0) for c in click_timing],
        [s.get("t", 0) for s in scroll_events],
        [k.get("t", 0) for k in key_events],
    ]

    quantization = _weighted_channel_mean(
        channel_times, _modal_repeat_ratio, min_gaps=MIN_TIMING_GAPS
    )
    zaman_kuantasyonu = (
        NEUTRAL_DEFAULTS["zaman_kuantasyonu"]
        if quantization is None
        else float(np.clip(quantization, 0.0, 1.0))
    )

    # CV is normalized by 1.5: a lognormal(sigma~0.5) human gap distribution
    # sits near 0.5, uniform-random script delays near 0.3, and fixed delays
    # at 0. The divisor keeps the human range off the clip ceiling so the
    # feature still discriminates above the human mean.
    dispersion = _weighted_channel_mean(
        channel_times, _coefficient_of_variation, min_gaps=MIN_TIMING_GAPS
    )
    duraklama_dagilimi = (
        NEUTRAL_DEFAULTS["duraklama_dagilimi"]
        if dispersion is None
        else float(np.clip(dispersion / 1.5, 0.0, 1.0))
    )

    return {
        "scroll_hizi_varyansi": scroll_hizi_varyansi,
        "tereddut_skoru": tereddut_skoru,
        "etkilesim_entropisi": etkilesim_entropisi,
        "ivme_degisimi": ivme_degisimi,
        "tiklama_yogunlugu": tiklama_yogunlugu,
        "odak_degisimi": odak_degisimi,
        "hiz_otokorelasyonu": hiz_otokorelasyonu,
        "yon_tutarliligi": yon_tutarliligi,
        "zaman_kuantasyonu": zaman_kuantasyonu,
        "duraklama_dagilimi": duraklama_dagilimi,
    }


def _event_times(raw: dict) -> list[float]:
    return sorted(
        [m.get("t", 0) for m in (raw.get("mouse_trajectory") or [])]
        + [c.get("t", 0) for c in (raw.get("click_timing") or [])]
        + [s.get("t", 0) for s in (raw.get("scroll_events") or [])]
        + [k.get("t", 0) for k in (raw.get("key_events") or [])]
    )


def _slice_raw(raw: dict, t_start: float, t_end: float) -> dict:
    """The sub-payload of `raw` whose events fall in [t_start, t_end].

    hesitation_intervals carry no timestamp of their own, so they cannot be
    filtered; they are re-derived from the gaps between the events actually
    inside the slice, mirroring the SDK's recordHesitation() threshold.
    """
    def keep(items, get_t):
        return [i for i in items if t_start <= get_t(i) <= t_end]

    mouse = keep(raw.get("mouse_trajectory") or [], lambda m: m.get("t", 0))
    clicks = keep(raw.get("click_timing") or [], lambda c: c.get("t", 0))
    scrolls = keep(raw.get("scroll_events") or [], lambda s: s.get("t", 0))
    keys = keep(raw.get("key_events") or [], lambda k: k.get("t", 0))
    focus = keep(raw.get("focus_changes") or [], lambda f: f)

    times = sorted(
        [m.get("t", 0) for m in mouse]
        + [c.get("t", 0) for c in clicks]
        + [s.get("t", 0) for s in scrolls]
        + [k.get("t", 0) for k in keys]
    )
    hesitations = [
        b - a for a, b in zip(times, times[1:]) if (b - a) >= HESITATION_THRESHOLD_MS
    ]

    return {
        "mouse_trajectory": mouse,
        "click_timing": clicks,
        "scroll_events": scrolls,
        "key_events": keys,
        "focus_changes": focus,
        "hesitation_intervals": hesitations,
    }


def build_sequence(raw: dict, features: dict) -> torch.Tensor:
    """Per-timestep feature sequence for the LSTM, from one flush's raw events.

    The LSTM used to be fed the same aggregate snapshot tiled SEQUENCE_LENGTH
    times, i.e. a constant series -- a "temporal" model with no temporal signal
    to read, which is why it contributed little beyond what the RF already saw.
    Instead, slide a window across the flush and extract features per position,
    so the sequence actually describes how behaviour evolved over the window.

    Windows overlap (each spans WINDOW_FRACTION of the flush) so that every
    step still contains enough events for the variance/entropy/autocorrelation
    estimators to be meaningful -- disjoint slices would be too sparse and the
    sequence would be mostly neutral fallbacks.

    Falls back to tiling the aggregate when the flush is too short or too
    sparse to slice, which keeps sparse sessions on the same code path the
    model was trained with rather than feeding it degenerate windows.
    """
    times = _event_times(raw)
    span = (times[-1] - times[0]) if len(times) >= 2 else 0

    if len(times) < MIN_EVENTS_FOR_SEQUENCE or span < MIN_SPAN_MS_FOR_SEQUENCE:
        return build_sequence_from_features([features[name] for name in FEATURE_NAMES])

    t0 = times[0]
    width = span * SEQUENCE_WINDOW_FRACTION
    # Last window ends exactly at the final event; first starts at the first.
    step = (span - width) / (SEQUENCE_LENGTH - 1)

    rows = []
    for i in range(SEQUENCE_LENGTH):
        w_start = t0 + i * step
        sliced = _slice_raw(raw, w_start, w_start + width)
        sliced_features = extract_features(sliced)
        rows.append([sliced_features[name] for name in FEATURE_NAMES])

    seq = torch.tensor(rows, dtype=torch.float32)
    return seq.unsqueeze(0)  # (1, SEQUENCE_LENGTH, NUM_FEATURES)


def signal_sufficiency(raw: dict) -> float:
    """How much real evidence this flush carries, in [0, 1].

    A near-empty payload (a headless script that submits a form without ever
    moving, clicking or typing) makes most features fall back to their neutral
    defaults, so it scores mid-scale by construction -- below the blocking
    threshold. That is correct for the *score* (absence of evidence is not
    evidence), but it must not be silently treated as "cleared": the caller
    decides policy from this value, so a signal-starved session can be stepped
    up rather than allowed. See main.py's transaction decision.
    """
    times = _event_times(raw)
    span = (times[-1] - times[0]) if len(times) >= 2 else 0
    event_score = min(len(times) / MIN_EVENTS_FOR_CONFIDENT_VERDICT, 1.0)
    span_score = min(span / MIN_SPAN_MS_FOR_CONFIDENT_VERDICT, 1.0) if span else 0.0
    # Both matter: 200 events in 50ms is a burst, not a confident observation.
    return round(float(min(event_score, span_score)), 3)


def compute_risk(raw: dict) -> dict:
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

    with torch.no_grad():
        seq = build_sequence(raw, features)
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
        "signal_sufficiency": signal_sufficiency(raw),
    }
