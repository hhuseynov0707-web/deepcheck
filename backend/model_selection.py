"""Model-selection study: why the served score is a RandomForest alone.

Run from backend/ after training (about 6-8 minutes on a laptop):
    python model_selection.py
    python model_selection.py --json results.json

LightGBM and XGBoost are compared when installed (`pip install lightgbm
xgboost`); they are not serving dependencies and are skipped otherwise.

Same data, same splits, several model families. Four protocols:
  A  sim -> sim      in-distribution sanity check (synthetic test split)
  B  sim -> lab      train on the simulator only, score the browser rows (234
                     in the capture the scorer.py numbers were measured on)
  C  blend, grouped  synthetic + lab, 5-fold StratifiedGroupKFold by RUN
                     (by person, for recordings that name one)
  D  leave-one-scenario-out: the scenario being scored was never trained on
Plus temporal models (LSTM variants vs history-aggregate trees), single-row
latency, and a run-level bootstrap of the LightGBM-vs-RandomForest gap.

Read protocol D before the others. The lab rows are scripted, so C mostly asks
whether a model recognises other runs of scripts it has already seen. D is the
only protocol that asks what happens to behaviour the model has NOT seen --
which is what every real customer is.
"""
import argparse
import json
import os
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
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
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
RESULTS = {}


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
lab = T.load_real_rows(T.REAL_TELEMETRY_PATH)
if lab is None:
    sys.exit(f"no usable real rows in {T.REAL_TELEMETRY_PATH}")
lab_X, lab_y = lab["X"], lab["y"]
# Fold groups are the holdout's unit (person, else run), so no protocol puts
# one person's sittings on both sides.
lab_run = np.array(lab["group"])
lab_scen = np.array(lab["scenario"])
log(f"real rows: {len(lab_y)} ({int(lab['from_raw'].sum())} re-extracted from raw)")

# Each row's own session up to and including it, in flush order.
_by_session = {}
for _i, (_session, _order) in enumerate(zip(lab["session"], lab["order"])):
    _by_session.setdefault(_session, []).append((_order, _i))
lab_prefix = {}
for _rows in _by_session.values():
    _ordered = [i for _, i in sorted(_rows)]
    for _rank, _i in enumerate(_ordered):
        lab_prefix[_i] = _ordered[: _rank + 1]


def lab_history(i, k=SEQUENCE_LENGTH):
    """Rows of row i's session up to and including i (oldest first)."""
    return lab_X[lab_prefix[i][-k:]]


# ---------------------------------------------------------------- metrics
def ece(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    return float(sum((idx == b).mean() * abs(p[idx == b].mean() - y[idx == b].mean()) for b in range(bins) if (idx == b).any()))


def metrics(y, p):
    y, p = np.asarray(y), np.asarray(p, dtype=float)
    h, b = p[y == 0], p[y == 1]
    out = {
        "n": int(len(y)),
        "auc": float(roc_auc_score(y, p)) if len(set(y)) == 2 else None,
        "brier": float(brier_score_loss(y, p)),
        "ece": ece(y, p),
    }
    for thr in (0.5, 0.6, 0.8):
        out[f"fpr@{thr}"] = float((h >= thr).mean()) if len(h) else None
        out[f"tpr@{thr}"] = float((b >= thr).mean()) if len(b) else None
    return out


# ---------------------------------------------------------------- tabular models
FACTORIES = {
    "RF (shipped)": lambda: RandomForestClassifier(n_estimators=200, max_depth=12, max_features=4, min_samples_leaf=5, random_state=42, n_jobs=-1),
    "ExtraTrees": lambda: ExtraTreesClassifier(n_estimators=300, max_features=4, min_samples_leaf=5, random_state=42, n_jobs=-1),
    "HistGB": lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=20, l2_regularization=1.0, random_state=42),
    "LightGBM": lambda: LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=42, verbose=-1),
    "XGBoost": lambda: XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=os.cpu_count(), eval_metric="logloss"),
    "LogReg": lambda: Pipeline([("s", StandardScaler()), ("m", LogisticRegression(C=1.0, max_iter=5000))]),
    "MLP": lambda: Pipeline([("s", StandardScaler()), ("m", MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300, early_stopping=True, random_state=42))]),
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
    return model.predict_proba(X)[:, 1]


def blend_weights(n_syn, n_lab):
    # Lab rows count, in aggregate, as much as the synthetic rows -- the same
    # ratio train_model.py's REAL_TELEMETRY_WEIGHT produces at full size.
    return np.concatenate([np.ones(n_syn), np.full(n_lab, n_syn / max(n_lab, 1))])


X_syn_tr, y_syn_tr = seqs[idx_tr, -1], y_syn[idx_tr]
X_syn_te, y_syn_te = seqs[idx_te, -1], y_syn[idx_te]
sgkf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0)
folds = list(sgkf.split(lab_X, lab_y, groups=lab_run))

