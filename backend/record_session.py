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

FEATURE_COLUMNS = (
    "scroll_hizi_varyansi",
    "tereddut_skoru",
    "etkilesim_entropisi",
    "ivme_degisimi",
    "tiklama_yogunlugu",
    "odak_degisimi",
)


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


def _write(record: dict, label: str, out_dir: str) -> str:
    target_dir = os.path.join(out_dir, label)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{record['session_id']}.json")
    record["label"] = label
    record["recorded_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    return path


async def record(session_ids: list[str], label: str, out_dir: str) -> int:
    written = 0
    async with get_sessionmaker()() as db:
        for session_id in session_ids:
            record_data = await _load(db, session_id)
            if record_data is None:
                print(f"ATLANDI  {session_id}: oturum yok veya hic akis kaydedilmemis")
                continue
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
    parser = argparse.ArgumentParser(description="Etiketli gercek oturumlari diske kaydeder.")
    parser.add_argument("session_ids", nargs="*", help="Kaydedilecek oturum kimlikleri")
    parser.add_argument("--label", choices=VALID_LABELS, help="Yer gercegi etiketi")
    parser.add_argument("--since", help="Bu ISO zamanindan sonraki tum oturumlar (ornek: 2026-09-05T14:00)")
    parser.add_argument("--list", action="store_true", help="Son oturumlari listele")
    parser.add_argument("--limit", type=int, default=40, help="--list icin satir sayisi")
    parser.add_argument("--out", default=DATA_DIR, help="Cikti kok dizini")
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

    written = asyncio.run(record(sorted(set(session_ids)), args.label, args.out))
    print(f"\nToplam {written} oturum kaydedildi -> {os.path.join(args.out, args.label)}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
