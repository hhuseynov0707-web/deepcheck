"""Measures the detector against RECORDED REAL sessions, not the simulator.

`train_model.py` generates the training data and `test_scorer.py` asserts on
hand-built payloads. Both are synthetic, and a number produced by either one
measures how well the models fit the simulator -- not how well they tell a
person from a script. This replays sessions captured by `record_session.py`
through the real serving path (`scorer.compute_risk`, including the LSTM's
flush history and the same median smoothing `/api/analyze` applies) and
reports what actually happened.

Usage:
    python evaluate.py                       # reads ../data/real
    python evaluate.py --threshold 60        # decision boundary to score at
    python evaluate.py --markdown ../docs/evaluation.md

Reads data/real/human/*.json and data/real/bot/*.json. The directory name is
the ground truth.
"""

import argparse
import glob
import json
import os
import statistics
import sys
from datetime import datetime, timezone

import numpy as np

import scorer
from lstm_model import FEATURE_NAMES, SEQUENCE_LENGTH

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "real")

# Mirrors main.py. A session's reported score is the median of its last few
# flushes, so an honest evaluation has to smooth the same way the API does.
SMOOTHING_WINDOW = 5

# 60 is where the deployed ladder stops letting a session through untouched
# (60-80 asks for step-up verification, 80+ blocks), so it is the boundary a
# false positive actually costs a customer something at.
DEFAULT_THRESHOLD = 60.0

LABELS = ("human", "bot")


def load_sessions(data_dir: str) -> list[dict]:
    sessions = []
    for label in LABELS:
        for path in sorted(glob.glob(os.path.join(data_dir, label, "*.json"))):
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
            record["label"] = label
            record["path"] = path
            sessions.append(record)
    return sessions


def replay(record: dict) -> float | None:
    """Re-scores one recorded session exactly as the API would have.

    Each flush is scored with the real history that preceded it, and the
    session's score is the median of the last SMOOTHING_WINDOW flushes --
    the same value /api/analyze stores and /api/decision reads.
    """
    history: list[list[float]] = []
    per_flush: list[float] = []

    for flush in record.get("flushes", []):
        raw = flush.get("raw") or {}
        result = scorer.compute_risk(raw, history[-(SEQUENCE_LENGTH - 1):])
        per_flush.append(result["risk_score"])
        history.append([result["features"][name] for name in FEATURE_NAMES])

    if not per_flush:
        return None
    return round(statistics.median(per_flush[-SMOOTHING_WINDOW:]), 1)


def roc_auc(y_true: list[int], scores: list[float]) -> float | None:
    """Rank-based AUC, ties averaged. Undefined with only one class present."""
    positives = [s for s, y in zip(scores, y_true) if y == 1]
    negatives = [s for s, y in zip(scores, y_true) if y == 0]
    if not positives or not negatives:
        return None

    order = np.argsort(np.asarray(scores, dtype=float), kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    sorted_scores = np.asarray(scores, dtype=float)[order]
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1

    positive_rank_sum = sum(r for r, y in zip(ranks, y_true) if y == 1)
    n_pos, n_neg = len(positives), len(negatives)
    return float((positive_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def evaluate(sessions: list[dict], threshold: float) -> dict:
    rows = []
    for record in sessions:
        score = replay(record)
        if score is None:
            continue
        rows.append(
            {
                "session_id": record.get("session_id", os.path.basename(record["path"])),
                "label": record["label"],
                "y_true": 1 if record["label"] == "bot" else 0,
                "score": score,
                "flushes": len(record.get("flushes", [])),
            }
        )

    y_true = [r["y_true"] for r in rows]
    scores = [r["score"] for r in rows]
    y_pred = [1 if s >= threshold else 0 for s in scores]

    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    total = len(rows)

    return {
        "rows": rows,
        "threshold": threshold,
        "total": total,
        "humans": sum(1 for t in y_true if t == 0),
        "bots": sum(1 for t in y_true if t == 1),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": (tp + tn) / total if total else None,
        "false_positive_rate": fp / (fp + tn) if (fp + tn) else None,
        "recall": tp / (tp + fn) if (tp + fn) else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "roc_auc": roc_auc(y_true, scores),
    }


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def render_text(report: dict) -> str:
    lines = [
        "Gercek oturumlar uzerinde degerlendirme",
        "=" * 48,
        f"Oturum sayisi      : {report['total']} ({report['humans']} insan, {report['bots']} bot)",
        f"Karar esigi        : {report['threshold']:.0f}",
        f"Dogruluk           : {_pct(report['accuracy'])}",
        f"Yanlis pozitif oran: {_pct(report['false_positive_rate'])}",
        f"Yakalama (recall)  : {_pct(report['recall'])}",
        f"Kesinlik           : {_pct(report['precision'])}",
        "ROC-AUC            : "
        + ("-" if report["roc_auc"] is None else f"{report['roc_auc']:.4f}"),
        "",
        f"TP {report['tp']}   TN {report['tn']}   FP {report['fp']}   FN {report['fn']}",
    ]
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# Evaluation on recorded real sessions",
        "",
        f"Generated by `backend/evaluate.py` on {generated}. Every number here comes",
        "from replaying recorded sessions through the same `scorer.compute_risk()`",
        "path the API serves, with the same median smoothing applied.",
        "",
        f"**Sessions:** {report['total']} ({report['humans']} human, {report['bots']} bot)  ",
        f"**Decision threshold:** {report['threshold']:.0f}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Accuracy | {_pct(report['accuracy'])} |",
        f"| False positive rate | {_pct(report['false_positive_rate'])} |",
        f"| Recall (bots caught) | {_pct(report['recall'])} |",
        f"| Precision | {_pct(report['precision'])} |",
        "| ROC-AUC | "
        + ("-" if report["roc_auc"] is None else f"{report['roc_auc']:.4f}")
        + " |",
        "",
        "| | Predicted human | Predicted bot |",
        "|---|---|---|",
        f"| **Actual human** | {report['tn']} | {report['fp']} |",
        f"| **Actual bot** | {report['fn']} | {report['tp']} |",
        "",
        "## Per-session scores",
        "",
        "| Session | Label | Flushes | Score |",
        "|---|---|---|---|",
    ]
    for row in sorted(report["rows"], key=lambda r: (r["label"], -r["score"])):
        lines.append(
            f"| `{row['session_id'][:12]}` | {row['label']} | {row['flushes']} | {row['score']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Kaydedilmis gercek oturumlarla degerlendirme.")
    parser.add_argument("--data", default=DATA_DIR, help="data/real dizini")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--markdown", help="Raporu bu dosyaya markdown olarak yaz")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    sessions = load_sessions(args.data)
    if not sessions:
        print(
            "Hic kayitli oturum bulunamadi.\n\n"
            f"Beklenen konum: {os.path.abspath(args.data)}/{{human,bot}}/*.json\n\n"
            "Once gercek oturum toplayin:\n"
            "  1) Demo sayfasini gercek kullanicilarla ve bot betikleriyle calistirin\n"
            "  2) python record_session.py --list\n"
            "  3) python record_session.py --label human <oturum-id>\n"
            "  4) python record_session.py --label bot   <oturum-id>\n",
            file=sys.stderr,
        )
        return 1

    report = evaluate(sessions, args.threshold)
    print(render_text(report))

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
        print(f"\nMarkdown rapor yazildi: {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
