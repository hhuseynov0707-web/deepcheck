"""Model-selection study: every component's retrial on the corrected data.

Run from backend/ (about 20 minutes on a laptop, most of it feature extraction):
    python model_selection.py
    python model_selection.py --json results.json
    N_SESSIONS=1500 python model_selection.py     # smoke run; numbers not quotable

LightGBM and XGBoost are compared when installed (`pip install lightgbm
xgboost`); they are not serving dependencies and are skipped otherwise.

WHY THIS STUDY WAS RE-RUN FROM SCRATCH
--------------------------------------
The Isolation Forest and the LSTM were each dropped from the served score on
measurement. Those measurements were taken against a simulator that was wrong
about real browsers, and a model judged on a broken distribution was judged
unfairly.

What was wrong: a real browser delivers pointer events on renderer frame
boundaries, so it repeats the same millisecond gap by construction -- in the
one recorded human session, 52.1% of deduplicated pointer gaps are exactly
17 ms and 30.0% are 16 ms. The simulator drew timestamps from continuous
distributions and essentially never repeated one. The forest learned
"repeated millisecond gap = script", and the first real person this project
ever scored came out at a per-flush median of 68.0 with 90% of flushes at or
above 60. Replacing that single feature with the simulated-human median moved
the median to 19.1.

So the simulator now models a frame clock and a browser-autofill persona, the
log-percentile scaling pool now contains real raw values, and the recorded
session reaches training. Every number in the previous edition of this file
was measured before all of that. None of them carried over; the whole study is
re-run here and the verdict is re-derived, whichever way it falls.

PROTOCOLS, in the order they should be read
-------------------------------------------
  A  sim -> sim            in-distribution sanity check (synthetic test split)
  B  sim -> lab            train on the simulator only, score the scripted
                           browser-lab flushes
  C  blend, grouped        synthetic + real, 5-fold StratifiedGroupKFold by
                           GROUP (person where one is recorded, else lab run)
  D  leave-one-scenario-out: the scenario being scored was never trained on
  R  leave-one-PERSON-out: the recorded human's own flushes, scored by a model
                           that never saw that person -- per flush AND through
                           smooth_session_score(), because the session score is
                           what /api/decision reads and the payment turns on
  F  autofill scenarios    a stored card filled by the browser, i.e. a checkout
                           with no keydown channel at all, scored by protocol
                           R's models

Read D and R before the others. The lab rows are scripted, so C mostly asks
whether a model recognises other runs of scripts it has already seen. D asks
what happens to behaviour the model has NOT seen, and R asks what happens to a
person it has not seen -- which is what every real customer is.

WHAT THE SAMPLE SIZES ALLOW
---------------------------
One recorded person. Every protocol-R and protocol-F number is therefore an
ANECDOTE about one person on one machine with one 60 Hz monitor, not a
false-challenge RATE, and the flush-level confidence intervals printed beside
them are intervals on that one sitting only: the flushes are not independent
and the person-level n is 1. The lab contributes a few dozen scripted runs, so
protocol-C and protocol-D differences of a few points are inside the noise --
the bootstrap section prints which ones, and saying so is part of the answer.
"""
import argparse
import json
import math
import os
import random
import sys
import time
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("DEBUG", "1")
BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
_parser = argparse.ArgumentParser(description="DeepCheck model-selection study")
_parser.add_argument("--json", default=os.path.join(BACKEND, "model_selection_results.json"))
OUT = _parser.parse_args().json

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.ensemble import (
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    IsolationForest,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold, train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from lightgbm import LGBMClassifier
except ImportError:
    LGBMClassifier = None
try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None

import scorer
import train_model as T
from lstm_model import FEATURE_NAMES, SEQUENCE_LENGTH, BehaviorLSTM, build_sequence

torch.set_num_threads(os.cpu_count() or 1)
bundle = scorer.get_bundle()  # served scaling + neutral defaults drive extraction
T.rng = np.random.default_rng(2026)
N_SESSIONS = int(os.getenv("N_SESSIONS", "8000"))
NF = len(FEATURE_NAMES)
BOT_PERSONAS = {"bot", "bot_sophisticated"}
RESULTS = {"config": {"n_synthetic_sessions": N_SESSIONS, "sklearn": __import__("sklearn").__version__}}


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- data
t0 = time.perf_counter()
seqs = np.empty((N_SESSIONS, SEQUENCE_LENGTH, NF))
y_syn = np.empty(N_SESSIONS, dtype=int)
personas = []
for i in range(N_SESSIONS):
    label = int(T.rng.integers(0, 2))
    persona = T._pick_persona(label)
    base = 1_700_000_000_000 + int(T.rng.integers(0, 10**9))
    for step, payload in enumerate(T.simulate_session_windows(persona, base)):
        f = scorer.extract_features(payload)
        seqs[i, step] = [f[n] for n in FEATURE_NAMES]
    y_syn[i] = label
    personas.append(persona)
personas = np.array(personas)
log(f"synthetic: {N_SESSIONS} sessions in {time.perf_counter() - t0:.0f}s")

idx_tr, idx_te = train_test_split(np.arange(N_SESSIONS), test_size=0.2, random_state=42, stratify=y_syn)

# Through the loader training uses, not straight off the JSON. A row carrying
# raw telemetry is re-extracted under the served bundle loaded above; a row
# without it is used only if the file vouches for the extraction that produced
# it. Reading stored features here would let the study score vectors the
# forest never sees once the scale changes.
real = T.load_real_rows()
if real is None:
    sys.exit("no usable real rows (lab/real_telemetry.json + data/real/)")
real_X, real_y = real["X"], real["y"]
# Fold groups are the holdout's unit: the PERSON for a recording, the run for
# a scripted lab capture. No protocol puts one person's flushes on both sides.
real_group = np.array(real["group"])
real_scen = np.array(real["scenario"])
real_source = np.array(real["source"])
real_session = np.array(real["session"])
real_order = np.array(real["order"])
is_lab = real_source == "lab"
is_rec = real_source == "recorded"
PERSONS = sorted(set(real_group[is_rec].tolist()))
log(
    f"real rows: {len(real_y)} ({int(real['from_raw'].sum())} re-extracted from raw) -- "
    f"{int(is_lab.sum())} browser-lab flushes in {len(set(real_group[is_lab].tolist()))} runs, "
    f"{int(is_rec.sum())} recorded flushes from {len(PERSONS)} person(s) {PERSONS}"
)
RESULTS["config"]["real_rows"] = {
    "total": int(len(real_y)),
    "lab_flushes": int(is_lab.sum()),
    "lab_runs": len(set(real_group[is_lab].tolist())),
    "recorded_flushes": int(is_rec.sum()),
    "recorded_persons": PERSONS,
    "re_extracted_from_raw": int(real["from_raw"].sum()),
}

# Each row's own session up to and including it, in flush order.
_by_session = {}
for _i, (_s, _o) in enumerate(zip(real["session"], real["order"])):
    _by_session.setdefault(_s, []).append((_o, _i))
prefix_of = {}
for _rows in _by_session.values():
    _ordered = [i for _, i in sorted(_rows)]
    for _rank, _i in enumerate(_ordered):
        prefix_of[_i] = _ordered[: _rank + 1]


def history(i, k=SEQUENCE_LENGTH):
    """Rows of row i's session up to and including i (oldest first)."""
    return real_X[prefix_of[i][-k:]]


# ------------------------------------------------- the autofill scenarios
# An e-wallet's ordinary customer does not type a card number: the browser or
# the device fills it. That removes an entire CHANNEL (key_events) while
# leaving the pointer intact, and no lab scenario has that shape -- H2 is
# keyboard-only, H1 types.
#
# Pointer timestamps are RESAMPLED from the recorded human's own gap
# distribution rather than from a round 16 ms. That matters: the recorded
# browser puts 52% of its pointer gaps on 17 ms and 30% on 16 ms, and a
# generator using a constant 16 hands zaman_kuantasyonu a value (~1.0) no real
# browser produces -- making the scenario harder than reality instead of equal
# to it.
#
# Because the gaps come from the recorded person, these scenarios are only
# out-of-sample against a model that did not train on that person. They are
# scored by protocol R's models for exactly that reason.
HESITATION_THRESHOLD_MS = 400   # sdk/deepcheck.js HESITATION_THRESHOLD_MS
ROLLING_WINDOW_MS = 10_000      # sdk/deepcheck.js ROLLING_WINDOW_MS
FLUSH_INTERVAL_MS = 2_000       # sdk/deepcheck.js DEFAULT_INTERVAL_MS
AUTOFILL_BASE_T = 1_790_280_000_000
AUTOFILL_SEEDS = (0, 1, 2, 3, 4)


def recorded_pointer_gaps(max_gap_ms=60):
    """Intra-movement pointer gaps from the recorded human sessions.

    Capped: a gap above this is the person pausing, which the scenarios place
    deliberately, not the browser's sampling rate.
    """
    samples, _ = T.load_recorded_samples()
    seen, times = set(), []
    for sample in samples:
        if sample["label"] != 0:
            continue
        for ev in (sample.get("raw") or {}).get("mouse_trajectory") or []:
            key = (ev.get("t"), ev.get("x"), ev.get("y"))
            if key not in seen:
                seen.add(key)
                times.append(ev.get("t", 0))
    times.sort()
    return [b - a for a, b in zip(times, times[1:]) if 0 < b - a <= max_gap_ms]


class _Hand:
    """A pointer moving along a minimum-jerk path, sampled at the recorded
    browser's frame-quantised gaps."""

    def __init__(self, seed, gaps, x=640.0, y=520.0):
        self.r = random.Random(seed)
        self.gaps = gaps
        self.x, self.y, self.t = x, y, float(AUTOFILL_BASE_T)
        self.mouse, self.clicks, self.keys = [], [], []

    def dwell(self, ms):
        self.t += ms

    def move_to(self, tx, ty, duration_ms):
        x0, y0, elapsed = self.x, self.y, 0.0
        while elapsed < duration_ms:
            gap = self.r.choice(self.gaps)
            elapsed += gap
            self.t += gap
            u = min(elapsed / duration_ms, 1.0)
            s = 10 * u**3 - 15 * u**4 + 6 * u**5          # minimum-jerk profile
            self.mouse.append({
                "x": x0 + (tx - x0) * s + self.r.normalvariate(0, 1.1),
                "y": y0 + (ty - y0) * s + self.r.normalvariate(0, 1.1),
                "t": int(self.t),
            })
        self.x, self.y = tx, ty

    def click(self):
        self.t += self.r.uniform(60, 180)
        self.clicks.append({"x": self.x, "y": self.y, "t": int(self.t)})

    def type_keys(self, n, median_ms=190, sigma=0.45):
        for _ in range(n):
            self.t += self.r.lognormvariate(math.log(median_ms), sigma)
            self.keys.append({"t": int(self.t)})


def _payload(mouse, clicks, keys):
    times = sorted([m["t"] for m in mouse] + [c["t"] for c in clicks] + [k["t"] for k in keys])
    return {
        "mouse_trajectory": list(mouse),
        "click_timing": list(clicks),
        "scroll_events": [],
        "key_events": list(keys),
        "focus_changes": [],
        "hesitation_intervals": [b - a for a, b in zip(times, times[1:]) if (b - a) >= HESITATION_THRESHOLD_MS],
    }


def rolling_windows(h):
    """The payloads the SDK would actually post: a 10 s rolling window every
    2 s. Scoring one hand-built payload would measure a flush shape the SDK
    never sends."""
    all_t = [m["t"] for m in h.mouse] + [c["t"] for c in h.clicks] + [k["t"] for k in h.keys]
    if not all_t:
        return []
    start, end, out = min(all_t), max(all_t), []
    now = start + FLUSH_INTERVAL_MS
    while now <= end + FLUSH_INTERVAL_MS:
        lo = now - ROLLING_WINDOW_MS
        w = _payload(
            [m for m in h.mouse if lo <= m["t"] <= now],
            [c for c in h.clicks if lo <= c["t"] <= now],
            [k for k in h.keys if lo <= k["t"] <= now],
        )
        if any(w[ch] for ch in ("mouse_trajectory", "click_timing", "key_events")):
            out.append(w)
        now += FLUSH_INTERVAL_MS
    return out


# Card, expiry and CVV fields plus an "Onayla" button, laid out like
# frontend/src/pages/Demo.jsx.
FIELD_CARD, SUGGESTION = (560.0, 300.0), (560.0, 348.0)
FIELD_CVV, BUTTON_CONFIRM = (760.0, 372.0), (640.0, 470.0)


def _autofill_no_keys(h):
    """Saved card, filled by the browser. ZERO keydown events: autofill does
    not type. Reach the field, click, pick the suggestion, read, confirm."""
    h.dwell(900); h.move_to(*FIELD_CARD, 620); h.click()
    h.dwell(650); h.move_to(*SUGGESTION, 260); h.click()
    h.dwell(1500); h.move_to(*BUTTON_CONFIRM, 540); h.dwell(400); h.click()


def _autofill_plus_cvv(h):
    """The commonest stored-card flow: everything autofilled except the CVV,
    which the customer types -- three keystrokes and one channel transition."""
    h.dwell(900); h.move_to(*FIELD_CARD, 620); h.click()
    h.dwell(650); h.move_to(*SUGGESTION, 260); h.click()
    h.dwell(1100); h.move_to(*FIELD_CVV, 380); h.click()
    h.dwell(500); h.type_keys(3, median_ms=210)
    h.dwell(700); h.move_to(*BUTTON_CONFIRM, 500); h.dwell(350); h.click()


def _typed_control(h):
    """Control: the same generator, the same hand, typing all 16 digits plus
    expiry and CVV. Not an autofill case -- it is here so the autofill numbers
    are read against a hand-typed flow built the same way, rather than only
    against the recorded session."""
    h.dwell(900); h.move_to(*FIELD_CARD, 620); h.click()
    h.dwell(420); h.type_keys(16, median_ms=185)
    h.dwell(500); h.type_keys(4, median_ms=200)          # expiry
    h.dwell(450); h.move_to(*FIELD_CVV, 340); h.click()
    h.dwell(400); h.type_keys(3, median_ms=210)
    h.dwell(700); h.move_to(*BUTTON_CONFIRM, 500); h.dwell(350); h.click()


AUTOFILL_SCENARIOS = {
    "autofill_no_keystrokes": _autofill_no_keys,
    "autofill_plus_typed_cvv": _autofill_plus_cvv,
    "typed_card_control": _typed_control,
}


def build_autofill():
    """{scenario: [run, ...]}, each run a list of feature vectors in flush
    order. Deterministic, so a re-run scores the same events."""
    gaps = recorded_pointer_gaps()
    if not gaps:
        return {}, 0
    out = {}
    for name, build in AUTOFILL_SCENARIOS.items():
        runs = []
        for seed in AUTOFILL_SEEDS:
            h = _Hand(hash((name, seed)) % 10_000_007, gaps)
            build(h)
            rows = [[scorer.extract_features(w)[n] for n in FEATURE_NAMES] for w in rolling_windows(h)]
            if rows:
                runs.append(np.array(rows, dtype=float))
        out[name] = runs
    return out, len(gaps)


AUTOFILL, _n_gaps = build_autofill()
if AUTOFILL:
    log(f"autofill scenarios: pointer gaps resampled from {_n_gaps} recorded intra-movement gaps "
        f"(median {np.median(recorded_pointer_gaps()):.0f} ms), "
        + ", ".join(f"{k} {len(v)}x{len(v[0])} flushes" for k, v in AUTOFILL.items() if v))
else:
    log("autofill scenarios: SKIPPED (no recorded human raw telemetry to resample pointer gaps from)")


# ---------------------------------------------------------------- metrics
def ece(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    return float(sum((idx == b).mean() * abs(p[idx == b].mean() - y[idx == b].mean()) for b in range(bins) if (idx == b).any()))


def wilson(k, n, z=1.96):
    """95% Wilson interval for a proportion. Printed beside the recorded
    person's flush shares as a reminder of how little one sitting pins down --
    it is an interval on THESE flushes, which are not independent, and the
    person-level n is 1 either way."""
    if not n:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [float((c - r) / d), float((c + r) / d)]


def metrics(y, p):
    y, p = np.asarray(y), np.asarray(p, dtype=float)
    h, b = p[y == 0], p[y == 1]
    out = {
        "n": int(len(y)),
        "auc": float(roc_auc_score(y, p)) if len(set(y.tolist())) == 2 else None,
        "brier": float(brier_score_loss(y, p)),
        "ece": ece(y, p),
    }
    for thr in (0.5, 0.6, 0.8):
        out[f"fpr@{thr}"] = float((h >= thr).mean()) if len(h) else None
        out[f"tpr@{thr}"] = float((b >= thr).mean()) if len(b) else None
    return out


# ---------------------------------------------------------------- models
class OneClassIF:
    """IsolationForest as a one-class component, fitted the way train_model
    fits it: on the HUMAN rows only.

    sklearn gives it no predict_proba, and an anomaly score is not a
    probability, so this maps it through the empirical CDF of the TRAINING
    humans' own anomaly scores: p = the share of known-normal rows this row is
    more anomalous than. That is the only reading under which "0.8" means for
    this model what it means for the others -- "more unusual than 80% of
    known-normal" -- and the reference set is fitted on the training side
    alone, so it never sees the rows it scores.

    The mapping is monotone, so ROC-AUC is unaffected by it; Brier and ECE
    are, and are reported for what they are.
    """

    def __init__(self, **kw):
        self.m = IsolationForest(**kw)

    def fit(self, X, y, sample_weight=None):
        X, y = np.asarray(X, dtype=float), np.asarray(y)
        human = X[y == 0]
        w = None if sample_weight is None else np.asarray(sample_weight, dtype=float)[y == 0]
        self.m.fit(human, sample_weight=w)
        self._ref = np.sort(-self.m.decision_function(human))
        return self

    def predict_proba(self, X):
        s = -self.m.decision_function(np.asarray(X, dtype=float))
        p = np.searchsorted(self._ref, s, side="left") / max(len(self._ref), 1)
        return np.column_stack([1 - p, p])


FACTORIES = {
    "RF (shipped)": lambda: RandomForestClassifier(n_estimators=200, max_depth=12, max_features=4, min_samples_leaf=5, random_state=42, n_jobs=-1),
    "ExtraTrees": lambda: ExtraTreesClassifier(n_estimators=300, max_features=4, min_samples_leaf=5, random_state=42, n_jobs=-1),
    "HistGB": lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=20, l2_regularization=1.0, random_state=42),
    "LightGBM": lambda: LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=42, verbose=-1),
    "XGBoost": lambda: XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=os.cpu_count(), eval_metric="logloss"),
    "LogReg": lambda: Pipeline([("s", StandardScaler()), ("m", LogisticRegression(C=1.0, max_iter=5000))]),
    "MLP": lambda: Pipeline([("s", StandardScaler()), ("m", MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300, early_stopping=True, random_state=42))]),
    # Same parameters train_model.py fits into the served bundle.
    "IsolationForest (1-class)": lambda: OneClassIF(n_estimators=100, contamination=T.CONTAMINATION_RATE, random_state=42, n_jobs=-1),
}

