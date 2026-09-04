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

Usage (backend must be running, see bot_lab.py):
    python lab/capture.py --api http://127.0.0.1:8000 --repeats 8
"""

import argparse
import json
import random
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

import bot_lab as L

OUT_PATH = L.LAB_DIR / "real_telemetry.json"

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


def fetch_history(api, operator_key, session_id):
    request = urllib.request.Request(
        f"{api}/api/score/{session_id}", headers={"X-Operator-Key": operator_key}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser(description="Capture real-browser telemetry")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--operator-key", default="lab")
    parser.add_argument("--port", type=int, default=3000)
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
    args = parser.parse_args()

    rng = random.Random(args.seed)
    httpd = L.start_static_server(args.port)
    harness = f"http://127.0.0.1:{args.port}/harness.html?api={args.api}"
    samples = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path="/opt/pw-browsers/chromium"
            )
            wanted = {n.strip() for n in args.only.split(",") if n.strip()}
            for name, scenario, label in LABELLED_SCENARIOS:
                if wanted and name not in wanted:
                    continue
                for run in range(args.repeats):
                    context = browser.new_context(viewport={"width": 1280, "height": 800})
                    page = context.new_page()
                    page.goto(harness, wait_until="load")
                    page.wait_for_function("() => window.DeepCheck && window.__dc")

                    scenario(page, rng, None)
                    time.sleep(L.FLUSH_MS / 1000.0)
                    page.evaluate("() => window.__flush()")
                    page.wait_for_timeout(900)

                    session_id = page.evaluate("() => window.DeepCheck.getSessionId()")
                    context.close()

                    if not session_id:
                        continue
                    detail = fetch_history(args.api, args.operator_key, session_id)
                    for row in detail.get("history", []):
                        # A flush with no measurable channel at all carries no
                        # information for either class; keep everything else,
                        # including thin flushes, because those are exactly the
                        # sparse cases the detector must handle.
                        features = {k: row.get(k) for k in FEATURE_KEYS}
                        if any(v is None for v in features.values()):
                            continue
                        samples.append(
                            {
                                "features": features,
                                "label": label,
                                "scenario": name,
                                # Every flush from one browser session is
                                # correlated with its siblings. Recording the
                                # run lets the trainer split session-wise; a
                                # random per-flush split would leak the same
                                # session into train and test and inflate the
                                # score.
                                "run_id": f"{name}#{args.run_tag}{run}",
                            }
                        )
                    print(
                        f"{name:20s} run {run + 1}/{args.repeats}  "
                        f"flushes={len(detail.get('history', []))}  total={len(samples)}"
                    )
            browser.close()
    finally:
        httpd.shutdown()

    out_path = Path(args.out)
    if args.append and out_path.exists():
        existing = json.loads(out_path.read_text()).get("samples", [])
        # run_id keeps appended runs distinct, so the session-level split still
        # holds across capture sessions.
        samples = existing + samples
        print(f"appended to {len(existing)} existing samples")

    payload = {
        "note": "Real Chromium telemetry captured via lab/capture.py",
        "feature_keys": FEATURE_KEYS,
        "samples": samples,
    }
    out_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    humans = sum(1 for s in samples if s["label"] == 0)
    print(f"\n{len(samples)} samples ({humans} human / {len(samples) - humans} bot) -> {args.out}")


if __name__ == "__main__":
    main()
