"""Tests for the stage bot, lab/live_bot.py.

Two things are under test, and they are kept apart on purpose:

1. The behaviour the bot SENDS is the one that was measured (module docstring:
   200/200 offline, 20/20 live on the old path). Nothing in the store change
   may move it, so its output for a fixed seed is pinned below. The hashes were
   taken from the committed file (HEAD 6c8ed5a) before the store flow was
   added. If one of these assertions fails, the bot no longer sends what was
   measured, and every number in its docstring and in docs/canli-demo.md is
   void until it is measured again -- fix the bot, do not re-pin the hash.

2. The two flows (store and legacy) against fake servers on 127.0.0.1: which
   paths are called, what the checkout body carries, and which exit code each
   answer maps to. Exit 1 must mean exactly "the payment went through".

Standard library and pytest only. No real sleeping: the bot's clock is swapped
for a virtual one, so its eight 2 s flushes run in milliseconds.
"""
import hashlib
import json
import random
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bot

# --- 1. the measured behaviour --------------------------------------------------

PINNED_CONSTANTS = [2000, 10000, 400, 80, 2000, [630, 576], 2038.8]
PINNED_TIMELINE_SHA256 = "c249d9ec4a3b2f3ca1fb1f77947a1278db0f41b177d008b77c9cad7bd1165335"
PINNED_FLUSHES_SHA256 = "a2d1a0dea1c7b482f8beabd4910b86f7bb4e78f701945928f3fc5d9b25b0f542"


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def test_measured_constants_are_unchanged():
    assert [
        live_bot.FLUSH_INTERVAL_MS, live_bot.ROLLING_WINDOW_MS, live_bot.HESITATION_THRESHOLD_MS,
        live_bot.MOVE_EVERY_MS, live_bot.ATTEMPT_EVERY_MS, list(live_bot.CVV), live_bot.DEMO_AMOUNT,
    ] == PINNED_CONSTANTS


def test_measured_timeline_and_flushes_are_unchanged():
    timeline = live_bot.script_timeline(1_000_000, 9 * live_bot.FLUSH_INTERVAL_MS, random.Random(20261001))
    assert _sha(timeline) == PINNED_TIMELINE_SHA256
    window = live_bot.SdkWindow(timeline)
    # Flush times a little off the 2 s grid, as a real sleep lands.
    flushes = [window.flush(1_000_000 + 200 + i * live_bot.FLUSH_INTERVAL_MS + (i % 3)) for i in range(1, 9)]
    assert _sha(flushes) == PINNED_FLUSHES_SHA256


# --- 2. the flows, against fake servers --------------------------------------------

class VirtualTime:
    """Stands in for the time module inside live_bot: sleep advances a clock."""

    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


DROP = object()  # a route that closes the connection without answering
HTML = "<!doctype html><title>SPA</title>"


def _core_routes(prefix: str) -> dict:
    return {
        ("GET", prefix + "/api/health"): (200, {"status": "healthy", "model_loaded": True}),
        ("POST", prefix + "/api/session"): (201, {"session_id": "s-0123456789", "challenge": "c.sig",
                                                  "difficulty_bits": 4}),
        ("POST", prefix + "/api/session/attest"): (201, {"session_id": "s-0123456789", "token": "tok-abc"}),
        ("POST", prefix + "/api/analyze"): (200, {"risk_score": 94.6, "label": "Bot Tespit Edildi"}),
    }


# What the core answers through checkout-web, whose nginx sets
# X-DeepCheck-Reply: ack on every SDK call (apps/checkout/nginx.conf).
STORE_ANALYZE_ACK = (200, {"session_id": "s-0123456789", "accepted": True})


def store_routes(checkout_answer) -> dict:
    routes = _core_routes(live_bot.STORE_SDK_PREFIX)
    routes[("POST", live_bot.STORE_SDK_PREFIX + "/api/analyze")] = STORE_ANALYZE_ACK
    routes.update({
        ("GET", "/api/health"): (200, {"status": "sağlıklı", "core": True}),
        ("GET", "/api/cart"): (200, {"items": [{"name": "Klavye", "unit_price": 1699.0}], "subtotal": 1699.0,
                                     "vat": 339.8, "total": 2038.8, "currency": "TRY"}),
        ("POST", "/api/checkout"): checkout_answer,
    })
    return routes


def legacy_routes(charge_answer) -> dict:
    routes = _core_routes("")
    # The legacy frontend's nginx answers any unknown path with index.html.
    routes[("GET", "/deepcheck/api/health")] = (200, HTML)
    routes[("POST", "/api/demo/charge")] = charge_answer
    return routes


class FakeServer:
    def __init__(self, routes: dict):
        self.routes = routes
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep pytest output clean
                pass

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                # Lower-cased: urllib sends "X-deepcheck-token" (str.capitalize).
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append((self.command, self.path, headers, body))
                route = outer.routes.get((self.command, self.path))
                if route is DROP:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if route is None:
                    status, payload = 404, {"detail": "Not Found"}
                else:
                    status, payload = route
                if isinstance(payload, str):
                    data, ctype = payload.encode(), "text/html"
                else:
                    data, ctype = json.dumps(payload).encode(), "application/json"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _answer
            do_POST = _answer

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        # A short poll so shutdown() returns quickly between tests.
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02},
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def paths(self, method=None):
        return [p for m, p, _, _ in self.requests if method is None or m == method]