if LGBMClassifier is None:
    FACTORIES.pop("LightGBM")
if XGBClassifier is None:
    FACTORIES.pop("XGBoost")


def fit(name, X, y, w=None):
    model = FACTORIES[name]()
    if w is None:
        model.fit(X, y)
        return model
    try:
        if isinstance(model, Pipeline):
            model.fit(X, y, m__sample_weight=w)
        else:
            model.fit(X, y, sample_weight=w)
    except (TypeError, ValueError):
        reps = np.maximum(1, np.round(w / w.min()).astype(int))
        model = FACTORIES[name]()
        model.fit(np.repeat(X, reps, axis=0), np.repeat(y, reps))
    return model


def proba(model, X):
    return model.predict_proba(np.asarray(X, dtype=float))[:, 1]


def blend_weights(n_syn, n_real):
    # Real rows count, in aggregate, as much as the synthetic rows -- the same
    # ratio train_model.py states as REAL_TELEMETRY_MASS_SHARE = 0.5. This
    # expression is what that constant derives; train_model used to hard-code
    # a per-row 120 that happened to produce it for one dataset size.
    return np.concatenate([np.ones(n_syn), np.full(n_real, n_syn / max(n_real, 1))])


X_syn_tr, y_syn_tr = seqs[idx_tr, -1], y_syn[idx_tr]
X_syn_te, y_syn_te = seqs[idx_te, -1], y_syn[idx_te]
sgkf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0)
folds = list(sgkf.split(real_X, real_y, groups=real_group))


