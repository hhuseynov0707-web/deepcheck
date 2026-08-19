"""Loads real captured behavior traces into the same (X, y) shape that
train_model.generate_synthetic_dataset() produces.

Traces are JSONL, one record per SDK flush:

    {"label": "bot", "profile": "linear_mover", "payload": { ...SDK body... }}

Bot traces come from tools/bot-harness, which drives the real demo page with
real browser automation. Human traces come from real people using the SDK.
Both are fed through scorer.extract_features() -- the same function the API
uses at request time -- so there is no train/serve skew.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from lstm_model import FEATURE_NAMES
from scorer import extract_features

# Repo-root-relative, so this works from backend/ or from the root.
_DATA = Path(__file__).resolve().parent.parent / "data"

# Two directories, deliberately. Bot traces are machine output and safe to
# commit. Human traces are behavioural data about identifiable people --
# personal data under KVKK -- so that directory is git-ignored by default and
# publishing it is a separate, considered decision.
BOT_TRACE_DIR = _DATA / "bot-traces"
HUMAN_TRACE_DIR = _DATA / "human-traces"
DEFAULT_TRACE_DIRS = (BOT_TRACE_DIR, HUMAN_TRACE_DIR)

LABELS = {"human": 0, "bot": 1}


def iter_trace_records(directory: Path):
    """Yield (label, profile, payload) from every .jsonl file in `directory`.

    Malformed lines are skipped with a warning rather than aborting the run:
    a capture interrupted mid-write should not cost you the whole file.
    """
    for path in sorted(Path(directory).glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  skipping malformed line {path.name}:{lineno}")
                    continue

                label = record.get("label")
                payload = record.get("payload")
                if label not in LABELS or not isinstance(payload, dict):
                    print(f"  skipping unusable record {path.name}:{lineno}")
                    continue

                yield label, record.get("profile", "unknown"), payload


def load_real_dataset(directories=DEFAULT_TRACE_DIRS):
    """Return (X, y, stats), or (None, None, stats) when there is nothing to load."""
    rows: list[list[float]] = []
    labels: list[int] = []
    stats: Counter = Counter()

    if isinstance(directories, (str, Path)):
        directories = (directories,)
    present = [Path(d) for d in directories if Path(d).exists()]
    if not present:
        return None, None, stats

    for label, profile, payload in _iter_all(present):
        try:
            features = extract_features(payload)
        except Exception as exc:  # a bad payload should not kill the run
            print(f"  skipping payload ({profile}): {exc}")
            stats["failed"] += 1
            continue

        rows.append([features[name] for name in FEATURE_NAMES])
        labels.append(LABELS[label])
        stats[f"{label}:{profile}"] += 1

    if not rows:
        return None, None, stats

    return np.asarray(rows, dtype=float), np.asarray(labels, dtype=int), stats


def _iter_all(directories):
    for directory in directories:
        yield from iter_trace_records(directory)


def describe(directories=DEFAULT_TRACE_DIRS) -> None:
    """Print what is in the trace directory and how the features look.

    Run this after a capture to confirm the profiles actually differ before
    you spend time training on them.
    """
    if isinstance(directories, (str, Path)):
        directories = (directories,)
    X, y, stats = load_real_dataset(directories)
    for directory in directories:
        mark = "found" if Path(directory).exists() else "absent"
        print(f"{mark:>6}  {directory}")

    if X is None:
        print("no usable traces found")
        if stats:
            print(f"  counters: {dict(stats)}")
        return

    print(f"\n{len(X)} rows across {len(FEATURE_NAMES)} features")
    for key in sorted(stats):
        print(f"  {key:<32} {stats[key]}")

    print(f"\n  {'feature':<24} {'human mean':>12} {'bot mean':>12}")
    human = X[y == 0]
    bot = X[y == 1]
    for i, name in enumerate(FEATURE_NAMES):
        h = f"{human[:, i].mean():.3f}" if len(human) else "-"
        b = f"{bot[:, i].mean():.3f}" if len(bot) else "-"
        print(f"  {name:<24} {h:>12} {b:>12}")

    if not len(human):
        print("\n  No human traces yet — collect them with the SDK and drop the")
        print("  JSONL in this directory with \"label\": \"human\".")


if __name__ == "__main__":
    import sys

    describe([Path(a) for a in sys.argv[1:]] or DEFAULT_TRACE_DIRS)
