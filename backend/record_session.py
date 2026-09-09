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

    # Look before writing: what WOULD be recorded, and is any of it usable.
    python record_session.py --label human --since 2026-09-09T14:00 --preview

    # Freeze one session and put it in front of the model.
    python record_session.py --label human --person p01 --to-training 3f2a...

Writes data/real/{label}/{session_id}.json. Labels are exactly "human" or
"bot": the file's directory IS the ground truth, so mislabelling here
silently corrupts every later measurement.

Three things this refuses to do quietly, each of which was a way to poison
the set without ever seeing an error:

  * File a session as a person when the browser reported `navigator.webdriver`
    or synthesised its own events. Both signals are trivially defeated by an
    attacker, which is why they are worthless as detection and useful here --
    nobody recording their own colleagues is trying to defeat them, so when
    one fires it is a Playwright window somebody left open. `--force` if you
    are certain.

  * Accept a flush that measured almost nothing. Every feature has a neutral
    fallback, so a window in which the person did nothing still produces a
    full twelve-number vector made of fallbacks. Labelled "human", that
    teaches the model that an empty window is a person -- and an empty window
    is exactly what a naive headless bot sends. See MIN_MEASURED_FOR_TRAINING.

  * Merge into the training set without `--person`. The holdout is split by
    whoever produced the data; without a person the best it can do is split
    by session, and one person contributing ten sittings then appears on both
    sides of that split.

Timing matters: the retention sweep blanks raw telemetry after an hour
(`RAW_RETENTION_HOURS`) and deletes rows after a day. Record while the
session is still fresh -- an hour-old row cannot even be checked for quality,
because a blank mouse channel could equally be a keyboard-only person.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import scorer
from database import get_engine, get_sessionmaker
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

# How many of the twelve features a flush must genuinely MEASURE before it is
# allowed into the training set.
#
# Every feature has a neutral fallback, so a window in which the person did
# nothing still produces a full twelve-number vector -- one made almost
# entirely of fallbacks. Labelling that "human" teaches the model that an
# empty window is a person, and an empty window is precisely what a naive
# headless bot sends. The A1_naive rows in the same file say the opposite.
# Recording both without a gate does not average out; it teaches nothing
# where the model most needs to learn something.
#
# Same threshold the scorer uses to decide whether a score is worth showing
# anyone, and for the same reason.
MIN_MEASURED_FOR_TRAINING = scorer.MIN_MEASURED_FOR_CONFIDENT_SCORE


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
                # Needed to judge the recording, not to score it. `raw_purged`
                # says whether the retention sweep has already blanked the
                # channels -- without it an hour-old row is indistinguishable
                # from a person who sat still. `client_signals` is what the
                # browser said about itself, which is how a session recorded
                # from a driven browser gets caught before it is filed as a
                # person.
                "raw_purged": bool(row.raw_purged),
                "client_signals": row.client_signals or {},
            }
            for row in flushes
        ],
    }


def measured_count(flush: dict) -> int | None:
    """How many of the twelve features this flush actually measured.

    None means unknowable: the retention sweep has already blanked the raw
    channels, so an empty mouse trajectory could equally be a keyboard-only
    person or a row that has simply aged out. Guessing here is how a training
    set quietly fills with vectors made of neutral fallbacks.
    """
    if flush.get("raw_purged"):
        return None
    raw_values = scorer.extract_raw(flush.get("raw") or {})
    return sum(1 for name in FEATURE_NAMES if raw_values.get(name) is not None)


def provenance_problems(record: dict) -> list[str]:
    """Reasons this session should not be filed as a person.

    Both signals are self-reported and both are trivially defeated by anyone
    trying -- which is exactly why they are useless as detection and useful
    here. Nobody recording their own colleagues is trying to defeat them, so
    when one fires it is almost always the honest explanation: a Playwright
    window was left open, or the capture harness was still running.
    """
    problems = []
    driven = sum(1 for f in record["flushes"] if (f.get("client_signals") or {}).get("webdriver"))
    injected = sum(
        int((f.get("client_signals") or {}).get("untrusted_events") or 0)
        for f in record["flushes"]
    )
    if driven:
        problems.append(f"navigator.webdriver {driven} akista true -- surulen tarayici")
    if injected:
        problems.append(f"{injected} adet isTrusted=false olay -- sentetik girdi")
    return problems