# ---------------------------------------------------------------- protocols
obs_structure = real["observed_structure"]
REC_IDX = {p: np.flatnonzero(is_rec & (real_group == p)) for p in PERSONS}


def smoothed(idx, p):
    """Replay smooth_session_score() over these rows, in flush order, per
    session -- the value /api/decision reads and the payment turns on.

    Returns (every value the sessions passed through, the last value of each
    session). Both matter: the decision can be requested after any flush, and
    the last one is where Onayla lands. M3 measured these two moving in
    OPPOSITE directions on the recorded session -- the per-flush median fell
    to 0.0 while the smoothed score rose to 88.4 -- so a study that reports
    only per-flush numbers can miss the failure that decides the payment.
    """
    by = {}
    for i, prob in zip(idx, p):
        by.setdefault(real_session[i], []).append(
            (real_order[i], round(100.0 * float(prob), 1), bool(obs_structure[i]))
        )
    every, final, bypasses = [], [], 0
    for key in sorted(by):
        seen, last = [], None
        for _, score, structural in sorted(by[key], key=lambda r: r[0]):
            last = scorer.smooth_session_score(seen, score, structural)
            # Did this flush override the median rather than join it? That is
            # how a session of calm flushes ends on a blocking score, and it
            # cost a real person a payment once already (scorer.py's note
            # above SMOOTHING_WINDOW), so the study counts it rather than
            # leaving it to be inferred from a gap between two numbers.
            if seen and last > float(np.median(seen + [score])) + 1e-9:
                bypasses += 1
            every.append(last)
            seen.append(score)
        if last is not None:
            final.append(last)
    return every, final, bypasses


