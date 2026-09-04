"""Evaluation benchmark: accessibility slices, attack detection, latency.

Reports what a payment product actually has to answer for, rather than one
global accuracy number:

  * False-positive rate per *interaction style*. A detector that is 98%
    accurate overall but blocks keyboard-only users is not deployable, and a
    global average hides that completely. This project has already produced
    exactly that failure once -- a 5-keystroke typing session scored 89.2 and
    would have been blocked.
  * Detection per attack family, so a strong naive-bot number cannot stand in
    for evasive-bot performance.
  * Every rate with a 95% Wilson confidence interval and its sample size,
    because "FPR < 1%" from 30 sessions is not a measurement. Wilson rather
    than the normal approximation: at 0 observed failures the normal interval
    collapses to [0, 0], which would claim a certainty the sample cannot support.

Run (after training):
    python benchmark.py
    python benchmark.py --rows 4000
"""

import argparse
import json
import math
import statistics
import time
from pathlib import Path

import numpy as np

import scorer
import train_model as T
from lstm_model import FEATURE_NAMES

REAL_TELEMETRY_PATH = Path(__file__).resolve().parent.parent / "lab" / "real_telemetry.json"

# The server blocks at 80 and steps up at 60 (see main.py). For a *false
# positive* the question is whether a legitimate user was blocked outright.
BLOCK_THRESHOLD = 80.0
STEP_UP_THRESHOLD = 60.0


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because these samples are small and
    the interesting counts are near zero, where the normal interval is simply
    wrong (it returns a zero-width interval at 0 successes).
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def fmt_rate(successes: int, n: int) -> str:
    if n == 0:
        return "n=0"
    lo, hi = wilson_interval(successes, n)
    return f"{successes / n:6.2%}  [{lo:5.2%}, {hi:5.2%}]  n={n}"


# ---------------------------------------------------------------------------
# Accessibility slices: legitimate users whose interaction style is unusual.
# ---------------------------------------------------------------------------


def slice_keyboard_only(rng_seed: int) -> dict:
    """Tab navigation, no pointer at all."""
    T.set_seed(rng_seed)
    t = 1_700_000_000_000
    mouse, clicks, keys, t, _, _ = T._form_fill(
        t, 200.0, 200.0,
        n_fields=int(T.rng.integers(2, 5)),
        points_per_reach=(0, 0),
        keys_per_field=(6, 18),
        typing_sigma=0.5,
        keyboard_only=True,
    )
    return _payload(mouse, clicks, keys)


def slice_slow_typist(rng_seed: int) -> dict:
    """Deliberate, slow typing with long pauses between fields."""
    T.set_seed(rng_seed)
    t = 1_700_000_000_000
    mouse, clicks, keys, t, _, _ = T._form_fill(
        t, 200.0, 200.0,
        n_fields=int(T.rng.integers(2, 4)),
        points_per_reach=(8, 16),
        keys_per_field=(4, 10),
        typing_sigma=0.7,
        speed=0.55,
        hand_move_ms=(600, 1400),
    )
    return _payload(mouse, clicks, keys)


def slice_low_pointer(rng_seed: int) -> dict:
    """Mostly typing, only a couple of pointer events."""
    T.set_seed(rng_seed)
    t = 1_700_000_000_000
    mouse, clicks, keys, t, _, _ = T._form_fill(
        t, 200.0, 200.0,
        n_fields=2,
        points_per_reach=(1, 3),
        keys_per_field=(6, 16),
        typing_sigma=0.5,
    )
    return _payload(mouse, clicks, keys)


def slice_rapid_legit(rng_seed: int) -> dict:
    """A fast, experienced user -- efficient but genuinely human."""
    T.set_seed(rng_seed)
    t = 1_700_000_000_000
    mouse, clicks, keys, t, _, _ = T._form_fill(
        t, 200.0, 200.0,
        n_fields=int(T.rng.integers(2, 4)),
        points_per_reach=(6, 11),
        keys_per_field=(5, 12),
        typing_sigma=0.35,
        speed=1.8,
        tremor=0.8,
        hand_move_ms=(120, 320),
    )
    return _payload(mouse, clicks, keys)


