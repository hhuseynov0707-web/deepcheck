"""DeepCheck adversarial bot lab.

Drives a REAL Chromium browser against the REAL SDK and the REAL backend, so
every number here comes from the same path a production session takes:
browser input events -> sdk/deepcheck.js -> POST /api/analyze -> server-side
POST /api/transaction. Nothing is simulated at the feature level.

This exists because synthetic separation is not evidence. The model is trained
on personas written by the same person who wrote the detector, so high accuracy
on that data is partly self-fulfilling. An attack scripted independently, in a
real browser, is a held-out adversary.

Attack ladder:
    A1 naive        instant scripted clicks and typing, no pointer at all
    A2 randomized   random delays and teleporting pointer jumps
    A3 human_mimic  smoothed pointer paths, variable typing rhythm
    A4 evasive      IID Gaussian jitter -- the transcribed real evasion that
                    once scored 9.1/100 against this API
    A5 adaptive     reads the score back and retunes itself each round

Legitimate baselines (false positives are the failure that matters most for a
payment product):
    H1 human        ballistic pointer motion, natural form-fill rhythm
    H2 keyboard     Tab navigation only, no pointer at all

Reporting note: for the adaptive attack the headline is per-round detection AT
round N, not a cumulative rate. A cumulative "detected by round N" figure rises
monotonically and would still look strong on a run where the attacker was
caught early and then broke through at the end -- which is precisely the
outcome that matters.

Usage:
    # 1. start the API (a scratch DB is fine)
    DEEPCHECK_SECRET=lab DEEPCHECK_OPERATOR_KEY=lab \\
    DEEPCHECK_ALLOWED_ORIGINS=http://127.0.0.1:3000 \\
    uvicorn main:app --port 8000        # from backend/

    # 2. run the lab
    python lab/bot_lab.py --api http://127.0.0.1:8000
"""

import argparse
import functools
import http.server
import json
import math
import os
import random
import socketserver
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LAB_DIR = ROOT / "lab"
SDK_DIR = ROOT / "sdk"

# The SDK flushes on this cadence; scenarios must stay long enough to produce
# several real windows. Left at the production value on purpose -- shortening
# it would change how much evidence each flush carries, and therefore the
# decisions, making the lab measure something the product does not do.
FLUSH_MS = 2000