def person_report(p_by_index):
    """What a model does to a recorded person's own human flushes."""
    out = {}
    for person, idx in REC_IDX.items():
        human = idx[real_y[idx] == 0]
        if not len(human):
            continue
        p = np.array([p_by_index[i] for i in human], dtype=float)
        every, final, bypasses = smoothed(human, p)
        n = len(p)
        out[person] = {
            "n_flushes": int(n),
            "n_sessions": int(len(final)),
            "flush_p_median": float(np.median(p)),
            "flush_p_max": float(p.max()),
            "flush_share_ge_0.6": float((p >= 0.6).mean()),
            "flush_share_ge_0.6_ci95": wilson(int((p >= 0.6).sum()), n),
            "flush_share_ge_0.8": float((p >= 0.8).mean()),
            "flush_share_ge_0.8_ci95": wilson(int((p >= 0.8).sum()), n),
            "smoothed_max_any_flush": float(max(every)) if every else None,
            "smoothed_share_ge_60": float(np.mean(np.array(every) >= 60.0)) if every else None,
            "smoothed_final": [float(v) for v in final],
            "n_level_shift_bypass": int(bypasses),
        }
    return out


def autofill_report(model):
    """The stored-card scenarios, per flush and at the confirm flush."""
    out = {}
    for name, runs in AUTOFILL.items():
        if not runs:
            continue
        flush_p, confirm, session_final = [], [], []
        for rows in runs:
            p = proba(model, rows)
            flush_p.extend(p.tolist())
            confirm.append(float(p[-1]))
            # Smoothed the way /api/analyze does. Every generated flush
            # carries pointer motion, so the structural flag is True.
            seen, last = [], None
            for prob in p:
                score = round(100.0 * float(prob), 1)
                last = scorer.smooth_session_score(seen, score, True)
                seen.append(score)
            session_final.append(last)
        flush_p = np.array(flush_p)
        out[name] = {
            "n_runs": len(runs),
            "n_flushes": int(len(flush_p)),
            "flush_p_median": float(np.median(flush_p)),
            "flush_share_ge_0.6": float((flush_p >= 0.6).mean()),
            "flush_share_ge_0.8": float((flush_p >= 0.8).mean()),
            "confirm_flush_p_mean": float(np.mean(confirm)),
            "smoothed_final_median": float(np.median(session_final)),
            "smoothed_final_max": float(np.max(session_final)),
        }
    return out


