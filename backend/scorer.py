"""Feature extraction, model inference, SHAP explanation and risk scoring."""

import math
import os
import time
from collections import Counter

import joblib
import numpy as np
import shap
import torch

from lstm_model import FEATURE_NAMES, BehaviorLSTM, build_sequence_from_features

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(MODEL_DIR, "model.pkl")
LSTM_PATH = os.path.join(MODEL_DIR, "lstm_model.pt")

CLICK_DENSITY_WINDOW_MS = 5000

# Empirically calibrated: raw variance(acceleration) from real mouse coordinates
# lands around 1e-6 to 1e-7 (px/ms^2, squared) for natural human jitter, since
# acceleration divides by elapsed-ms twice. The previous /2.0 divisor assumed
# speed-delta-sized magnitudes and silently collapsed every real session to
# ~0.0 regardless of how human or robotic the movement actually was. This
# divisor maps a natural-human trajectory's acceleration variance to ~0.45,
# matching the training distribution's human mean (see train_model.py).
# Calibrated against real browser mouse traces captured by tools/bot-harness,
# which measured acceleration variance between 8.6e-5 and 1.6e-3. The previous
# value of 2.2e-6 was two to three orders of magnitude below that, so every
# real trace clipped to 1.0 and the feature distinguished nothing -- a
# straight-line constant-velocity bot scored identically to a bezier-eased one.
# This divisor puts those bots in the lower third with headroom above.
#
# NOTE: calibrated from bot traces only. Real human traces are needed to
# confirm humans land above this range rather than inside it.
ACCELERATION_VARIANCE_DIVISOR = 5.0e-3

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
# Once a window carries at least this many tracked events, the session has
# told us enough that silence in any ONE channel is itself a measurement
# rather than missing data.
SESSION_ACTIVITY_THRESHOLD = 12

NEUTRAL_DEFAULTS = {
    "scroll_hizi_varyansi": 0.17,
    "tereddut_skoru": 0.45,
    "etkilesim_entropisi": 0.66,
    "ivme_degisimi": 0.35,
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


def _channel_entropy(times: list[float]) -> tuple[float, int] | None:
    """Entropy of one channel's own inter-arrival gaps, plus the gap count
    (used as a reliability weight). Returns None if there's not enough data
    (<2 gaps) to measure anything."""
    ts = sorted(times)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    if len(gaps) < 2:
        return None
    return _entropy(gaps), len(gaps)


def extract_features(raw: dict) -> dict:
    """Turns raw SDK payload into the 6 model features, each normalized to ~0-1."""
    mouse_trajectory = raw.get("mouse_trajectory") or []
    click_timing = raw.get("click_timing") or []
    scroll_events = raw.get("scroll_events") or []
    hesitation_intervals = raw.get("hesitation_intervals") or []
    focus_changes = raw.get("focus_changes") or []
    key_events = raw.get("key_events") or []

    # A feature with no samples means one of two very different things, and
    # scoring them the same is what let a bot with no cursor pass as human.
    #
    #   quiet session  -- the page just loaded, nothing has happened anywhere.
    #                     There is genuinely nothing to measure, so neutral is
    #                     correct; guessing "bot" would punish a user for not
    #                     having acted yet.
    #   active session -- plenty happened, just not in THIS channel. That is a
    #                     measurement: no scrolling across a busy window, or no
    #                     cursor movement alongside 120 keystrokes, is exactly
    #                     what automation looks like.
    #
    # Only the first deserves the neutral default. The second gets 0.0, the
    # bot-ward end of every one of these features.
    tracked_events = len(mouse_trajectory) + len(click_timing) + len(scroll_events) + len(key_events)
    session_is_active = tracked_events >= SESSION_ACTIVITY_THRESHOLD

    def _absent(feature: str) -> float:
        return 0.0 if session_is_active else NEUTRAL_DEFAULTS[feature]

    # scroll_hizi_varyansi: variance of scroll speed, normalized.
    # Needs >=2 scroll samples to compute a variance at all -- with fewer,
    # there is no measurement to make, so fall back to neutral (not 0.0).
    scroll_speeds = []
    for a, b in zip(scroll_events, scroll_events[1:]):
        dt = max(b.get("t", 0) - a.get("t", 0), 1)
        dy = b.get("scrollY", 0) - a.get("scrollY", 0)
        scroll_speeds.append(dy / dt)
    if len(scroll_speeds) >= 2:
        scroll_hizi_varyansi = float(np.clip(_safe_variance(scroll_speeds) / 5.0, 0.0, 1.0))
    else:
        scroll_hizi_varyansi = _absent("scroll_hizi_varyansi")

    # tereddut_skoru: normalized average pause before actions (ms / 1500).
    # An empty list here usually means too few tracked events fired to even
    # measure a gap, not that the user paused zero times -- neutral fallback.
    if hesitation_intervals:
        tereddut_skoru = float(np.clip(np.mean(hesitation_intervals) / 1500.0, 0.0, 1.0))
    else:
        tereddut_skoru = _absent("tereddut_skoru")

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
        etkilesim_entropisi = _absent("etkilesim_entropisi")

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
    if len(accelerations) >= 2:
        ivme_degisimi = float(np.clip(_safe_variance(accelerations) / ACCELERATION_VARIANCE_DIVISOR, 0.0, 1.0))
    else:
        ivme_degisimi = _absent("ivme_degisimi")

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

    return {
        "scroll_hizi_varyansi": scroll_hizi_varyansi,
        "tereddut_skoru": tereddut_skoru,
        "etkilesim_entropisi": etkilesim_entropisi,
        "ivme_degisimi": ivme_degisimi,
        "tiklama_yogunlugu": tiklama_yogunlugu,
        "odak_degisimi": odak_degisimi,
    }


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
        seq = build_sequence_from_features([features[name] for name in FEATURE_NAMES])
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