CARD = "4242424242424242"
NAME = "AHMET YILMAZ"
EXP = "1228"
AMOUNT = 2038.8


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the harness page and the real SDK from one origin.

    Strictly an allowlist. Overriding translate_path() bypasses
    SimpleHTTPRequestHandler's own normalization, which is what strips ``..``
    from a request path -- so mapping the URL onto a directory here (
    ``LAB_DIR / clean``) would reintroduce directory traversal, because
    pathlib does not normalize ``..`` when joining:
    ``LAB_DIR / "../backend/security.py"`` points straight at the backend.
    The lab only ever needs two files, so enumerate them instead of
    reimplementing containment checks.
    """

    _ALLOWED = {
        "": LAB_DIR / "harness.html",
        "harness.html": LAB_DIR / "harness.html",
        "deepcheck.js": SDK_DIR / "deepcheck.js",
    }

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        target = self._ALLOWED.get(clean)
        # Anything else resolves to a path that does not exist, which the base
        # handler turns into a 404.
        return str(target) if target else str(LAB_DIR / "__not_found__")

    def log_message(self, *args):
        pass


class _ReusableServer(socketserver.TCPServer):
    # Must be set on the class: TCPServer binds inside __init__, so assigning
    # it to the instance afterwards is too late and a rerun fails with
    # "Address already in use" while the previous socket is in TIME_WAIT.
    allow_reuse_address = True


def start_static_server(port: int):
    httpd = _ReusableServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


# ---------------------------------------------------------------------------
# Motion primitives
# ---------------------------------------------------------------------------


def ballistic_move(page, x0, y0, x1, y1, rng, steps=None, tremor=1.2):
    """Human-like reach: minimum-jerk velocity profile plus tremor.

    Emits many individual mouse events rather than one jump, because that is
    what a hand produces and what the kinematics features measure. Playwright's
    own `steps=` interpolates linearly (constant velocity), which is a *bot*
    signature -- so the human baseline cannot use it.
    """
    steps = steps or rng.randint(9, 16)
    for i in range(1, steps + 1):
        p = i / steps
        s = 10 * p**3 - 15 * p**4 + 6 * p**5
        x = x0 + (x1 - x0) * s + rng.gauss(0, tremor)
        y = y0 + (y1 - y0) * s + rng.gauss(0, tremor)
        page.mouse.move(x, y)
        time.sleep(rng.uniform(0.012, 0.028))
    return x1, y1


def jitter_move(page, x0, y0, n, rng):
    """IID Gaussian random walk -- the transcribed evasion's motion model.

    Every step is drawn independently, so the path has human-looking marginal
    statistics but no momentum and no target. This is what defeated the
    detector before the kinematics features existed.
    """
    x, y = x0, y0
    for _ in range(n):
        x += rng.gauss(6, 6)
        y += rng.gauss(4, 5)
        page.mouse.move(x, y)
        time.sleep(rng.uniform(0.05, 0.15))
    return x, y


def type_human(page, selector, text, rng, sigma=0.5, mean_ms=110):
    page.click(selector) if False else None
    for ch in text:
        page.keyboard.type(ch)
        delay = min(rng.lognormvariate(math.log(mean_ms), sigma), 900) / 1000.0
        time.sleep(delay)
        if rng.random() < 0.05:
            time.sleep(rng.uniform(0.4, 1.1))


def type_fixed(page, text, delay_ms):
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(delay_ms / 1000.0)


def field_center(page, selector):
    box = page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


# ---------------------------------------------------------------------------
# Scenarios. Each returns nothing; the runner measures the outcome.
# ---------------------------------------------------------------------------


def scenario_human(page, rng, params=None):
    """H1: a real person filling the form."""
    x, y = 200.0, 150.0
    for selector, text in (("#card", CARD), ("#name", NAME), ("#exp", EXP)):
        tx, ty = field_center(page, selector)
        x, y = ballistic_move(page, x, y, tx, ty, rng)
        time.sleep(rng.uniform(0.08, 0.25))       # settle before clicking
        page.mouse.click(x, y)
        time.sleep(rng.uniform(0.25, 0.7))        # hand moves to the keyboard
        type_human(page, selector, text, rng)
        time.sleep(rng.uniform(0.25, 0.7))        # hand moves back
    time.sleep(1.0)


def scenario_keyboard_only(page, rng, params=None):
    """H2: Tab navigation, no pointer at all. Legitimate and common."""
    page.keyboard.press("Tab")
    for text in (CARD, NAME, EXP):
        time.sleep(rng.uniform(0.2, 0.6))
        type_human(page, None, text, rng, sigma=0.45)
        time.sleep(rng.uniform(0.3, 0.8))
        page.keyboard.press("Tab")
    time.sleep(1.0)


def scenario_naive(page, rng, params=None):
    """A1: form filled by script. No pointer, near-zero delays."""
    for selector, text in (("#card", CARD), ("#name", NAME), ("#exp", EXP)):
        page.focus(selector)
        type_fixed(page, text, 3)
    time.sleep(1.0)


def scenario_randomized(page, rng, params=None):
    """A2: random delays and teleporting pointer jumps."""
    for selector, text in (("#card", CARD), ("#name", NAME), ("#exp", EXP)):
        page.mouse.move(rng.uniform(50, 400), rng.uniform(50, 400))  # teleport
        page.focus(selector)
        for ch in text:
            page.keyboard.type(ch)
            time.sleep(rng.uniform(0.02, 0.18))
        time.sleep(rng.uniform(0.1, 0.4))
    time.sleep(1.0)


def scenario_human_mimic(page, rng, params=None):
    """A3: smoothed (but linearly interpolated) paths and varied typing."""
    for selector, text in (("#card", CARD), ("#name", NAME), ("#exp", EXP)):
        tx, ty = field_center(page, selector)
        page.mouse.move(tx, ty, steps=rng.randint(12, 22))  # constant velocity
        page.mouse.click(tx, ty)
        time.sleep(rng.uniform(0.1, 0.3))
        for ch in text:
            page.keyboard.type(ch)
            time.sleep(rng.uniform(0.06, 0.22))
        time.sleep(rng.uniform(0.15, 0.4))
    time.sleep(1.0)


def scenario_evasive(page, rng, params=None):
    """A4: the transcribed real evasion -- IID jitter + copied typing rhythm."""
    x, y = 200.0, 200.0
    x, y = jitter_move(page, x, y, 30, rng)
    for selector, text in (("#card", CARD), ("#name", NAME), ("#exp", EXP)):
        page.focus(selector)
        time.sleep(rng.uniform(0.3, 0.9))
        for ch in text:
            page.keyboard.type(ch)
            time.sleep(min(rng.lognormvariate(math.log(110), 0.5), 900) / 1000.0)
        x, y = jitter_move(page, x, y, 8, rng)
    time.sleep(1.0)


def scenario_adaptive(page, rng, params):
    """A5: same shape as A4 but retuned from the score it observed last round.

    Models the strongest realistic attacker: one with full feedback, who can
    see the risk score their own session produced and change tactics. Each
    round it smooths its motion, lengthens its pauses and slows its typing
    toward human ranges.
    """
    smooth = params.get("smooth", 0.0)          # 0 = pure jitter, 1 = ballistic
    pause = params.get("pause", 0.0)            # extra hesitation, seconds
    x, y = 200.0, 200.0

    for selector, text in (("#card", CARD), ("#name", NAME), ("#exp", EXP)):
        tx, ty = field_center(page, selector)
        if rng.random() < smooth:
            x, y = ballistic_move(page, x, y, tx, ty, rng)
            time.sleep(rng.uniform(0.08, 0.25))
            page.mouse.click(x, y)
        else:
            x, y = jitter_move(page, x, y, 10, rng)
            page.focus(selector)
        time.sleep(rng.uniform(0.2, 0.6) + pause)
        for ch in text:
            page.keyboard.type(ch)
            time.sleep(min(rng.lognormvariate(math.log(90 + 40 * smooth), 0.35 + 0.2 * smooth), 900) / 1000.0)
        time.sleep(rng.uniform(0.15, 0.45) + pause)
    time.sleep(1.0)


SCENARIOS = {
    "H1_human": (scenario_human, "İnsan (meşru)"),
    "H2_keyboard_only": (scenario_keyboard_only, "Yalnızca klavye (meşru)"),
    "A1_naive": (scenario_naive, "Naif bot"),
    "A2_randomized": (scenario_randomized, "Rastgeleleştirilmiş bot"),
    "A3_human_mimic": (scenario_human_mimic, "İnsan taklidi bot"),
    "A4_evasive": (scenario_evasive, "Kaçırmacı bot (gerçek saldırı)"),
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_one(browser, harness_url, scenario_fn, rng, params=None, settle_flushes=2):
    """Runs one scenario in a fresh browser context and returns the outcome."""
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.goto(harness_url, wait_until="load")
    page.wait_for_function("() => window.DeepCheck && window.__dc")

    started = time.time()
    scenario_fn(page, rng, params)

    # Let the periodic flushes land, then force the final window to be scored
    # before submitting -- the event-triggered flush a checkout should do.
    time.sleep(FLUSH_MS / 1000.0 * settle_flushes)
    page.evaluate("() => window.__flush()")
    page.wait_for_timeout(900)

    last = page.evaluate("() => window.__dc.last")
    decision = page.evaluate(f"async () => await window.__pay({AMOUNT})")
    elapsed = time.time() - started

    context.close()
    return {
        "risk_score": (last or {}).get("risk_score"),
        "label": (last or {}).get("label"),
        "evidence_state": (decision or {}).get("evidence_state_label"),
        "signal_sufficiency": (last or {}).get("signal_sufficiency"),
        "decision": (decision or {}).get("decision"),
        "reasons": (decision or {}).get("reason_codes", {}),
        "updates": page_updates(last),
        "seconds": round(elapsed, 1),
    }


def page_updates(last):
    return 0 if last is None else 1


def detected(outcome):
    """An attack is stopped if the server did not approve it outright."""
    return outcome["decision"] != "onaylandi"


def main():
    parser = argparse.ArgumentParser(description="DeepCheck adversarial bot lab")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=3000, help="harness origin port")
    parser.add_argument("--rounds", type=int, default=4, help="adaptive attack rounds")
    parser.add_argument("--repeat", type=int, default=1, help="repeats per scenario")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=str(LAB_DIR / "report.json"))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    httpd = start_static_server(args.port)
    harness_url = f"http://127.0.0.1:{args.port}/harness.html?api={args.api}"
    results = {"scenarios": [], "adaptive_rounds": []}

    chromium_path = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium")
    launch = {"headless": not args.headed}
    if Path(chromium_path).exists():
        launch["executable_path"] = chromium_path

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            print(f"{'senaryo':22s} {'risk':>6s} {'karar':>18s}  {'kanıt durumu'}")
            print("-" * 76)

            for key, (fn, label) in SCENARIOS.items():
                for _ in range(args.repeat):
                    out = run_one(browser, harness_url, fn, rng)
                    out.update({"scenario": key, "label": label})
                    results["scenarios"].append(out)
                    print(
                        f"{key:22s} {str(out['risk_score']):>6s} "
                        f"{str(out['decision']):>18s}  {out['evidence_state']}"
                    )

            # A5: adaptive. Each round the attacker sees its score and retunes.
            print(f"\n{'A5 adaptif tur':22s} {'risk':>6s} {'karar':>18s}  {'tespit'}")
            print("-" * 76)
            params = {"smooth": 0.0, "pause": 0.0}
            for rnd in range(1, args.rounds + 1):
                out = run_one(browser, harness_url, scenario_adaptive, rng, params)
                out.update({"scenario": "A5_adaptive", "round": rnd, "params": dict(params)})
                results["adaptive_rounds"].append(out)
                print(
                    f"tur {rnd:<18d} {str(out['risk_score']):>6s} "
                    f"{str(out['decision']):>18s}  {'EVET' if detected(out) else 'HAYIR — GEÇTİ'}"
                )
                # Retune toward human ranges using the observed score.
                if out["risk_score"] is not None and out["risk_score"] >= 40:
                    params["smooth"] = min(1.0, params["smooth"] + 0.35)
                    params["pause"] = min(0.8, params["pause"] + 0.2)

            browser.close()
    finally:
        httpd.shutdown()

    summarize(results)
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nJSON raporu: {args.out}")


def summarize(results):
    attacks = [r for r in results["scenarios"] if r["scenario"].startswith("A")]
    humans = [r for r in results["scenarios"] if r["scenario"].startswith("H")]

    print("\n" + "=" * 76)
    print("ÖZET")
    print("=" * 76)

    if attacks:
        stopped = sum(1 for a in attacks if detected(a))
        print(f"Saldırı senaryoları durduruldu : {stopped}/{len(attacks)}")
    if humans:
        wrongly = [h for h in humans if h["decision"] == "reddedildi"]
        print(f"Meşru oturum yanlışlıkla engellendi: {len(wrongly)}/{len(humans)}")
        for h in humans:
            print(f"   {h['scenario']:20s} risk={h['risk_score']} karar={h['decision']}")

    rounds = results["adaptive_rounds"]
    if rounds:
        print("\nAdaptif saldırı — tur başına tespit (kümülatif DEĞİL):")
        for r in rounds:
            print(
                f"   tur {r['round']}: risk={r['risk_score']} "
                f"karar={r['decision']} tespit={'evet' if detected(r) else 'HAYIR'}"
            )
        broke = [r["round"] for r in rounds if not detected(r)]
        print(
            "   Saldırgan hiçbir turda geçemedi."
            if not broke
            else f"   DİKKAT: {broke} turlarında geçti."
        )


if __name__ == "__main__":
    main()