def _fmt_person(r):
    if not r:
        return "n/a"
    return (f"p>=.6 {r['flush_share_ge_0.6']:.2f} smoothed "
            + "/".join(f"{v:.1f}" for v in r["smoothed_final"]))


tab, oof_tab, person_models = {}, {}, {}
for name in FACTORIES:
    t = time.perf_counter()
    res = {}
    m = fit(name, X_syn_tr, y_syn_tr)
    res["A_sim_to_sim"] = metrics(y_syn_te, proba(m, X_syn_te))
    if is_lab.any():
        p_lab = proba(m, real_X[is_lab])
        res["B_sim_to_lab"] = metrics(real_y[is_lab], p_lab)
        _ls = real_scen[is_lab]
        res["B_per_scenario_mean_p"] = {s: float(p_lab[_ls == s].mean()) for s in sorted(set(_ls.tolist()))}

    oof = np.zeros(len(real_y))
    for tr, te in folds:
        X = np.vstack([X_syn_tr, real_X[tr]])
        yy = np.concatenate([y_syn_tr, real_y[tr]])
        mm = fit(name, X, yy, blend_weights(len(y_syn_tr), len(tr)))
        oof[te] = proba(mm, real_X[te])
    oof_tab[name] = oof
    res["C_blend_grouped_oof"] = metrics(real_y, oof)
    res["C_per_scenario_mean_p"] = {s: float(oof[real_scen == s].mean()) for s in sorted(set(real_scen.tolist()))}

    loso = {}
    for s in sorted(set(real_scen.tolist())):
        keep = real_scen != s
        X = np.vstack([X_syn_tr, real_X[keep]])
        yy = np.concatenate([y_syn_tr, real_y[keep]])
        mm = fit(name, X, yy, blend_weights(len(y_syn_tr), int(keep.sum())))
        p = proba(mm, real_X[~keep])
        loso[s] = {
            "n": int((~keep).sum()), "label": int(real_y[~keep][0]),
            "mean_p": float(p.mean()), "ge_0.5": float((p >= 0.5).mean()),
            "ge_0.6": float((p >= 0.6).mean()), "ge_0.8": float((p >= 0.8).mean()),
        }
    res["D_leave_one_scenario_out"] = loso

    # R. Leave-one-PERSON-out. Training is the simulator plus every real row
    # that is not this person's -- the whole browser lab included -- so the
    # only thing the model has never seen is the person.
    person_out = {}
    for person in PERSONS:
        keep = real_group != person
        X = np.vstack([X_syn_tr, real_X[keep]])
        yy = np.concatenate([y_syn_tr, real_y[keep]])
        mm = fit(name, X, yy, blend_weights(len(y_syn_tr), int(keep.sum())))
        person_models[(name, person)] = mm
        idx = REC_IDX[person]
        person_out.update(person_report(dict(zip(idx.tolist(), proba(mm, real_X[idx]).tolist()))))
    res["R_leave_one_person_out"] = person_out

    # F. The autofill scenarios, scored by an R model: its training saw no
    # flush of the person whose pointer gaps the scenarios resample.
    if AUTOFILL and PERSONS:
        res["F_autofill"] = autofill_report(person_models[(name, PERSONS[0])])

    # Single-row latency, as served: one row, one thread.
    single = fit(name, X_syn_tr, y_syn_tr)
    for target in (single, getattr(single, "m", None)):
        if target is not None and hasattr(target, "n_jobs"):
            try:
                target.n_jobs = 1
            except (AttributeError, ValueError, TypeError):
                pass
    row = real_X[:1]
    for _ in range(30):
        single.predict_proba(row)
    ts = []
    for _ in range(200):
        a = time.perf_counter()
        single.predict_proba(row)
        ts.append((time.perf_counter() - a) * 1000)
    res["latency_ms_p50"] = float(np.median(ts))

    tab[name] = res
    c = res["C_blend_grouped_oof"]
    log(f"{name:26s} A.auc={res['A_sim_to_sim']['auc']:.3f}  C.auc={c['auc']:.3f} "
        f"brier={c['brier']:.3f} C.tpr@.8={c['tpr@0.8']:.2f} C.fpr@.6={c['fpr@0.6']:.2f}  "
        f"R[{PERSONS[0] if PERSONS else '-'}] {_fmt_person(res['R_leave_one_person_out'].get(PERSONS[0]) if PERSONS else None)}  "
        f"lat={res['latency_ms_p50']:.2f}ms  ({time.perf_counter() - t:.0f}s)")
