"""Evaluation benchmark: interaction-style slices, attack families, latency.

Reports what a payment product actually has to answer for, rather than one
global accuracy number:

  * False-positive rate per *interaction style*. A detector that is 98%
    accurate overall but blocks keyboard-only users is not deployable, and a
    global average hides that completely. This project produced exactly that
    failure once: a five-keystroke typing session scored 89.2 and would have
    been blocked outright.
  * Detection per attack family, so a strong naive-bot number cannot stand in
    for evasive-bot performance.
  * Every rate with a 95% Wilson confidence interval and its sample size,
    because "false positives under 1%" from thirty sessions is not a
    measurement.

Run (after training):
    python benchmark.py
    python benchmark.py --n 400
"""

import argparse
import json
import math
import os
import random
import statistics
import sys
import time

import numpy as np

import scorer
import train_model as T
from lstm_model import FEATURE_NAMES

# The server blocks at 80 and steps up at 60 (see main.py's ACTION_LADDER).
# For a *false positive* the question that matters is whether a legitimate
# user was blocked outright; being asked to verify is an inconvenience, being
# refused is a lost sale.
BLOCK_THRESHOLD = 80.0
STEP_UP_THRESHOLD = 60.0

HESITATION_MS = 400


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because these samples are small and
    the interesting counts sit near zero, where the normal interval is simply
    wrong: at zero observed failures it collapses to [0, 0], claiming a
    certainty the sample cannot support.
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def fmt_rate(successes: int, n: int) -> str:
    lo, hi = wilson_interval(successes, n)
    return f"{100 * successes / n:5.1f}%  [{100 * lo:4.1f}, {100 * hi:5.1f}]  n={n}"


# --------------------------------------------------------------------------
# Legitimate interaction styles.
#
# Deliberately self-contained rather than reaching into train_model's persona
# internals: those exist to build a training distribution, and a benchmark that
# shares their private helpers silently changes meaning whenever they are
# retuned. Here the shapes are written out, so what each slice claims to be is
# readable in one place.
# --------------------------------------------------------------------------


def _payload(mouse, clicks, keys, scrolls=(), focus=()):
    times = sorted(
        [m["t"] for m in mouse] + [c["t"] for c in clicks] + [k["t"] for k in keys]
        + [s["t"] for s in scrolls]
    )
    return {
        "mouse_trajectory": list(mouse),
        "click_timing": list(clicks),
        "scroll_events": list(scrolls),
        "key_events": list(keys),
        "focus_changes": list(focus),
        "hesitation_intervals": [
            b - a for a, b in zip(times, times[1:]) if (b - a) >= HESITATION_MS
        ],
    }


def _form_fill(r, *, fields, points_per_reach, keys_per_field, typing_median,
               typing_sigma, speed=1.0, tremor=1.4, hand_move_ms=(250, 900)):
    """One person filling `fields` form fields, as raw events."""
    t = 1_700_000_000_000.0
    x, y = 260.0, 200.0
    mouse, clicks, keys = [], [], []

    for _ in range(fields):
        target = (r.uniform(430, 690), r.uniform(150, 330))
        steps = r.randint(*points_per_reach) if points_per_reach[1] > 0 else 0
        for i in range(1, steps + 1):
            u = i / max(steps, 1)
            s = 10 * u**3 - 15 * u**4 + 6 * u**5          # minimum-jerk profile
            t += r.uniform(9, 18) / speed
            mouse.append({
                "x": x + (target[0] - x) * s + r.normalvariate(0, tremor),
                "y": y + (target[1] - y) * s + r.normalvariate(0, tremor),
                "t": int(t),
            })
        if steps:
            x, y = target
            t += r.lognormvariate(math.log(380), 0.5) / speed
            clicks.append({"x": x, "y": y, "t": int(t)})

        for _ in range(r.randint(*keys_per_field)):
            t += r.lognormvariate(math.log(typing_median), typing_sigma) / speed
            keys.append({"t": int(t)})

        t += r.uniform(*hand_move_ms)

    return mouse, clicks, keys


def slice_keyboard_only(seed):
    """Tab navigation, no pointer at all. The group most at risk of being
    wrongly blocked, and the one the model has least evidence about."""
    r = random.Random(seed)
    return _payload(*_form_fill(r, fields=r.randint(2, 4), points_per_reach=(0, 0),
                                keys_per_field=(6, 18), typing_median=180, typing_sigma=0.5))


def slice_slow_typist(seed):
    """Deliberate, slow typing with long pauses between fields."""
    r = random.Random(seed)
    return _payload(*_form_fill(r, fields=r.randint(2, 3), points_per_reach=(8, 16),
                                keys_per_field=(4, 10), typing_median=340, typing_sigma=0.7,
                                speed=0.55, hand_move_ms=(600, 1400)))


def slice_low_pointer(seed):
    """Mostly typing, only a couple of pointer events -- the exact shape that
    once scored 89.2."""
    r = random.Random(seed)
    return _payload(*_form_fill(r, fields=2, points_per_reach=(1, 3),
                                keys_per_field=(6, 16), typing_median=180, typing_sigma=0.5))


def slice_rapid_legitimate(seed):
    """A fast, experienced user: efficient but genuinely human."""
    r = random.Random(seed)
    return _payload(*_form_fill(r, fields=r.randint(2, 3), points_per_reach=(6, 11),
                                keys_per_field=(5, 12), typing_median=140, typing_sigma=0.35,
                                speed=1.8, tremor=0.8, hand_move_ms=(120, 320)))