@pytest.fixture(autouse=True)
def virtual_clock(monkeypatch):
    monkeypatch.setattr(live_bot, "time", VirtualTime())


def run(server: FakeServer, *extra: str) -> int:
    return live_bot.main(["--url", server.url, *extra])


# Store flow -----------------------------------------------------------------------

def test_store_declined_is_exit_0_and_sends_the_contract_body(capsys):
    with FakeServer(store_routes((200, {"status": "declined"}))) as server:
        code = run(server)
    out = capsys.readouterr().out
    assert code == 0
    assert "ÖDEME REDDEDİLDİ - satıcı sebep söylemez; sebep yalnızca SOC panelinde" in out

    # The SDK protocol goes through checkout-web's /deepcheck/ prefix, every
    # flush with the session token; nothing touches the old demo endpoint.
    posts = server.paths("POST")
    assert posts[:2] == ["/deepcheck/api/session", "/deepcheck/api/session/attest"]
    assert posts.count("/deepcheck/api/analyze") == 8
    assert posts[-1] == "/api/checkout"
    assert "/api/demo/charge" not in posts and "/api/session" not in posts
    assert "/api/cart" in server.paths("GET")
    analyze_headers = [h for m, p, h, _ in server.requests if p == "/deepcheck/api/analyze"]
    assert analyze_headers and all(h.get("x-deepcheck-token") == "tok-abc" for h in analyze_headers)

    checkout = next(b for m, p, _, b in server.requests if p == "/api/checkout")
    # No amount (the store's own), and no e-mail: a guest checkout, like the page.
    assert set(checkout) == {"session_id", "token", "card"}
    assert checkout["session_id"] == "s-0123456789" and checkout["token"] == "tok-abc"
    assert checkout["card"] == {"last4": "0366", "brand": "visa", "exp_month": 9, "exp_year": 2028}


def test_store_windows_print_no_score(capsys):
    """The store tells the payer nothing about their score, so the console in
    front of the jury does not either: a line per window, and no number."""
    with FakeServer(store_routes((200, {"status": "declined"}))) as server:
        assert run(server) == 0
    out = capsys.readouterr().out
    assert [f"  pencere {i}: gönderildi" for i in range(1, 9)] == [
        line for line in out.splitlines() if line.startswith("  pencere ")
    ]
    assert "risk skoru" not in out.lower()
    assert "Bot Tespit Edildi" not in out
    assert "Uyarı" not in out


def test_store_reply_that_still_carries_a_score_is_not_printed(capsys):
    """An image from before the acknowledgement reply still answers with the
    score. The console must not show it, and must say the store leaks it, so a
    rehearsal finds the stale image before the jury does."""
    routes = store_routes((200, {"status": "declined"}))
    routes[("POST", live_bot.STORE_SDK_PREFIX + "/api/analyze")] = (
        200, {"risk_score": 94.6, "label": "Bot Tespit Edildi"})
    with FakeServer(routes) as server:
        assert run(server) == 0
    out = capsys.readouterr().out
    assert out.count(": gönderildi") == 8
    assert "94.6" not in out and "Bot Tespit Edildi" not in out
    assert "Uyarı: mağaza, pencere yanıtlarında risk skorunu hâlâ tarayıcıya döndürüyor" in out


def test_store_checkout_sends_no_email():
    with FakeServer(store_routes((200, {"status": "declined"}))) as server:
        run(server, "--flushes", "1")
    bodies = [b for m, p, _, b in server.requests if p == "/api/checkout"]
    assert len(bodies) == 1 and "email" not in bodies[0]


def test_store_paid_is_the_only_exit_1(capsys):
    with FakeServer(store_routes((200, {"status": "paid", "order_id": "TS-ABC", "amount": 2038.8,
                                        "last4": "0366", "brand": "visa"}))) as server:
        code = run(server)
    out = capsys.readouterr().out
    assert code == 1
    assert "ÖDEME ALINDI" in out and "TS-ABC" in out


def test_store_requires_action_is_exit_0_and_not_called_a_detection(capsys):
    with FakeServer(store_routes((200, {"status": "requires_action", "challenge": "otp"}))) as server:
        code = run(server)
    out = capsys.readouterr().out
    assert code == 0
    assert "EK DOĞRULAMA İSTENDİ - bot kodu bilmiyor" in out
    # The store gives no reason, so the console must not invent one.
    assert "yetersiz" in out and "SOC panelinde" in out


@pytest.mark.parametrize("answer, fragment", [
    ((503, {"status": "error"}), "bu bir engelleme değil"),
    ((429, {"status": "error", "error": "rate_limited"}), "HTTP 429"),
    ((422, {"status": "error", "error": "invalid_request", "fields": ["card.exp_year"]}), "card.exp_year"),
    ((200, {"status": "something_new"}), "tanımlanmamış"),
    ((200, HTML), "beklenmeyen"),
])
def test_store_errors_are_exit_2_never_a_result(capsys, answer, fragment):
    with FakeServer(store_routes(answer)) as server:
        code = run(server)
    assert code == 2
    assert fragment in capsys.readouterr().out


