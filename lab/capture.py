"""Captures labelled REAL-browser telemetry as a training/evaluation dataset.

This is the answer to the single biggest weakness in the project: the models
were trained entirely on synthetic personas written by the same author as the
detector, so their separation was partly self-fulfilling.

The bot lab proved that concretely. Driving the *same* evasive attack through
a real Chromium instead of the simulator moved it from 88.9 (blocked) to 36.5
(approved), because two of the heaviest timing features are inverted between
real and simulated telemetry:

    etkilesim_entropisi   real bot 0.225  vs  synthetic bot 0.836
    duraklama_dagilimi    real bot 1.000  vs  synthetic bot 0.264

The model had learned "high entropy = bot" and "high pause dispersion =
human", and a real browser produces the opposite on both. No amount of
retuning the simulator fixes that reliably; the distribution the detector must
work on is the one a real browser emits, so that is what it should be trained
and evaluated on.

Each scenario run yields several flushes, and every flush is one labelled
sample -- so a few minutes of browser time produces a few hundred rows of
genuine telemetry.

What a sample stores, and why RAW telemetry is the part that matters:

    raw             the exact channels the SDK POSTed to /api/analyze for this
                    flush, intercepted in the browser. train_model.py
                    re-extracts every feature from this under the scaling in
                    force when it trains.
    features        what the server computed at capture time (GET
                    /api/score history). Inspection only.
    client_signals  the SDK's self-reported provenance counters, kept apart
                    from raw because they never reach a feature.
    client_sent_at  the sender's clock at send time, when the SDK sends it.

The first capture stored `features` alone. Those are normalised by whichever
bundle was serving, so the 120x-weighted rows were frozen to that bundle's
feature_scaling and neutral_defaults: retrain after a simulator change and the
real rows silently sit in the old coordinate system. And because the raw
values were gone, the scale itself could never be refitted to include them --
which is why ivme_degisimi sat clipped at 1.0 in 90 of 234 rows.

The file header records the feature_scaling and neutral_defaults that produced
the stored `features`, but only when that can be checked: if backend/ is
importable here, every captured flush is re-extracted locally and compared
with what the server stored. Only when every flush reproduces exactly are the
local bundle's values written; otherwise the header says null rather than
guess.

Usage (backend must be running with the demo endpoints on, see README.md):
    python lab/capture.py --api http://127.0.0.1:8000 --repeats 8
"""

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

import bot_lab as L

OUT_PATH = L.LAB_DIR / "real_telemetry.json"
BACKEND_DIR = L.ROOT / "backend"

# The development dashboard key main.py falls back to under DEBUG=1 -- which
# is what `docker compose up` runs by default. A backend started with its own
# DASHBOARD_KEY needs --dashboard-key (or the same environment variable here).
DEV_DASHBOARD_KEY = "deepcheck-dev-pano-anahtari-2026"

# main.py's HISTORY_LIMIT: GET /api/score returns at most this many newest rows.
HISTORY_LIMIT = 200

# label 1 = automation, 0 = legitimate human
LABELLED_SCENARIOS = [
    ("H1_human", L.scenario_human, 0),
    ("H2_keyboard_only", L.scenario_keyboard_only, 0),
    ("A1_naive", L.scenario_naive, 1),
    ("A2_randomized", L.scenario_randomized, 1),
    ("A3_human_mimic", L.scenario_human_mimic, 1),
    ("A4_evasive", L.scenario_evasive, 1),
]

FEATURE_KEYS = [
    "scroll_hizi_varyansi",
    "tereddut_skoru",
    "etkilesim_entropisi",
    "ivme_degisimi",
    "tiklama_yogunlugu",
    "odak_degisimi",
    "hiz_otokorelasyonu",
    "yon_tutarliligi",
    "zaman_kuantasyonu",
    "duraklama_dagilimi",
    "tiklama_oncesi_hareket",
    "kanal_gecis_gecikmesi",
]

# The telemetry channels of an /api/analyze body, under the names
# scorer.extract_raw() reads. session_id and client_signals are not behaviour.
RAW_CHANNELS = (
    "mouse_trajectory",
    "click_timing",
    "scroll_events",
    "hesitation_intervals",
    "focus_changes",
    "key_events",
)