RESULTS["tabular"] = tab
json.dump(RESULTS, open(OUT, "w"), indent=1)


# ---------------------------------------------------------------- temporal
def persona_label_at(persona, k):
    """Class of whoever is driving the session at its k-th flush (1-based)."""
    active = T._persona_at_step(persona, k - 1)
    return 1 if active in BOT_PERSONAS else 0


def padded(rows):
    return build_sequence([list(r) for r in rows]).squeeze(0).numpy()


def syn_prefix_set(idx, rng):
    """One random-length prefix per session plus the full sequence, padded the
    way the server pads them, labelled by who is acting at the last step.

    The prefix variant exists because it is what the SERVING path produces: a
    decision can be asked for after flush 1, and build_sequence() then
    left-pads with the current row. Training on full ten-step sequences only
    and serving padded prefixes is a train/serve mismatch, and the previous
    edition of this study is where it was first measured."""
    X, y = [], []
    for i in idx:
        for k in (int(rng.integers(1, SEQUENCE_LENGTH + 1)), SEQUENCE_LENGTH):
            X.append(padded(seqs[i, :k]))
            y.append(persona_label_at(personas[i], k) if personas[i].startswith("drift") else y_syn[i])
    return np.array(X), np.array(y)


def train_lstm(X, y, w=None, epochs=8, seed=0):
    torch.manual_seed(seed)
    model = BehaviorLSTM()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    Xt = torch.tensor(X, dtype=torch.float32)
    Yt = torch.tensor(y, dtype=torch.float32).unsqueeze(1)
    Wt = torch.ones_like(Yt) if w is None else torch.tensor(w, dtype=torch.float32).unsqueeze(1)
    for _ in range(epochs):
        model.train()
        perm = torch.randperm(len(Xt))
        for s in range(0, len(Xt), 256):
            b = perm[s:s + 256]
            opt.zero_grad()
            loss = (F.binary_cross_entropy(model(Xt[b]), Yt[b], reduction="none") * Wt[b]).sum() / Wt[b].sum()
            loss.backward()
            opt.step()
    model.eval()
    return model


def lstm_p(model, X):
    with torch.no_grad():
        return model(torch.tensor(X, dtype=torch.float32)).squeeze(1).numpy()


def hist_features(rows):
    """current row + mean of up to 4 previous rows + how many there were."""
    rows = np.asarray(rows)
    cur = rows[-1]
    prev = rows[-5:-1]
    mean_prev = prev.mean(axis=0) if len(prev) else cur
    return np.concatenate([cur, mean_prev, [len(prev) / 4.0]])


temporal = {}
rng = np.random.default_rng(5)
real_seq = np.array([padded(history(i)) for i in range(len(real_y))])
real_hist = np.array([hist_features(history(i, 5)) for i in range(len(real_y))])

X_pre_tr, y_pre_tr = syn_prefix_set(idx_tr, rng)
full_tr = np.array([seqs[i] for i in idx_tr])

cands = {}
m_full = train_lstm(full_tr, y_syn_tr)
cands["LSTM as formerly shipped (syn, full seq only)"] = lstm_p(m_full, real_seq)
m_pre = train_lstm(X_pre_tr, y_pre_tr)
cands["LSTM syn + padded prefixes (serving shape)"] = lstm_p(m_pre, real_seq)

oof = np.zeros(len(real_y))
for tr, te in folds:
    X = np.concatenate([X_pre_tr, real_seq[tr]])
    yy = np.concatenate([y_pre_tr, real_y[tr]])
    w = blend_weights(len(y_pre_tr), len(tr))
    oof[te] = lstm_p(train_lstm(X, yy, w), real_seq[te])
cands["LSTM syn + prefixes + real (OOF)"] = oof

_pairs = [(i, k) for i in idx_tr for k in (int(rng.integers(1, SEQUENCE_LENGTH + 1)), SEQUENCE_LENGTH)]
H_tr = np.array([hist_features(seqs[i, max(0, k - 5):k]) for i, k in _pairs])
H_y = np.array([persona_label_at(personas[i], k) if personas[i].startswith("drift") else y_syn[i] for i, k in _pairs])
for base in ("RF (shipped)", "HistGB"):
    oof = np.zeros(len(real_y))
    for tr, te in folds:
        X = np.vstack([H_tr, real_hist[tr]])
        yy = np.concatenate([H_y, real_y[tr]])
        oof[te] = proba(fit(base, X, yy, blend_weights(len(H_y), len(tr))), real_hist[te])
    cands[f"{base} + history feats (OOF)"] = oof


def former_blend(rf, ls):
    """The ensemble this project used to ship: 0.6 RF + 0.4 LSTM, escalated
    towards whichever one is higher when they disagree by 0.35 or more."""
    blend = 0.6 * rf + 0.4 * ls
    d = np.abs(rf - ls)
    return np.where(d >= 0.35, (1 - d) * blend + d * np.maximum(rf, ls), blend)


rf_oof = oof_tab["RF (shipped)"]
cands["RF OOF alone (shipped today)"] = rf_oof
cands["HistGB OOF alone"] = oof_tab["HistGB"]
cands["FORMER ensemble (RF + LSTM + escalation)"] = former_blend(
    rf_oof, cands["LSTM as formerly shipped (syn, full seq only)"]
)
cands["RF 0.6 + LSTM(syn+pref+real) 0.4"] = 0.6 * rf_oof + 0.4 * cands["LSTM syn + prefixes + real (OOF)"]