def slice_typical_human(seed):
    """Pointer, typing and a scroll burst -- the ordinary case."""
    r = random.Random(seed)
    mouse, clicks, keys = _form_fill(r, fields=r.randint(3, 4), points_per_reach=(10, 20),
                                     keys_per_field=(6, 16), typing_median=175, typing_sigma=0.5)
    scrolls, t, sy = [], (mouse[-1]["t"] if mouse else 1_700_000_000_000) + 400, 0.0
    for _ in range(r.randint(4, 8)):
        t += r.uniform(60, 150)
        sy += r.uniform(40, 200)
        scrolls.append({"scrollY": sy, "t": int(t)})
    return _payload(mouse, clicks, keys, scrolls)


def slice_sparse_first_flush(seed):
    """The opening two seconds: very little has happened yet, and 'no data'
    must not read as 'suspicious'."""
    r = random.Random(seed)
    return _payload(*_form_fill(r, fields=1, points_per_reach=(2, 5),
                                keys_per_field=(2, 5), typing_median=200, typing_sigma=0.5))


HUMAN_SLICES = {
    "keyboard_only": slice_keyboard_only,
    "slow_typist": slice_slow_typist,
    "low_pointer": slice_low_pointer,
    "rapid_legitimate": slice_rapid_legitimate,
    "sparse_first_flush": slice_sparse_first_flush,
    "typical_human": slice_typical_human,
}

# Attack families come from the trainer's own personas, so this measures the
# distribution the model was actually fitted against.
ATTACK_PERSONAS = ["bot", "bot_sophisticated"]


def evaluate(n: int) -> dict:
    results = {"human_slices": {}, "attacks": {}, "latency_ms": []}

    for name, generate in HUMAN_SLICES.items():
        blocked = stepped_up = 0
        scores = []
        for i in range(n):
            raw = generate(10_000 + i)
            started = time.perf_counter()
            out = scorer.compute_risk(raw)
            results["latency_ms"].append((time.perf_counter() - started) * 1000)
            scores.append(out["risk_score"])
            if out["risk_score"] >= BLOCK_THRESHOLD:
                blocked += 1
            elif out["risk_score"] >= STEP_UP_THRESHOLD:
                stepped_up += 1
        results["human_slices"][name] = {
            "n": n, "blocked": blocked, "stepped_up": stepped_up,
            "median_risk": round(statistics.median(scores), 1),
        }

    for persona in ATTACK_PERSONAS:
        detected = 0
        scores = []
        for i in range(n):
            windows = T.simulate_session_windows(persona, 1_700_000_000_000 + i * 100_000)
            out = scorer.compute_risk(windows[-1])
            scores.append(out["risk_score"])
            if out["risk_score"] >= STEP_UP_THRESHOLD:
                detected += 1
        results["attacks"][persona] = {
            "n": n, "detected": detected,
            "median_risk": round(statistics.median(scores), 1),
        }

    real = evaluate_real_holdout()
    if real:
        results["real"] = real
    return results


def evaluate_real_holdout() -> dict:
    """Scores the HELD-OUT real-browser rows through the trained forest.

    Only the runs train_model.py held back are scored. Scoring every captured
    sample would include rows the forest was fitted on and report a number
    inflated by its own training data.
    """
    split = T.load_real_telemetry()
    if split is None:
        return {}
    _, _, X_eval, y_eval, scenarios = split
    bundle = scorer.get_bundle()
    proba = bundle.rf.predict_proba(bundle.scaler.transform(X_eval))[:, 1]

    out = {}
    for name in sorted(set(scenarios)):
        idx = [i for i, s in enumerate(scenarios) if s == name]
        flagged = int(sum(1 for i in idx if proba[i] >= 0.5))
        out[name] = {"n": len(idx), "flagged": flagged,
                     "label": int(y_eval[idx[0]]),
                     "mean_p": round(float(np.mean([proba[i] for i in idx])), 3)}
    return out


def report(results: dict) -> None:
    print("\nLEGITIMATE INTERACTION STYLES  (blocked = a lost customer)\n")
    print(f"  {'style':<20}{'blocked (95% CI)':>28}{'stepped up':>13}{'median':>9}")
    for name, r in results["human_slices"].items():
        print(f"  {name:<20}{fmt_rate(r['blocked'], r['n']):>28}"
              f"{100 * r['stepped_up'] / r['n']:>12.0f}%{r['median_risk']:>9}")

    print("\nATTACK FAMILIES  (detected = reached step-up or block)\n")
    print(f"  {'family':<20}{'detected (95% CI)':>28}{'median':>9}")
    for name, r in results["attacks"].items():
        print(f"  {name:<20}{fmt_rate(r['detected'], r['n']):>28}{r['median_risk']:>9}")

    if results.get("real"):
        print("\nHELD-OUT REAL BROWSER RUNS\n")
        print(f"  {'scenario':<22}{'flagged (95% CI)':>28}{'mean p(bot)':>13}")
        for name, r in results["real"].items():
            print(f"  {name:<22}{fmt_rate(r['flagged'], r['n']):>28}{r['mean_p']:>13}")

    lat = results["latency_ms"]
    lat.sort()
    print(f"\nLATENCY  median {statistics.median(lat):.1f} ms   "
          f"p95 {lat[int(len(lat) * 0.95)]:.1f} ms   n={len(lat)}\n")


def main() -> int:
    # A Windows console defaults to cp1252 and dies on the Turkish labels the
    # scorer returns; train_model.py carries the same guard.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="DeepCheck degerlendirme benchmark'i")
    parser.add_argument("--n", type=int, default=200, help="her dilim icin oturum sayisi")
    parser.add_argument("--json", help="neticeleri bu fayla da yaz")
    args = parser.parse_args()

    try:
        scorer.get_bundle()
    except Exception as exc:
        print(f"Model yuklenemedi: {exc}\nOnce `python train_model.py` calistirin.")
        return 1

    results = evaluate(args.n)
    report(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False)
        print(f"JSON: {os.path.abspath(args.json)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