# A flush with no event from any of these (only hesitation intervals: an idle
# tab) is neither stored nor scored by /api/analyze, and the current SDK does
# not send one. An older SDK does, so capture drops them: they describe nothing
# the served model is ever asked about.
TIMESTAMPED_CHANNELS = (
    "mouse_trajectory",
    "click_timing",
    "scroll_events",
    "key_events",
    "focus_changes",
)

# Stored features come back from Postgres as floats; anything past float noise
# is a different extraction, not the same one.
VERIFY_TOLERANCE = 1e-6


def fetch_history(api, dashboard_key, session_id):
    request = urllib.request.Request(
        f"{api}/api/score/{session_id}", headers={"X-Dashboard-Key": dashboard_key}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # No row: the server stored no flush for this session at all.
            return {"history": []}
        if exc.code == 401:
            raise SystemExit(
                "GET /api/score 401 dondu: pano anahtari yanlis. Backend'in DASHBOARD_KEY "
                "degerini --dashboard-key ile verin."
            ) from None
        raise


def load_backend_scorer():
    """backend/scorer.py with its bundle loaded, or None.

    Optional on purpose: the lab only needs Playwright, and the backend may be
    running in a container whose Python this process does not have. Without
    it the capture still records raw telemetry, which is what training reads;
    it just cannot certify which extraction produced the stored features.
    """
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        import scorer  # noqa: WPS433 (deliberately lazy)
        from lstm_model import FEATURE_NAMES

        scorer.get_bundle()
    except Exception as exc:  # ImportError, missing or mismatched model, ...
        print(
            f"NOT: backend/scorer.py yuklenemedi ({type(exc).__name__}: {exc}).\n"
            "     Ham telemetri yine kaydediliyor; saklanan ozelliklerin hangi olcekle\n"
            "     uretildigi dogrulanamayacak, dosya basligina null yazilacak."
        )
        return None
    if list(FEATURE_NAMES) != FEATURE_KEYS:
        raise SystemExit(
            "lab/capture.py FEATURE_KEYS, backend/lstm_model.py FEATURE_NAMES ile ayni degil. "
            "Once listeyi esitleyin."
        )
    return scorer


def carries_behaviour(raw):
    return any(raw.get(name) for name in TIMESTAMPED_CHANNELS)


def features_match(scorer, raw, features):
    local = scorer.extract_features(raw)
    return all(
        features.get(name) is not None
        and math.isfinite(float(features[name]))
        and abs(float(features[name]) - float(local[name])) <= VERIFY_TOLERANCE
        for name in FEATURE_KEYS
    )


def run_scenario(browser, harness, scenario, rng):
    """Drives one scenario and returns every /api/analyze exchange, in send order.

    The request bodies are read from the browser itself, so what is stored is
    byte-for-byte what the SDK sent -- not a reconstruction from the server's
    tables, whose raw channels the retention sweep blanks after an hour.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    requests = []

    def on_request(request):
        if request.method == "POST" and request.url.split("?", 1)[0].endswith("/api/analyze"):
            requests.append(request)

    page.on("request", on_request)
    try:
        page.goto(harness, wait_until="load")
        page.wait_for_function("() => window.DeepCheck && window.__dc")

        scenario(page, rng, None)
        time.sleep(L.FLUSH_MS / 1000.0)
        L.flush_now(page)

        exchanges = []
        for request in requests:
            body = request.post_data_json or {}
            response = request.response()  # waits for an in-flight request
            result = None
            if response is not None and response.ok:
                try:
                    result = response.json()
                except Exception:
                    result = None
            exchanges.append(
                {"body": body, "status": response.status if response else None, "result": result}
            )
        return exchanges
    finally:
        context.close()


def stored_flushes(exchanges, session_id):
    """The exchanges the server stored a BehaviorData row for, in send order.

    A 200 carrying measured_features == 0 is the answer to a flush with no
    behaviour: the server returns the session's score and stores nothing. An
    older server stores such a flush, and so does not answer 0.
    """
    return [
        e
        for e in exchanges
        if e["body"].get("session_id") == session_id
        and e["status"] == 200
        and (e["result"] or {}).get("measured_features", 1) != 0
    ]


def resolve_stamp(existing_payload, existing_samples, stamp):
    """feature_scaling / neutral_defaults for the file header.

    The header describes rows WITHOUT raw telemetry -- the only rows whose
    stored features training has to trust. If the file already holds such
    rows, their header stays exactly as found: this capture did not produce
    them and cannot vouch for them.
    """
    if any(not isinstance(s.get("raw"), dict) for s in existing_samples):
        return {
            key: existing_payload[key]
            for key in ("feature_scaling", "neutral_defaults")
            if key in existing_payload
        }
    previous = {key: existing_payload.get(key) for key in ("feature_scaling", "neutral_defaults")}
    if stamp is not None and all(previous[k] in (None, stamp[k]) for k in previous):
        return stamp
    return {"feature_scaling": None, "neutral_defaults": None}


def dump_dataset(payload, fh):
    """Header readable, one sample per line: the same layout
    backend/record_session.py writes. Raw telemetry indented one value per
    line would make the file several times larger for nothing."""
    header = {key: value for key, value in payload.items() if key != "samples"}
    samples = payload.get("samples", [])
    fh.write("{\n")
    for key, value in header.items():
        fh.write(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},\n")
    fh.write(' "samples": [\n')
    for index, sample in enumerate(samples):
        line = json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
        fh.write(f"  {line}{',' if index < len(samples) - 1 else ''}\n")
    fh.write(" ]\n}\n")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="Gercek tarayici telemetrisi yakala")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--dashboard-key", "--operator-key", dest="dashboard_key",
        default=os.environ.get("DASHBOARD_KEY") or DEV_DASHBOARD_KEY,
        help="backend'in DASHBOARD_KEY degeri (GET /api/score icin)",
    )
    # 3000 is taken by the demo frontend container in this repository.
    parser.add_argument("--port", type=int, default=3100)
    parser.add_argument("--repeats", type=int, default=8, help="runs per scenario")
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument(
        "--append", action="store_true",
        help="merge into the existing dataset instead of replacing it",
    )
    parser.add_argument(
        "--only", default="", help="comma-separated scenario names to capture",
    )
    parser.add_argument(
        "--run-tag", default="", help="suffix for run ids, so appended runs stay distinct",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="do not import backend/ to check the stored features against raw",
    )
    args = parser.parse_args()

    scorer = None if args.no_verify else load_backend_scorer()

    rng = random.Random(args.seed)
    httpd = L.start_static_server(args.port)
    harness = f"http://127.0.0.1:{args.port}/harness.html?api={args.api}"
    samples = []
    counts = {"verified": 0, "mismatch": 0, "unpaired": 0, "idle": 0, "rejected": 0}

    try:
        with sync_playwright() as playwright:
            # bot_lab resolves the browser the same way: use PW_CHROMIUM if it
            # points at a real binary, otherwise let Playwright find the one it
            # installed.
            browser = playwright.chromium.launch(**L.chromium_launch_args(headless=True))
            wanted = {n.strip() for n in args.only.split(",") if n.strip()}
            for name, scenario, label in LABELLED_SCENARIOS:
                if wanted and name not in wanted:
                    continue
                for run in range(args.repeats):
                    exchanges = run_scenario(browser, harness, scenario, rng)
                    counts["rejected"] += sum(1 for e in exchanges if e["status"] != 200)

                    # Normally one server session per run. A token that
                    # expires part-way makes the SDK register again, and the
                    # server starts a new session -- and new smoothing -- there.
                    session_ids = list(
                        dict.fromkeys(e["body"].get("session_id") for e in exchanges if e["body"])
                    )
                    run_samples = 0
                    for session_index, session_id in enumerate(s for s in session_ids if s):
                        stored = stored_flushes(exchanges, session_id)
                        history = fetch_history(args.api, args.dashboard_key, session_id).get("history", [])

                        # Pair stored flushes with history rows by position.
                        # Both are in send order; history keeps only its newest
                        # HISTORY_LIMIT rows. A count that still disagrees
                        # means the pairing cannot be trusted, and the stored
                        # features are left out rather than attached to the
                        # wrong flush -- raw is kept either way.
                        paired = [None] * len(stored)
                        if len(stored) == len(history) or (
                            len(history) == HISTORY_LIMIT and len(stored) > HISTORY_LIMIT
                        ):
                            offset = len(stored) - len(history)
                            for i, row in enumerate(history):
                                paired[offset + i] = row
                        else:
                            counts["unpaired"] += len(stored)
                            print(
                                f"  UYARI: {name} run {run + 1}: sunucu {len(history)} akis sakladi, "
                                f"tarayici {len(stored)} kabul edilen akis gordu; saklanan "
                                "ozellikler bu oturum icin yazilmiyor (ham telemetri yaziliyor)."
                            )

                        for flush_index, (exchange, row) in enumerate(zip(stored, paired)):
                            body = exchange["body"]
                            raw = {channel: body.get(channel) or [] for channel in RAW_CHANNELS}
                            if not carries_behaviour(raw):
                                counts["idle"] += 1
                                continue
                            features = None
                            if row is not None:
                                features = {k: row.get(k) for k in FEATURE_KEYS}
                                if any(v is None for v in features.values()):
                                    features = None
                            if scorer is not None and features is not None:
                                key = "verified" if features_match(scorer, raw, features) else "mismatch"
                                counts[key] += 1
                            sample = {
                                "raw": raw,
                                "features": features,
                                "client_signals": body.get("client_signals") or {},
                                "label": label,
                                "scenario": name,
                                # Every flush from one browser session is
                                # correlated with its siblings. Recording the
                                # run lets the trainer split session-wise; a
                                # random per-flush split would leak the same
                                # session into train and test and inflate the
                                # score.
                                "run_id": f"{name}#{args.run_tag}{run}",
                                "session_index": session_index,
                                # Send order within the session: the conformal
                                # calibration replays the smoothing in it.
                                "flush_index": flush_index,
                            }
                            if body.get("client_sent_at") is not None:
                                sample["client_sent_at"] = body["client_sent_at"]
                            samples.append(sample)
                            run_samples += 1
                    print(
                        f"{name:20s} run {run + 1}/{args.repeats}  "
                        f"flushes={run_samples}  total={len(samples)}"
                    )
            browser.close()
    finally:
        httpd.shutdown()

    stamp = None
    if scorer is not None and counts["verified"] and not counts["mismatch"]:
        bundle = scorer.get_bundle()
        stamp = {
            "feature_scaling": {k: [float(v) for v in bundle.feature_scaling[k]] for k in bundle.feature_scaling},
            "neutral_defaults": {k: float(v) for k, v in bundle.neutral_defaults.items()},
        }
    if counts["mismatch"]:
        print(
            f"\nUYARI: {counts['mismatch']} akista sunucunun sakladigi ozellikler, yerel "
            "backend modeliyle hamdan cikarilanlarla uyusmuyor. Sunucu baska bir model "
            "dosyasiyla calisiyor (orn. konteyner). Baslikta olcek null birakildi; egitim "
            "ham telemetriyi yeniden cikaracagi icin satirlar yine kullanilabilir."
        )

    out_path = Path(args.out)
    existing_payload, existing = {}, []
    if args.append and out_path.exists():
        existing_payload = json.loads(out_path.read_text(encoding="utf-8"))
        existing = existing_payload.get("samples", [])
        print(f"appended to {len(existing)} existing samples")

    payload = {
        "note": (
            "Real Chromium telemetry captured via lab/capture.py. `raw` is the exact "
            "/api/analyze body per flush and is what training re-extracts; "
            "feature_scaling / neutral_defaults describe the stored `features` only."
        ),
        "feature_keys": FEATURE_KEYS,
        **resolve_stamp(existing_payload, existing, stamp),
        # run_id keeps appended runs distinct, so the session-level split still
        # holds across capture sessions.
        "samples": existing + samples,
    }
    with out_path.open("w", encoding="utf-8") as fh:
        dump_dataset(payload, fh)

    humans = sum(1 for s in samples if s["label"] == 0)
    print(f"\n{len(samples)} samples ({humans} human / {len(samples) - humans} bot) -> {args.out}")
    print(
        f"  ozellik dogrulandi: {counts['verified']}, uyusmadi: {counts['mismatch']}, "
        f"eslestirilemedi: {counts['unpaired']}, bos (yalnizca bekleme) akis: {counts['idle']}, "
        f"reddedilen istek: {counts['rejected']}"
    )


if __name__ == "__main__":
    main()
