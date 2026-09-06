"""Records a labelled real session out of Postgres and onto disk.

The models currently learn from `train_model.py`'s simulator, and
`test_scorer.py` checks them against hand-built payloads. Both are synthetic,
so any accuracy number they produce measures fit to the simulator rather than
performance against people. This is the collection half of fixing that: it
freezes a real session -- every flush the SDK actually posted -- into a JSON
file that `evaluate.py` can replay offline, as many times as the models
change.

Usage:

    # See what is in the database, newest first, and pick an id.
    python record_session.py --list

    # Freeze one session under a label.
    python record_session.py --label human 3f2a...-...
    python record_session.py --label bot   9c11...-...

    # Everything since a timestamp, all under one label (useful right after a
    # scripted bot run).
    python record_session.py --label bot --since 2026-09-05T14:00:00

Writes data/real/{label}/{session_id}.json. Labels are exactly "human" or
"bot": the file's directory IS the ground truth, so mislabelling here
silently corrupts every later measurement.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from database import get_sessionmaker
from lstm_model import FEATURE_NAMES
from models import BehaviorData, Session

VALID_LABELS = ("human", "bot")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "real")

# Channel names as the scorer expects them. BehaviorData stores the scroll
# channel under `scroll_rhythm`, so the mapping is not identity.
RAW_CHANNELS = {
    "mouse_trajectory": "mouse_trajectory",
    "click_timing": "click_timing",
    "scroll_events": "scroll_rhythm",
    "hesitation_intervals": "hesitation_intervals",
    "focus_changes": "focus_changes",
    "key_events": "key_events",
}

# Imported, never listed here. This was a hand-written copy of the six original
# features and it silently went stale when six more were added: a recording
# made with it would have carried half the vector, and the omission would only
# have surfaced as a training run quietly learning less than it should.
FEATURE_COLUMNS = tuple(FEATURE_NAMES)

# Where training reads its real-browser rows from. Sessions recorded here are
# merged into that file so a recording actually reaches the model -- without
# this step the archive under data/real/ was only ever readable by evaluate.py,
# and a session recorded from a real person changed nothing.
TRAINING_SET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lab", "real_telemetry.json"
)

# Scenario names for recorded sessions, kept distinct from the browser lab's
# scripted H1/H2/A1-A4 so the holdout report can tell "a person did this" apart
# from "a script did this", which is the whole point of collecting them.
LIVE_SCENARIOS = {"human": "R1_human_live", "bot": "R2_bot_live"}


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


async def list_sessions(limit: int) -> None:
    async with get_sessionmaker()() as db:
        result = await db.execute(
            select(Session).order_by(Session.last_seen_at.desc()).limit(limit)
        )
        rows = result.scalars().all()

    if not rows:
        print("Veritabaninda hic oturum yok.")
        return

    print(f"{'session_id':38}  {'skor':>6}  {'etiket':22}  son gorulme")
    for s in rows:
        print(f"{s.id:38}  {s.risk_score or 0:6.1f}  {(s.label or ''):22}  {_iso(s.last_seen_at)}")


async def _load(db, session_id: str) -> dict | None:
    session = await db.get(Session, session_id)
    if session is None:
        return None

    result = await db.execute(
        select(BehaviorData)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.asc())
    )
    flushes = result.scalars().all()
    if not flushes:
        return None

    return {
        "session_id": session.id,
        "created_at": _iso(session.created_at),
        "last_seen_at": _iso(session.last_seen_at),
        "smoothed_risk_score": session.risk_score,
        "smoothed_label": session.label,
        "flushes": [
            {
                "created_at": _iso(row.created_at),
                "risk_score": row.risk_score,
                "raw": {
                    name: (getattr(row, column) or []) for name, column in RAW_CHANNELS.items()
                },
                "features": {name: getattr(row, name) for name in FEATURE_COLUMNS},
            }
            for row in flushes
        ],
    }


def merge_into_training_set(records: list[dict], label: str, path: str) -> dict:
    """Turns recorded sessions into the sample rows train_model.py reads.

    One sample per flush, carrying the session id as run_id so the holdout
    split stays by session: flushes from one sitting are correlated, and
    splitting them across train and test would report a number that will not
    reproduce on a fresh person.

    Re-recording a session replaces its rows rather than duplicating them.
    """
    payload = {"note": "Real telemetry", "feature_keys": list(FEATURE_NAMES), "samples": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    payload.setdefault("samples", [])
    payload["feature_keys"] = list(FEATURE_NAMES)

    incoming_runs = {r["session_id"] for r in records}
    kept = [s for s in payload["samples"] if s.get("run_id") not in incoming_runs]
    replaced = len(payload["samples"]) - len(kept)

    added = 0
    skipped = 0
    for record in records:
        for flush in record["flushes"]:
            features = flush.get("features") or {}
            # A flush recorded before a feature existed cannot be blended: the
            # vector would be the wrong width, or worse, silently mis-ordered.
            if any(features.get(name) is None for name in FEATURE_NAMES):
                skipped += 1
                continue
            kept.append(
                {
                    "features": {name: float(features[name]) for name in FEATURE_NAMES},
                    "label": 0 if label == "human" else 1,
                    "scenario": LIVE_SCENARIOS[label],
                    "run_id": record["session_id"],
                }
            )
            added += 1

    payload["samples"] = kept
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    return {"added": added, "skipped": skipped, "replaced": replaced, "total": len(kept)}


def _write(record: dict, label: str, out_dir: str) -> str:
    target_dir = os.path.join(out_dir, label)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{record['session_id']}.json")
    record["label"] = label
    record["recorded_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    return path


async def record(session_ids: list[str], label: str, out_dir: str, collected: list | None = None) -> int:
    written = 0
    async with get_sessionmaker()() as db:
        for session_id in session_ids:
            record_data = await _load(db, session_id)
            if record_data is None:
                print(f"ATLANDI  {session_id}: oturum yok veya hic akis kaydedilmemis")
                continue
            if collected is not None:
                collected.append(record_data)
            path = _write(record_data, label, out_dir)
            print(f"YAZILDI  {path}  ({len(record_data['flushes'])} akis)")
            written += 1
    return written


async def resolve_since(since: str) -> list[str]:
    moment = datetime.fromisoformat(since)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    async with get_sessionmaker()() as db:
        result = await db.execute(
            select(Session.id).where(Session.created_at >= moment).order_by(Session.created_at.asc())
        )
        return [row[0] for row in result.all()]


def main() -> int:
    # A Windows console defaults to cp1252, where "i" without a dot is
    # unencodable, so printing a Turkish label killed the run after the work
    # was already done. train_model.py carries the same guard.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="Etiketli gercek oturumlari diske kaydeder.")
    parser.add_argument("session_ids", nargs="*", help="Kaydedilecek oturum kimlikleri")
    parser.add_argument("--label", choices=VALID_LABELS, help="Yer gercegi etiketi")
    parser.add_argument("--since", help="Bu ISO zamanindan sonraki tum oturumlar (ornek: 2026-09-05T14:00)")
    parser.add_argument("--list", action="store_true", help="Son oturumlari listele")
    parser.add_argument("--limit", type=int, default=40, help="--list icin satir sayisi")
    parser.add_argument("--out", default=DATA_DIR, help="Cikti kok dizini")
    parser.add_argument(
        "--to-training",
        action="store_true",
        help="Kaydedilen oturumlari lab/real_telemetry.json icine de ekle (model bunu okur)",
    )
    parser.add_argument("--training-out", default=TRAINING_SET_PATH, help="Egitim kumesi yolu")
    args = parser.parse_args()

    if args.list:
        asyncio.run(list_sessions(args.limit))
        return 0

    if not args.label:
        parser.error("--label zorunlu (human veya bot)")

    session_ids = list(args.session_ids)
    if args.since:
        session_ids += asyncio.run(resolve_since(args.since))
    if not session_ids:
        parser.error("En az bir oturum kimligi veya --since gerekli")

    collected: list = []
    written = asyncio.run(record(sorted(set(session_ids)), args.label, args.out, collected))
    print(f"\nToplam {written} oturum kaydedildi -> {os.path.join(args.out, args.label)}")

    if args.to_training and collected:
        stats = merge_into_training_set(collected, args.label, args.training_out)
        print(
            f"Egitim kumesine eklendi: +{stats['added']} satir "
            f"({stats['replaced']} eski satir degistirildi, "
            f"{stats['skipped']} eksik ozellikli satir atlandi). "
            f"Toplam {stats['total']} satir -> {args.training_out}"
        )
        print("Modeli yeniden egitin: cd backend && python train_model.py")
    elif args.to_training:
        print("Egitim kumesine eklenecek oturum yok.")

    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