# The Isolation Forest blends. 0.5/0.2/0.3 is the weighting this project used
# to ship (RF / LSTM / IsolationForest); 0.9/0.1 asks whether even a token
# share of a one-class detector adds anything.
if "IsolationForest (1-class)" in oof_tab:
    if_oof = oof_tab["IsolationForest (1-class)"]
    cands["IsolationForest OOF alone"] = if_oof
    cands["FORMER 0.5 RF + 0.2 LSTM + 0.3 IsoForest"] = (
        0.5 * rf_oof + 0.2 * cands["LSTM as formerly shipped (syn, full seq only)"] + 0.3 * if_oof
    )
    cands["RF 0.9 + IsoForest 0.1"] = 0.9 * rf_oof + 0.1 * if_oof
    cands["max(RF, IsoForest)"] = np.maximum(rf_oof, if_oof)

temporal["E_candidates_on_real_rows"] = {}
for name, p in cands.items():
    mt = metrics(real_y, p)
    mt["per_scenario_mean_p"] = {s: float(p[real_scen == s].mean()) for s in sorted(set(real_scen.tolist()))}
    # Person-clean by construction: the folds are grouped by person, and the
    # synthetic-only candidates never saw a real row at all.
    mt["recorded_person"] = person_report({i: p[i] for i in range(len(p))})
    temporal["E_candidates_on_real_rows"][name] = mt
    r = mt["recorded_person"].get(PERSONS[0]) if PERSONS else None
    log(f"{name:48s} auc={mt['auc']:.3f} brier={mt['brier']:.3f} ece={mt['ece']:.3f} "
        f"tpr@.6={mt['tpr@0.6']:.2f} tpr@.8={mt['tpr@0.8']:.2f} fpr@.6={mt['fpr@0.6']:.2f}  "
        f"person {_fmt_person(r)}")

# G. Does history add anything the current flush does not already say?
# Synthetic drift_to_bot sessions: the switch happens at flush 6 (index 5).
drift = idx_te[personas[idx_te] == "drift_to_bot"]
pure_h = idx_te[np.isin(personas[idx_te], ["human", "human_rushed", "human_autofill"])]
rf_cur = fit("RF (shipped)", X_syn_tr, y_syn_tr)
hgb_hist = fit("HistGB", H_tr, H_y)
react = {}
for label, fn in {
    "RF current flush only": lambda rows: proba(rf_cur, rows[-1:]),
    "LSTM (syn, full seq)": lambda rows: lstm_p(m_full, padded(rows)[None]),
    "LSTM syn + prefixes": lambda rows: lstm_p(m_pre, padded(rows)[None]),
    "HistGB + history": lambda rows: proba(hgb_hist, hist_features(rows[-5:])[None]),
}.items():
    per_k = {}
    for k in (5, 6, 7, 10):
        pd_ = np.array([fn(seqs[i, :k])[0] for i in drift])
        ph = np.array([fn(seqs[i, :k])[0] for i in pure_h])
        per_k[f"k={k}"] = {
            "drift_to_bot_ge_0.5": float((pd_ >= 0.5).mean()),
            "human_ge_0.5": float((ph >= 0.5).mean()),
        }
    react[label] = per_k
    log(f"drift reaction {label:24s} " + "  ".join(
        f"{k}: bot {v['drift_to_bot_ge_0.5']:.2f}/hum {v['human_ge_0.5']:.2f}" for k, v in per_k.items()))
temporal["G_drift_reaction"] = {"n_drift_sessions": int(len(drift)), "n_human_sessions": int(len(pure_h)), "by_model": react}
RESULTS["temporal"] = temporal
json.dump(RESULTS, open(OUT, "w"), indent=1)


# ---------------------------------------------------------------- noise
# H. Which protocol-C differences are real? Resample GROUPS, not flushes:
# flushes within one run or one person are not independent, and a flush-level
# bootstrap would report a confidence interval several times too narrow.
groups = np.array(sorted(set(real_group.tolist())))
boot = np.random.default_rng(0)
draws = [np.concatenate([np.where(real_group == g)[0] for g in boot.choice(groups, size=len(groups))])
         for _ in range(2000)]
base = rf_oof
noise = {}
for name, p in list(oof_tab.items()) + [("FORMER 0.5/0.2/0.3 ensemble", cands.get("FORMER 0.5 RF + 0.2 LSTM + 0.3 IsoForest"))]:
    if p is None or name == "RF (shipped)":
        continue
    d = {"auc": [], "tpr@0.8": [], "fpr@0.6": [], "brier": []}
    for idx in draws:
        yy = real_y[idx]
        if len(set(yy.tolist())) < 2:
            continue
        d["auc"].append(roc_auc_score(yy, p[idx]) - roc_auc_score(yy, base[idx]))
        d["tpr@0.8"].append((p[idx][yy == 1] >= 0.8).mean() - (base[idx][yy == 1] >= 0.8).mean())
        d["fpr@0.6"].append((p[idx][yy == 0] >= 0.6).mean() - (base[idx][yy == 0] >= 0.6).mean())
        d["brier"].append(brier_score_loss(yy, p[idx]) - brier_score_loss(yy, base[idx]))
    noise[name] = {k: {"mean": float(np.mean(v)), "ci95": [float(x) for x in np.percentile(v, [2.5, 97.5])],
                       "separated_from_zero": bool(np.percentile(v, 2.5) > 0 or np.percentile(v, 97.5) < 0)}
                   for k, v in d.items()}
RESULTS["H_minus_RF_group_bootstrap"] = {
    "note": "Each candidate MINUS RandomForest on protocol C, 2000 resamples of the "
            f"{len(groups)} real groups. 'separated_from_zero' false means the difference is "
            "inside the noise at this sample size, whatever its sign.",
    "n_groups": int(len(groups)),
    "by_model": noise,
}
log("")
for name, v in noise.items():
    marks = " ".join(f"{k}={v[k]['mean']:+.3f}{'*' if v[k]['separated_from_zero'] else ' '}" for k in ("auc", "tpr@0.8", "fpr@0.6", "brier"))
    log(f"minus RF  {name:34s} {marks}")