def merge_into_training_set(
    records: list[dict],
    label: str,
    path: str,
    person_id: str | None = None,
    min_measured: int = MIN_MEASURED_FOR_TRAINING,
) -> dict:
    """Turns recorded sessions into the sample rows train_model.py reads.

    One sample per flush, carrying the session id as run_id AND the person as
    person_id, because the holdout has to be split by whoever generated the
    data. Flushes from one sitting are correlated; so are sittings from one
    person. Splitting by session alone lets the same person appear on both
    sides of the split, which reports an accuracy that will not reproduce on
    somebody new -- and "does it work on somebody new" is the only question
    this dataset exists to answer.

    Flushes that measured too little are dropped rather than filed, and the
    count is reported: a vector of neutral fallbacks labelled "human" is worse
    than no row at all.

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
    thin = 0
    purged = 0
    for record in records:
        for flush in record["flushes"]:
            features = flush.get("features") or {}
            # A flush recorded before a feature existed cannot be blended: the
            # vector would be the wrong width, or worse, silently mis-ordered.
            if any(features.get(name) is None for name in FEATURE_NAMES):
                skipped += 1
                continue
            measured = measured_count(flush)
            if measured is None:
                purged += 1
                continue
            if measured < min_measured:
                thin += 1
                continue
            sample = {
                "features": {name: float(features[name]) for name in FEATURE_NAMES},
                "label": 0 if label == "human" else 1,
                "scenario": LIVE_SCENARIOS[label],
                "run_id": record["session_id"],
                "measured": measured,
            }
            if person_id:
                sample["person_id"] = person_id
            kept.append(sample)
            added += 1

    payload["samples"] = kept
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    return {
        "added": added,
        "skipped": skipped,
        "thin": thin,
        "purged": purged,
        "replaced": replaced,
        "total": len(kept),
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


def _summary(record: dict) -> dict:
    """The three numbers that decide whether a recording is worth keeping."""
    measured = [measured_count(f) for f in record["flushes"]]
    known = [m for m in measured if m is not None]
    return {
        "flushes": len(record["flushes"]),
        "usable": sum(1 for m in known if m >= MIN_MEASURED_FOR_TRAINING),
        "purged": len(measured) - len(known),
        "median_measured": statistics.median(known) if known else 0,
        "problems": provenance_problems(record),
    }


async def preview(session_ids: list[str], label: str) -> int:
    """Show what WOULD be recorded, and write nothing.

    The failure this exists to prevent: `--since` sweeps every session in a
    window, and a window almost never contains only the person you were
    watching. A red-team run, a colleague's tab, the capture harness left open
    -- each of those becomes a row labelled by hand as a human being, and the
    directory a sample sits in IS its ground truth. One bad sweep is not a bad
    row, it is a quietly wrong model, and nothing downstream will ever say so.
    """
    async with get_sessionmaker()() as db:
        records, missing = [], []
        for session_id in session_ids:
            record_data = await _load(db, session_id)
            if record_data is None:
                # Reported rather than skipped in silence: a mistyped id that
                # simply vanishes reads as "that session had no problems".
                missing.append(session_id)
            else:
                records.append(record_data)

    for session_id in missing:
        print(f"BULUNAMADI  {session_id}: oturum yok veya hic akis kaydedilmemis")

    if not records:
        print("Onizlenecek oturum yok.")
        return 1

    print(f"\n'{label}' olarak kaydedilecek {len(records)} oturum -- HENUZ YAZILMADI\n")
    print(f"{'session_id':38}  {'akis':>4}  {'kullanilabilir':>14}  {'skor':>6}  durum")
    keepable = 0
    for r in records:
        s = _summary(r)
        if s["problems"]:
            status = "SUPHELI: " + "; ".join(s["problems"])
        elif s["purged"] == s["flushes"]:
            status = "GEC KALINDI: ham telemetri silinmis (1 saatlik pencere)"
        elif s["usable"] == 0:
            status = f"ZAYIF: hicbir akis {MIN_MEASURED_FOR_TRAINING} ozellik olcmemis"
        else:
            status = "UYGUN"
            keepable += 1
        score = r.get("smoothed_risk_score") or 0.0
        print(f"{r['session_id']:38}  {s['flushes']:4}  {s['usable']:14}  {score:6.1f}  {status}")

    print(f"\n{keepable}/{len(records)} oturum kaydedilmeye uygun.")
    print("Kaydetmek icin ayni komutu --preview olmadan, tercihen oturum "
          "kimliklerini tek tek vererek calistirin.")
    return 0


async def record(
    session_ids: list[str],
    label: str,
    out_dir: str,
    collected: list | None = None,
    force: bool = False,
) -> int:
    written = 0
    async with get_sessionmaker()() as db:
        for session_id in session_ids:
            record_data = await _load(db, session_id)
            if record_data is None:
                print(f"ATLANDI  {session_id}: oturum yok veya hic akis kaydedilmemis")
                continue
            # A driven browser filed as a person is the one error this whole
            # dataset cannot survive: it teaches the detector that automation
            # is what people look like, and every later measurement inherits
            # it while reporting nothing.
            problems = provenance_problems(record_data) if label == "human" else []
            if problems and not force:
                print(f"REDDEDILDI  {session_id}: {'; '.join(problems)}")
                print("            Gercekten bir insansa --force ile gecebilirsiniz.")
                continue
            if collected is not None:
                collected.append(record_data)
            path = _write(record_data, label, out_dir)
            print(f"YAZILDI  {path}  ({len(record_data['flushes'])} akis)")
            written += 1
    return written


def parse_since(since: str) -> datetime:
    """"20m" / "2h" / an ISO timestamp.

    The relative forms exist because the absolute one is a trap. created_at is
    stored in UTC, and a naive ISO string is read as UTC -- so an operator in
    UTC+4 who types their own wall clock sweeps four extra hours of sessions
    and labels every one of them by hand as a person. "20m" cannot be wrong
    about a timezone.
    """
    text = since.strip().lower()
    if text and text[-1] in "mh" and text[:-1].replace(".", "", 1).isdigit():
        amount = float(text[:-1])
        delta = timedelta(minutes=amount) if text[-1] == "m" else timedelta(hours=amount)
        return datetime.now(timezone.utc) - delta
    moment = datetime.fromisoformat(since)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def resolve_since(since: str) -> list[str]:
    moment = parse_since(since)
    async with get_sessionmaker()() as db:
        result = await db.execute(
            select(Session.id).where(Session.created_at >= moment).order_by(Session.created_at.asc())
        )
        return [row[0] for row in result.all()]


async def _with_pool(coro):
    """Runs one coroutine and then disposes the connection pool, in the SAME loop.

    Every asyncio.run() builds a loop and closes it at the end, but the engine
    is a module-level singleton whose pooled connections stay bound to
    whichever loop opened them. Two asyncio.run() calls in one process --
    resolve_since() and then record() -- therefore hand loop two a connection
    belonging to loop one, and the teardown dies with "Event loop is closed"
    after the work is already done. On Windows that killed the --since path
    outright, which is the path an operator actually uses.
    """
    try:
        return await coro
    finally:
        await get_engine().dispose()


async def _gather(args, collected: list) -> tuple[int, bool]:
    """Resolve ids, then preview or record -- all inside one event loop."""
    session_ids = list(args.session_ids)
    if args.since:
        session_ids += await resolve_since(args.since)
    session_ids = sorted(set(session_ids))
    if not session_ids:
        print("Bu araliktan hic oturum yok.")
        return 1, True
    if args.preview:
        return await preview(session_ids, args.label), True
    return await record(session_ids, args.label, args.out, collected, force=args.force), False


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
    parser.add_argument(
        "--since",
        help="Bu andan sonraki tum oturumlar. Goreli: 20m, 2h. Mutlak: 2026-09-09T14:00 "
             "(UTC olarak okunur -- goreli bicimi tercih edin)",
    )
    parser.add_argument("--list", action="store_true", help="Son oturumlari listele")
    parser.add_argument("--limit", type=int, default=40, help="--list icin satir sayisi")
    parser.add_argument("--out", default=DATA_DIR, help="Cikti kok dizini")
    parser.add_argument(
        "--to-training",
        action="store_true",
        help="Kaydedilen oturumlari lab/real_telemetry.json icine de ekle (model bunu okur)",
    )
    parser.add_argument("--training-out", default=TRAINING_SET_PATH, help="Egitim kumesi yolu")
    parser.add_argument(
        "--person",
        help="Oturumu ureten kisinin takma kimligi (ornek: p01). Egitim kumesindeki "
             "ayirma bu alana gore yapilir; --to-training icin zorunludur.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Neyin kaydedilecegini goster, hicbir sey yazma",
    )
    parser.add_argument(
        "--min-measured",
        type=int,
        default=MIN_MEASURED_FOR_TRAINING,
        help=f"Egitime girmek icin bir akisin olcmesi gereken en az ozellik sayisi "
             f"(varsayilan {MIN_MEASURED_FOR_TRAINING})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Surulen tarayici uyarisina ragmen 'human' olarak kaydet",
    )
    args = parser.parse_args()

    if args.list:
        asyncio.run(_with_pool(list_sessions(args.limit)))
        return 0

    if not args.label:
        parser.error("--label zorunlu (human veya bot)")

    # Without this the holdout can only be split by session, and one person
    # contributing ten sittings then appears on both sides of that split. The
    # resulting accuracy answers "does it recognise this person again", not
    # "does it work on someone new" -- and only the second question is worth
    # the trouble of collecting the data.
    if args.to_training and not args.person:
        parser.error(
            "--to-training icin --person zorunlu (ornek: --person p01). "
            "Ayni kisinin oturumlari egitimde ve testte birden gorunmesin diye."
        )

    if not args.session_ids and not args.since:
        parser.error("En az bir oturum kimligi veya --since gerekli")

    collected: list = []
    written, previewed = asyncio.run(_with_pool(_gather(args, collected)))
    if previewed:
        return written

    print(f"\nToplam {written} oturum kaydedildi -> {os.path.join(args.out, args.label)}")

    if args.to_training and collected:
        stats = merge_into_training_set(
            collected, args.label, args.training_out, args.person, args.min_measured
        )
        print(
            f"Egitim kumesine eklendi: +{stats['added']} satir "
            f"(kisi: {args.person}; {stats['replaced']} eski satir degistirildi)"
        )
        if stats["thin"]:
            print(
                f"  {stats['thin']} akis atlandi: {args.min_measured} ozellikten az olculdu. "
                "Bunlar cogunlukla notr varsayilan olurdu ve 'bos pencere = insan' "
                "ogretirdi."
            )
        if stats["purged"]:
            print(
                f"  {stats['purged']} akis atlandi: ham telemetri silinmis. "
                "Kayit, oturumdan sonraki 1 saat icinde alinmali."
            )
        if stats["skipped"]:
            print(f"  {stats['skipped']} akis atlandi: eksik ozellik.")
        print(f"  Toplam {stats['total']} satir -> {args.training_out}")
        if stats["added"]:
            print("Modeli yeniden egitin: cd backend && python train_model.py")
    elif args.to_training:
        print("Egitim kumesine eklenecek oturum yok.")

    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