tab = {}
oof_tab = {}
for name in FACTORIES:
    t = time.perf_counter()
    res = {}
    m = fit(name, X_syn_tr, y_syn_tr)
    res["A_sim_to_sim"] = metrics(y_syn_te, proba(m, X_syn_te))
    p_lab = proba(m, lab_X)
    res["B_sim_to_lab"] = metrics(lab_y, p_lab)
    res["B_per_scenario_mean_p"] = {s: float(p_lab[lab_scen == s].mean()) for s in sorted(set(lab_scen))}

    oof = np.zeros(len(lab_y))
    for tr, te in folds:
        X = np.vstack([X_syn_tr, lab_X[tr]])
        yy = np.concatenate([y_syn_tr, lab_y[tr]])
        mm = fit(name, X, yy, blend_weights(len(y_syn_tr), len(tr)))
        oof[te] = proba(mm, lab_X[te])
    oof_tab[name] = oof
    res["C_blend_grouped_oof"] = metrics(lab_y, oof)
    res["C_per_scenario_mean_p"] = {s: float(oof[lab_scen == s].mean()) for s in sorted(set(lab_scen))}

    loso = {}
    for s in sorted(set(lab_scen)):
        keep = lab_scen != s
        X = np.vstack([X_syn_tr, lab_X[keep]])
        yy = np.concatenate([y_syn_tr, lab_y[keep]])
        mm = fit(name, X, yy, blend_weights(len(y_syn_tr), int(keep.sum())))
        p = proba(mm, lab_X[~keep])
        loso[s] = {"mean_p": float(p.mean()), ">=0.5": float((p >= 0.5).mean()), ">=0.6": float((p >= 0.6).mean()), ">=0.8": float((p >= 0.8).mean())}
    res["D_leave_one_scenario_out"] = loso

    # single-row latency, as served (one row, one thread)
    for attr in ("n_jobs",):
        if hasattr(mm, attr):
            setattr(mm, attr, 1)
    if XGBClassifier is not None and isinstance(mm, XGBClassifier):
        mm.set_params(n_jobs=1)
    row = lab_X[:1]
    for _ in range(30):
        mm.predict_proba(row)
    ts = []
    for _ in range(200):
        a = time.perf_counter()
        mm.predict_proba(row)
        ts.append((time.perf_counter() - a) * 1000)
    res["latency_ms_p50"] = float(np.median(ts))
    tab[name] = res
    c = res["C_blend_grouped_oof"]
    log(f"{name:14s} A.auc={res['A_sim_to_sim']['auc']:.3f}  B.auc={res['B_sim_to_lab']['auc']:.3f}  "
        f"C.auc={c['auc']:.3f} C.brier={c['brier']:.3f} C.tpr@.8={c['tpr@0.8']:.2f} C.fpr@.6={c['fpr@0.6']:.2f}  "
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
    way the server pads them, labelled by who is acting at the last step."""
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
lab_seq = np.array([padded(lab_history(i)) for i in range(len(lab_y))])
lab_hist = np.array([hist_features(lab_history(i, 5)) for i in range(len(lab_y))])

# Temporal candidates on the lab rows, every flush scored with its own history.
X_pre_tr, y_pre_tr = syn_prefix_set(idx_tr, rng)
full_tr = np.array([seqs[i] for i in idx_tr])

cands = {}
m = train_lstm(full_tr, y_syn_tr)
cands["LSTM as formerly shipped (syn, full seq only)"] = lstm_p(m, lab_seq)
m_pre = train_lstm(X_pre_tr, y_pre_tr)
cands["LSTM syn + padded prefixes"] = lstm_p(m_pre, lab_seq)

oof = np.zeros(len(lab_y))
for tr, te in folds:
    X = np.concatenate([X_pre_tr, lab_seq[tr]])
    yy = np.concatenate([y_pre_tr, lab_y[tr]])
    w = blend_weights(len(y_pre_tr), len(tr))
    oof[te] = lstm_p(train_lstm(X, yy, w), lab_seq[te])
cands["LSTM syn + prefixes + lab (OOF)"] = oof

_pairs = [(i, k) for i in idx_tr for k in (int(rng.integers(1, SEQUENCE_LENGTH + 1)), SEQUENCE_LENGTH)]
H_tr = np.array([hist_features(seqs[i, max(0, k - 5):k]) for i, k in _pairs])
H_y = np.array([persona_label_at(personas[i], k) if personas[i].startswith("drift") else y_syn[i] for i, k in _pairs])
for base in ("RF (shipped)", "HistGB"):
    oof = np.zeros(len(lab_y))
    for tr, te in folds:
        X = np.vstack([H_tr, lab_hist[tr]])
        yy = np.concatenate([H_y, lab_y[tr]])
        oof[te] = proba(fit(base, X, yy, blend_weights(len(H_y), len(tr))), lab_hist[te])
    cands[f"{base} + history feats (OOF)"] = oof

# The shipped blend, reconstructed on OOF rows: RF(OOF) with the synthetic-only LSTM.
def shipped_blend(rf, ls):
    blend = 0.6 * rf + 0.4 * ls
    d = np.abs(rf - ls)
    return np.where(d >= 0.35, (1 - d) * blend + d * np.maximum(rf, ls), blend)

cands["FORMER ensemble (RF OOF + LSTM + escalation)"] = shipped_blend(oof_tab["RF (shipped)"], cands["LSTM as formerly shipped (syn, full seq only)"])
cands["RF OOF alone"] = oof_tab["RF (shipped)"]
cands["HistGB OOF alone"] = oof_tab["HistGB"]
cands["RF OOF 0.6 + LSTM(syn+pref+lab) 0.4"] = 0.6 * oof_tab["RF (shipped)"] + 0.4 * cands["LSTM syn + prefixes + lab (OOF)"]

temporal["E_lab_candidates"] = {}
for name, p in cands.items():
    mt = metrics(lab_y, p)
    mt["per_scenario_mean_p"] = {s: float(p[lab_scen == s].mean()) for s in sorted(set(lab_scen))}
    temporal["E_lab_candidates"][name] = mt
    log(f"{name:55s} auc={mt['auc']:.3f} brier={mt['brier']:.3f} ece={mt['ece']:.3f} "
        f"tpr@.6={mt['tpr@0.6']:.2f} tpr@.8={mt['tpr@0.8']:.2f} fpr@.6={mt['fpr@0.6']:.2f}")

# G. Does history add anything the current flush does not already say?
# Synthetic drift_to_bot sessions: the switch happens at flush 6 (index 5).
drift = idx_te[personas[idx_te] == "drift_to_bot"]
pure_h = idx_te[personas[idx_te] == "human"]
rf_cur = fit("RF (shipped)", X_syn_tr, y_syn_tr)
hgb_hist = fit("HistGB", H_tr, H_y)
react = {}
for label, fn in {
    "RF current flush only": lambda rows: proba(rf_cur, rows[-1:]),
    "LSTM (syn, full seq)": lambda rows: lstm_p(m, padded(rows)[None]),
    "LSTM syn + prefixes": lambda rows: lstm_p(m_pre, padded(rows)[None]),
    "HistGB + history": lambda rows: proba(hgb_hist, hist_features(rows[-5:])[None]),
}.items():
    per_k = {}
    for k in (5, 6, 7, 10):
        pd_ = np.array([fn(seqs[i, :k])[0] for i in drift])
        ph = np.array([fn(seqs[i, :k])[0] for i in pure_h])
        per_k[f"k={k}"] = {"drift_to_bot_p>=0.5": float((pd_ >= 0.5).mean()), "pure_human_p>=0.5": float((ph >= 0.5).mean())}
    react[label] = per_k
    log(f"drift reaction {label:22s} " + "  ".join(f"k{k}: bot {v['drift_to_bot_p>=0.5']:.2f}/hum {v['pure_human_p>=0.5']:.2f}" for k, v in
                                                    ((kk.split('=')[1], vv) for kk, vv in per_k.items())))
temporal["G_drift_reaction"] = react
RESULTS["temporal"] = temporal

# H. Is the boosting advantage in protocol C more than noise? Resample RUNS,
# not flushes: flushes within a run are not independent.
if "LightGBM" in oof_tab:
    runs = np.array(sorted(set(lab_run)))
    boot = np.random.default_rng(0)
    a, b = oof_tab["LightGBM"], oof_tab["RF (shipped)"]
    diffs = {"auc": [], "tpr@0.8": [], "brier": []}
    for _ in range(2000):
        idx = np.concatenate([np.where(lab_run == r)[0] for r in boot.choice(runs, size=len(runs))])
        yy = lab_y[idx]
        if len(set(yy)) < 2:
            continue
        diffs["auc"].append(roc_auc_score(yy, a[idx]) - roc_auc_score(yy, b[idx]))
        diffs["tpr@0.8"].append((a[idx][yy == 1] >= 0.8).mean() - (b[idx][yy == 1] >= 0.8).mean())
        diffs["brier"].append(brier_score_loss(yy, a[idx]) - brier_score_loss(yy, b[idx]))
    RESULTS["H_lightgbm_minus_rf_bootstrap"] = {
        k: {"mean": float(np.mean(v)), "ci95": [float(x) for x in np.percentile(v, [2.5, 97.5])]}
        for k, v in diffs.items()
    }
    for k, v in RESULTS["H_lightgbm_minus_rf_bootstrap"].items():
        log(f"LightGBM - RF  {k:8s} mean={v['mean']:+.3f}  95% CI [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]")

json.dump(RESULTS, open(OUT, "w"), indent=1)
log("done ->", OUT)