log("(* = 95% group-bootstrap interval excludes zero; everything else is inside the noise)")

json.dump(RESULTS, open(OUT, "w"), indent=1)
log("\ndone ->", OUT)


# ---------------------------------------------------------------- verdict
# The comparison the decision is made on, in one table, written into the JSON
# as well as printed. No component is in or out of the served score because of
# taste; this is the evidence, and scorer.py's comment at the decision site
# quotes it.
def worst_human_loso(res):
    """Under leave-one-scenario-out, the highest share of an UNSEEN human
    scenario's flushes a model puts at or above the block line. This is the
    number that decided the previous edition of this study, and it is the one
    that matters most: every real customer is unseen by construction."""
    d = res.get("D_leave_one_scenario_out") or {}
    human = {s: v for s, v in d.items() if v["label"] == 0}
    if not human:
        return None, None
    worst = max(human, key=lambda s: human[s]["ge_0.8"])
    return worst, human[worst]["ge_0.8"]


summary = {}
log("\n" + "=" * 118)
log(f"{'model':<26}{'C.auc':>7}{'C.brier':>9}{'C.tpr@.8':>10}{'C.fpr@.6':>10}"
    f"{'D worst unseen human >=.8':>27}{'R p01 >=.6':>12}{'R smoothed':>12}{'ms':>7}")
log("-" * 118)
for name, res in tab.items():
    c = res["C_blend_grouped_oof"]
    scen, share = worst_human_loso(res)
    r = res["R_leave_one_person_out"].get(PERSONS[0]) if PERSONS else None
    summary[name] = {
        "C_auc": c["auc"], "C_brier": c["brier"], "C_tpr@0.8": c["tpr@0.8"], "C_fpr@0.6": c["fpr@0.6"],
        "D_worst_unseen_human_scenario": scen, "D_worst_unseen_human_ge_0.8": share,
        "R_person_flush_share_ge_0.6": (r or {}).get("flush_share_ge_0.6"),
        "R_person_smoothed_final": (r or {}).get("smoothed_final"),
        "R_person_level_shift_bypasses": (r or {}).get("n_level_shift_bypass"),
        "latency_ms_p50": res["latency_ms_p50"],
        "F_autofill_smoothed_final_median": {
            k: v["smoothed_final_median"] for k, v in (res.get("F_autofill") or {}).items()
        },
    }
    log(f"{name:<26}{c['auc']:>7.3f}{c['brier']:>9.3f}{c['tpr@0.8']:>10.2f}{c['fpr@0.6']:>10.2f}"
        f"{(scen or '-') + ' ' + ('%.2f' % share if share is not None else '-'):>27}"
        f"{(r or {}).get('flush_share_ge_0.6', float('nan')):>12.2f}"
        f"{'/'.join('%.1f' % v for v in (r or {}).get('smoothed_final', [])) or '-':>12}"
        f"{res['latency_ms_p50']:>7.2f}")
log("=" * 118)
RESULTS["verdict_table"] = summary

if AUTOFILL:
    log("\nProtocol F -- stored-card checkout, scored by a model that never saw person "
        f"{PERSONS[0] if PERSONS else '-'} (median smoothed session score):")
    scen_names = sorted({k for res in tab.values() for k in (res.get("F_autofill") or {})})
    log(f"{'model':<26}" + "".join(f"{s[:24]:>26}" for s in scen_names))
    for name, res in tab.items():
        f = res.get("F_autofill") or {}
        log(f"{name:<26}" + "".join(f"{f[s]['smoothed_final_median']:>26.1f}" if s in f else f"{'-':>26}" for s in scen_names))

RESULTS["reading_guide"] = {
    "what_each_column_is": {
        "C": "5-fold grouped CV over the real rows, groups = person or lab run. Mostly "
             "asks whether a model recognises other runs of scripts it has seen.",
        "D worst unseen human >=.8": "leave-one-scenario-out: the human scenario a model "
             "treats worst when that scenario was never in its training set. Every real "
             "customer is unseen by construction, so this is the column a payment product "
             "is chosen on.",
        "R": "the recorded person's own flushes, scored by a model trained on the simulator "
             "and the whole browser lab but not on that person. 'smoothed' is what "
             "/api/decision reads and the payment turns on.",
        "F": "a stored card filled by the browser -- a checkout with no keydown channel at "
             "all -- scored by the same person-blind model.",
    },
    "what_these_numbers_cannot_say": [
        "There is ONE recorded person. Every R and F number is an anecdote about one person "
        "on one machine with one 60 Hz monitor and one hand. It is not a false-positive rate, "
        "and the Wilson intervals beside the flush shares are intervals on that one sitting, "
        "whose flushes are not independent.",
        "The browser lab is scripted. Its H1/H2 'human' scenarios are generated motion driven "
        "through CDP, not a person's pointer clock -- which is exactly how the frame-clock bug "
        "survived 234 lab rows undetected.",
        "No calibration. Every probability here is a vote share or a margin, not P(fraud|behaviour); "
        "Brier and ECE are reported against a training class balance nobody claims is a base rate.",
    ],
    "what_would_settle_the_open_questions": [
        "Whether a component earns its place: 20-30 recorded people, several sessions each, held "
        "out BY PERSON. Twelve features and a handful of groups cannot separate a 0.002 AUC "
        "difference from noise -- the bootstrap section says which of today's differences already "
        "cannot be.",
        "Whether the frame-clock mix is right: recordings from 120 Hz and 144 Hz monitors, from a "
        "trackpad, from touch, and from a throttled tab. The simulator's 0.70/0.20/0.10 refresh-rate "
        "mix is an assumption, not a measurement -- one machine was ever recorded.",
        "Whether the conformal guard protects anyone: its calibration sample is scripted lab humans. "
        "It needs held-out REAL human sessions, which needs more than one person.",
    ],
}
json.dump(RESULTS, open(OUT, "w"), indent=1)
log("\ndone ->", OUT)