def slice_sparse_first_flush(rng_seed: int) -> dict:
    """The opening two seconds of a session: very little has happened yet."""
    T.set_seed(rng_seed)
    raw = T._simulate_session("human_sparse", 1_700_000_000_000)
    return raw


def _payload(mouse, clicks, keys) -> dict:
    times = sorted([m["t"] for m in mouse] + [c["t"] for c in clicks] + [k["t"] for k in keys])
    return {
        "mouse_trajectory": mouse,
        "click_timing": clicks,
        "scroll_events": [],
        "key_events": keys,
        "focus_changes": [],
        "hesitation_intervals": [
            b - a for a, b in zip(times, times[1:]) if (b - a) >= T.HESITATION_THRESHOLD_MS
        ],
    }


HUMAN_SLICES = {
    "keyboard_only": slice_keyboard_only,
    "slow_typist": slice_slow_typist,
    "low_pointer": slice_low_pointer,
    "rapid_legitimate": slice_rapid_legit,
    "sparse_first_flush": slice_sparse_first_flush,
    "typical_human": lambda seed: (T.set_seed(seed), T._simulate_session("human", 1_700_000_000_000))[1],
}

ATTACK_PERSONAS = ["bot", "bot_sophisticated", "bot_evasive"]


def evaluate(n_per_group: int) -> dict:
    results = {"human_slices": {}, "attacks": {}, "latency_ms": [], "real": {}}

    for name, generator in HUMAN_SLICES.items():
        blocked = stepped_up = 0
        scores = []
        for i in range(n_per_group):
            raw = generator(10_000 + i)
            started = time.perf_counter()
            out = scorer.compute_risk(raw)
            results["latency_ms"].append((time.perf_counter() - started) * 1000)
            scores.append(out["risk_score"])
            if out["risk_score"] >= BLOCK_THRESHOLD:
                blocked += 1
            elif out["risk_score"] >= STEP_UP_THRESHOLD:
                stepped_up += 1
        results["human_slices"][name] = {
            "n": n_per_group,
            "blocked": blocked,
            "stepped_up": stepped_up,
            "median_risk": round(statistics.median(scores), 1),
        }

    for persona in ATTACK_PERSONAS:
        detected = 0
        scores = []
        for i in range(n_per_group):
            T.set_seed(20_000 + i)
            raw = T._simulate_session(persona, 1_700_000_000_000)
            out = scorer.compute_risk(raw)
            scores.append(out["risk_score"])
            if out["risk_score"] >= STEP_UP_THRESHOLD:
                detected += 1
        results["attacks"][persona] = {
            "n": n_per_group,
            "detected": detected,
            "median_risk": round(statistics.median(scores), 1),
        }

    if REAL_TELEMETRY_PATH.exists():
        results["real"] = evaluate_real()

    return results


