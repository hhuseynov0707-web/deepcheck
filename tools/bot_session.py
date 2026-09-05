"""Drives the demo checkout with Playwright, to collect labelled BOT sessions.

This is the automated half of the evaluation set. It deliberately ships two
variants, because "bot" is not one behavior:

    naive   -- fills the form by setting values and clicking directly, with
               no mouse path and no waiting. This is what a scraper or a
               card-testing script actually looks like.
    jitter  -- moves the mouse along a path, varies its typing delays and
               pauses between fields. This is the automation that tries to
               look human, and it is the one worth measuring against.

Install and run:

    pip install playwright
    playwright install chromium
    python tools/bot_session.py --variant naive  --runs 15
    python tools/bot_session.py --variant jitter --runs 15

Each run leaves a session in Postgres. Freeze them afterwards with:

    cd backend && python record_session.py --label bot --since <start-time>

NOTE: not exercised in CI and not part of `docker-compose up` -- it needs a
browser installed on the host and the demo already running.
"""

import argparse
import random
import sys
import time

CARD = "4242424242424242"
NAME = "TEST KULLANICI"
EXPIRY = "1230"
CVV = "123"


def _fill_naive(page, dwell_ms: int) -> None:
    """No mouse, no rhythm: the shape of a headless script."""
    inputs = page.locator("form input")
    for index, value in enumerate([CARD, NAME, EXPIRY, CVV]):
        inputs.nth(index).fill(value)
    page.wait_for_timeout(dwell_ms)
    page.locator("form button[type=submit]").click()


def _fill_jitter(page, dwell_ms: int) -> None:
    """Mouse path, variable keystroke delay, pauses between fields."""
    box = page.locator("form").bounding_box() or {"x": 200, "y": 200, "width": 300, "height": 400}
    x, y = box["x"], box["y"]
    for _ in range(25):
        x += random.uniform(-18, 22)
        y += random.uniform(-12, 20)
        page.mouse.move(x, y)
        page.wait_for_timeout(random.randint(40, 130))

    inputs = page.locator("form input")
    for index, value in enumerate([CARD, NAME, EXPIRY, CVV]):
        field = inputs.nth(index)
        field.click()
        page.wait_for_timeout(random.randint(150, 600))
        field.type(value, delay=random.randint(70, 190))

    page.wait_for_timeout(dwell_ms + random.randint(200, 900))
    page.locator("form button[type=submit]").click()


VARIANTS = {"naive": _fill_naive, "jitter": _fill_jitter}


def run(url: str, variant: str, runs: int, dwell_ms: int, headless: bool) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright kurulu degil. Kurulum:\n"
            "  pip install playwright\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        return 1

    fill = VARIANTS[variant]
    started = time.strftime("%Y-%m-%dT%H:%M:%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        for run_index in range(1, runs + 1):
            page = browser.new_page()
            page.goto(url)
            # The SDK flushes every 2s; give it a few windows of behavior so
            # the session has a real sequence rather than a single flush.
            fill(page, dwell_ms)
            page.wait_for_timeout(dwell_ms)
            print(f"[{run_index}/{runs}] {variant} oturumu tamamlandi")
            page.close()
        browser.close()

    print(
        f"\nBitti. Bu oturumlari kaydetmek icin:\n"
        f"  cd backend && python record_session.py --label bot --since {started}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot oturumlari uretir (Playwright).")
    parser.add_argument("--url", default="http://localhost:3000/demo")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="naive")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--dwell-ms", type=int, default=6000, help="Sayfada kalinacak sure")
    parser.add_argument("--headed", action="store_true", help="Tarayiciyi gorunur calistir")
    args = parser.parse_args()
    return run(args.url, args.variant, args.runs, args.dwell_ms, not args.headed)


if __name__ == "__main__":
    sys.exit(main())