def test_store_connection_dropped_at_checkout_is_exit_2(capsys):
    with FakeServer(store_routes(DROP)) as server:
        code = run(server)
    out = capsys.readouterr().out
    assert code == 2
    assert "Bağlantı koptu" in out and "SOC" in out


def test_store_server_down_stops_before_a_session_is_spent(capsys):
    routes = store_routes((200, {"status": "declined"}))
    routes[("GET", "/api/health")] = (502, HTML)  # checkout-api not running behind nginx
    with FakeServer(routes) as server:
        code = run(server)
    assert code == 2
    assert server.paths("POST") == []
    assert "CHECKOUT_MERCHANT_ID" in capsys.readouterr().out


def test_store_that_cannot_reach_the_core_is_exit_2(capsys):
    routes = store_routes((200, {"status": "declined"}))
    routes[("GET", "/api/health")] = (200, {"status": "çekirdeğe ulaşılamıyor", "core": False})
    with FakeServer(routes) as server:
        code = run(server)
    assert code == 2
    assert server.paths("POST") == []


def test_store_with_core_still_training_is_exit_2(capsys):
    routes = store_routes((200, {"status": "declined"}))
    routes[("GET", "/deepcheck/api/health")] = (502, HTML)
    with FakeServer(routes) as server:
        code = run(server)
    assert code == 2
    assert "Sunucu hazır değil (HTTP 502)" in capsys.readouterr().out


# Legacy flow ------------------------------------------------------------------------

LEGACY_BLOCK = (200, {"status": "declined", "decision": {"action": "block", "reason": "score",
                                                          "risk_score": 94.8, "label": "Bot Tespit Edildi"}})


def test_legacy_is_detected_behind_an_spa_fallback(capsys):
    with FakeServer(legacy_routes(LEGACY_BLOCK)) as server:
        code = run(server)
    out = capsys.readouterr().out
    assert code == 0
    assert "ENGELLENDİ - ödeme alınmadı" in out
    posts = server.paths("POST")
    assert posts[:2] == ["/api/session", "/api/session/attest"]
    assert posts.count("/api/analyze") == 8
    assert posts[-1] == "/api/demo/charge"
    charge = next(b for m, p, _, b in server.requests if p == "/api/demo/charge")
    assert charge == {"session_id": "s-0123456789", "amount": live_bot.DEMO_AMOUNT}


def test_legacy_keeps_printing_each_window_score(capsys):
    """The old flow's console is the one it always had: the core, asked
    directly, gives the full reply, and the score per window is shown."""
    with FakeServer(legacy_routes(LEGACY_BLOCK)) as server:
        assert run(server, "--legacy") == 0
    out = capsys.readouterr().out
    assert len(re.findall(r"^  pencere [1-8]: risk skoru  94\.6  Bot Tespit Edildi$", out, re.M)) == 8
    assert "gönderildi" not in out and "Uyarı" not in out


def test_legacy_is_detected_at_the_core_port(capsys):
    # The core at :8000 answers /deepcheck/api/health with its own JSON 404.
    routes = legacy_routes(LEGACY_BLOCK)
    del routes[("GET", "/deepcheck/api/health")]
    with FakeServer(routes) as server:
        code = run(server)
    assert code == 0
    assert "/api/demo/charge" in server.paths("POST")


def test_legacy_charged_is_exit_1(capsys):
    charged = (200, {"status": "charged", "decision": {"action": "allow", "reason": "score",
                                                        "risk_score": 12.0, "label": "Gerçek Kullanıcı"}})
    with FakeServer(legacy_routes(charged)) as server:
        assert run(server, "--legacy") == 1


def test_legacy_flag_against_the_store_refuses_without_a_session(capsys):
    with FakeServer(store_routes((200, {"status": "declined"}))) as server:
        code = run(server, "--legacy")
    assert code == 2
    assert server.paths("POST") == []
    assert "--legacy" in capsys.readouterr().out


def test_nothing_listening_is_exit_2(capsys):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert live_bot.main(["--url", f"http://127.0.0.1:{port}"]) == 2
    assert "Sunucuya ulaşılamadı" in capsys.readouterr().out


@pytest.mark.parametrize("argv, expected", [
    ([], "http://127.0.0.1:3000"),
    (["--legacy"], "http://127.0.0.1:8000"),
    (["--url", "http://localhost:3000/odeme"], "http://127.0.0.1:3000"),
])
def test_default_targets(monkeypatch, argv, expected):
    seen = []

    class NoNetwork:
        def __init__(self, base):
            seen.append(base)
            self.base = base

        def call(self, *args, **kwargs):
            return 0, {"detail": "test"}

    monkeypatch.setattr(live_bot, "Api", NoNetwork)
    assert live_bot.main(argv) == 2
    assert seen == [expected]