def evaluate_real() -> dict:
    """Scores the HELD-OUT real-browser flushes through the full ensemble.

    Only the runs train_model.py held back are scored. Scoring every captured
    sample would include the rows the forest was fitted on and report a number
    inflated by its own training data -- and because each browser session
    contributes several correlated flushes, the split is by run, not by flush.
    """
    payload = json.loads(REAL_TELEMETRY_PATH.read_text())
    by_scenario: dict[str, dict] = {}
    bundle = scorer.get_bundle()

    split = T.load_real_telemetry()
    if split is None:
        return {}
    # Recompute the same holdout run ids the trainer used, so the two agree.
    runs = sorted({s.get("run_id", s["scenario"]) for s in payload.get("samples", [])})
    holdout_rng = np.random.default_rng(1234)
    holdout_rng.shuffle(runs)
    holdout = set(runs[: max(1, int(len(runs) * T.REAL_HOLDOUT_FRACTION))])

    for sample in payload.get("samples", []):
        if sample.get("run_id", sample["scenario"]) not in holdout:
            continue
        vector = np.array([[sample["features"][name] for name in FEATURE_NAMES]])
        scaled = bundle.scaler.transform(vector)
        rf_p = float(bundle.rf.predict_proba(scaled)[0][1])
        iso = float(np.clip(0.5 - bundle.iso_forest.decision_function(scaled)[0], 0, 1))
        # No raw events here, so the LSTM has no window to read; give its share
        # to the RF exactly as scorer.compute_risk does at temporal_support 0.
        risk = 100 * float(
            np.clip(
                (scorer.RF_WEIGHT + scorer.LSTM_WEIGHT) * rf_p + scorer.ISO_WEIGHT * iso,
                0, 1,
            )
        )
        entry = by_scenario.setdefault(
            sample["scenario"], {"n": 0, "wrong": 0, "label": sample["label"], "scores": []}
        )
        entry["n"] += 1
        entry["scores"].append(risk)
        if sample["label"] == 0 and risk >= BLOCK_THRESHOLD:
            entry["wrong"] += 1
        if sample["label"] == 1 and risk < STEP_UP_THRESHOLD:
            entry["wrong"] += 1
    for entry in by_scenario.values():
        entry["median_risk"] = round(statistics.median(entry["scores"]), 1)
        del entry["scores"]
    return by_scenario


def main():
    parser = argparse.ArgumentParser(description="DeepCheck evaluation benchmark")
    parser.add_argument("--rows", type=int, default=300, help="samples per group")
    parser.add_argument("--out", default="benchmark_report.json")
    args = parser.parse_args()

    results = evaluate(args.rows)

    print("=" * 78)
    print("LEGITIMATE USERS -- blocked outright (false positives)")
    print("=" * 78)
    print(f"{'slice':22s} {'blocked rate  [95% CI]':38s} {'step-up':>9s} {'median risk':>12s}")
    for name, r in results["human_slices"].items():
        print(
            f"{name:22s} {fmt_rate(r['blocked'], r['n']):38s} "
            f"{r['stepped_up']:9d} {r['median_risk']:12.1f}"
        )

    print("\n" + "=" * 78)
    print("ATTACKS -- detected (risk >= step-up threshold)")
    print("=" * 78)
    for name, r in results["attacks"].items():
        print(f"{name:22s} {fmt_rate(r['detected'], r['n']):38s} median risk {r['median_risk']:6.1f}")

    if results["real"]:
        print("\n" + "=" * 78)
        print("REAL BROWSER TELEMETRY (lab/capture.py)")
        print("=" * 78)
        for name, r in sorted(results["real"].items()):
            kind = "human" if r["label"] == 0 else "attack"
            print(
                f"{name:22s} {kind:7s} misclassified {fmt_rate(r['wrong'], r['n']):38s} "
                f"median risk {r['median_risk']:6.1f}"
            )

    latency = results["latency_ms"]
    latency.sort()
    print("\n" + "=" * 78)
    print("MODEL SCORING LATENCY (feature extraction + ensemble + SHAP)")
    print("=" * 78)
    print(f"  p50 {latency[len(latency) // 2]:6.1f} ms")
    print(f"  p95 {latency[int(len(latency) * 0.95)]:6.1f} ms")
    print(f"  p99 {latency[int(len(latency) * 0.99)]:6.1f} ms")
    print("  (model scoring only -- excludes database write and network transit)")

    results["latency_summary"] = {
        "p50": round(latency[len(latency) // 2], 1),
        "p95": round(latency[int(len(latency) * 0.95)], 1),
        "p99": round(latency[int(len(latency) * 0.99)], 1),
    }
    del results["latency_ms"]
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nJSON: {args.out}")


if __name__ == "__main__":
    main()
