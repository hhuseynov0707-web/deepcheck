"""Stage bot for the live jury demo: a script that attacks the store's checkout
straight through the network, from a command prompt.

What it is. A card-testing style loop that never opens a browser. It speaks the
SDK's own protocol -- session, the proof of work, attest -- then posts the
behaviour of a scripted form fill in the SDK's exact flush format
(sdk/deepcheck.js: a 10 s rolling window sent every 2 s), and finally asks for
the payment. The core scores it with the same model and decides with the same
code as any session.

Two targets (docs/architecture-two-apps.md):
  - the store, the default: TechStore's checkout at http://<IP>:3000. The SDK
    protocol goes to /deepcheck/api/session, /deepcheck/api/session/attest and
    /deepcheck/api/analyze (checkout-web's proxy to the core; same bodies as
    the core's /api/...), the cart comes from GET /api/cart, and the payment is
    POST /api/checkout on the store's own server. That server asks the core
    POST /api/decision with its merchant key and answers paid, declined or
    requires_action -- never a score, a label or a reason. The SDK calls'
    replies carry none either: checkout-web's nginx asks the core for an
    acknowledgement (X-DeepCheck-Reply: ack), so each window prints only
    "gönderildi". The store is chosen automatically whenever
    /deepcheck/api/health answers JSON.
  - --legacy: the former combined demo's flow (that demo was removed on
    2026-10-02), POST /api/... and POST /api/demo/charge, straight against
    the core at http://localhost:8000 (loopback only). Also chosen
    automatically when the address has no /deepcheck/api/ but answers the
    core's /api/health. Its console output is the one it always had.
The behaviour sent is the same in both modes: script_timeline and SdkWindow
below are shared, and neither knows which mode it is in.

What it is NOT. Evidence about real bots. Its parameters are taken from the
model's own training "bot" persona in train_model.py: the mousemove step of
_background_motion (every 80+-10 ms, x += 5+-3, y += 2+-2.2), a 150+-8 ms
pause after a click (in training that gap separates consecutive clicks), and
the 1-4 ms key gap of _phase_bot's headless variant. The window composition
differs from a training window: a continuous pointer stream (~125 points per
10 s window where a training window carries 10-20), one click per 2 s, no
scroll. The model saw behaviour generated with these parameters in training,
so blocking it shows the pipeline working end to end -- not that the model
generalises. How many real
card-testing bots behave like this has NOT been measured. It was chosen so the
stage outcome does not hinge on luck:
  - offline: this file's own timeline through scorer.compute_risk and main.py's
    decision rules, with the HOST bundle backend/model-sklearn1.8.0.pkl (host
    scikit-learn 1.8.0, trained 2026-09-25): 200 random runs of 8 flushes, 200
    blocked, lowest session score 93.2, median 94.8;
  - live, on the OLD path only (POST /api/demo/charge): the running stack
    serves backend/model-sklearn1.5.0.pkl, the scikit-learn 1.5.0 build of the
    same training. 20 of 20 runs against http://localhost:8000 were blocked,
    decision-time score 94.3-95.8 (2026-10-01); 3 later runs, through the
    former demo frontend's nginx proxy (then on :3000; removed since) and from
    cmd.exe, were blocked too (93.8, 93.9, 95.1).
  - the store path (/deepcheck/api/... + POST /api/checkout), against the
    running two-app stack on laptop A through http://127.0.0.1:3000
    (2026-10-01, same container bundle): 11 of 11 runs were declined by the
    store; every one is a core `block` recorded under merchant demo-magaza,
    decision-time score 93.8-95.5. The store asks the same /api/decision
    about the same behaviour, its customer reference can only turn an allow
    or a warn into a verify (never touch a block), and its hidden re-ask (up
    to 3 times, 2 s apart, on insufficient evidence) adds no behaviour,
    because this script sends nothing while it waits.
The known gap, measured on the same bot timeline with the mousemove stream
taken out (clicks plus 1-4 ms key gaps): 6 pointer-less settings x 20 runs,
offline with the host bundle, were APPROVED in 111 of 120 (9 undecided), and
real Chromium typing without moving the pointer was approved 2 of 2 against the
live stack. Bots that imitate people pass too (docs/evaluation.md). Say so if
the jury asks; do not try those on stage. The runbook, with these numbers in
Turkish, is docs/canli-demo.md.

Needs only Python 3.9+ (standard library). Copy this one file to the Desktop,
then in a new cmd window:
    cd %USERPROFILE%\\Desktop
    python live_bot.py --url http://<presenter-laptop-IP>:3000
Where python.org was installed without ticking "Add python.exe to PATH",
`python` opens the Microsoft Store stub; type `py live_bot.py ...` instead.
The old flow, on laptop A only (the core is published on loopback):
    python lab\\live_bot.py --legacy --url http://localhost:8000

Exit codes:
    0  ödeme alınmadı -- the store declined or asked for a code the bot does
       not have; on the old path, any verdict other than allow/warn
    1  ödeme alındı -- the store answered "paid" (old path: "charged")
    2  kurulum/ağ hatası -- server unreachable or not ready, connection lost
       mid-run, the store's fail-closed 503, an unexpected answer. No result,
       never "blocked"
  130  stopped with Ctrl+C
Nothing but a payment that went through exits 1: a rehearsal loop that counts
exit codes used to record a traceback (Python's exit 1) as "the bot got
through".
"""
import argparse
import hashlib
import http.client
import json
import math
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# --- what the SDK does (sdk/deepcheck.js) -------------------------------------
FLUSH_INTERVAL_MS = 2000
ROLLING_WINDOW_MS = 10000
HESITATION_THRESHOLD_MS = 400

# --- what the script does -------------------------------------------------------
# train_model.py's plain "bot" persona, re-implemented (see the module
# docstring for how close, and why that matters). The card number, name and
# expiry stay filled; every attempt clicks the CVV field, types three new
# digits and retries. Two loops run side by side, as in that persona: a
# mousemove on the script's own timer, with no frame clock, so the pointer is a
# sparse dotted path; and the scripted steps, whose keys arrive a few
# milliseconds apart. The small timing noise is the persona's. These constants
# and script_timeline are what was measured: changing any of them voids every
# number in the docstring.
MOVE_EVERY_MS = 80      # the script's own mousemove timer
ATTEMPT_EVERY_MS = 2000 # one CVV attempt per cycle
CVV = (630, 576)        # the CVV field's centre on the demo page, 1440 px wide window
DEMO_AMOUNT = 2038.8    # Demo.jsx: 1699 + %20 KDV

# --- what the console says ----------------------------------------------------
# The scored client is told only the PUBLIC reason (main.PUBLIC_REASONS), and
# three of the verify reasons describe the state of the TELEMETRY, not a
# verdict: unknown_session (no window of this script was stored),
# insufficient_evidence (too few observed windows, or an undecided sequential
# test), stale (the last stored window is older than main.DECISION_MAX_AGE_S,
# 30 s). Printing every verify as a step-up made a network hiccup or a frozen
# console read as a detection on stage. score and step_up are the two that do
# mean "the server asked for a second proof".
ACTIONS_TR = {
    "allow": "ONAYLANDI",
    "warn": "UYARI İLE ONAYLANDI",
    "block": "ENGELLENDİ",
}
VERIFY_TR = {
    "score": "EK DOĞRULAMA İSTENDİ",
    "step_up": "EK DOĞRULAMA İSTENDİ",
    "insufficient_evidence": "KARAR İÇİN VERİ YETERSİZ",
    "unknown_session": "OTURUM VERİSİ SUNUCUYA ULAŞMADI",
    "stale": "VERİ GÜNCEL DEĞİL",
}
VERIFY_NOTES_TR = {
    "score": "Risk skoru 60-80 bandında; sunucu kartı çekmeden ikinci kanıt istedi.",
    "step_up": "Sunucu kartı çekmeden ikinci kanıt istedi; hangi kontrolün "
               "devreye girdiği yalnızca SOC panelinde görünür.",
    "insufficient_evidence": "Sunucu karar verecek kadar kanıt bulamadı (az gözlenen pencere "
                             "ya da henüz sonuçsuz ardışık test). Bu bir tespit değil.",
    "unknown_session": "Botun davranış pencereleri sunucuya kaydedilmedi. Bu bir tespit değil.",
    "stale": "Son davranış penceresi 30 saniyeden eski. Bu bir tespit değil.",
}
PAYMENT_TR = {"charged": "ödeme alındı", "declined": "ödeme alınmadı"}
# The backend's own labels are ASCII where it falls back (main.py).
LABELS_TR = {"Degerlendirilemedi": "Değerlendirilemedi"}


def script_timeline(start_ms: int, duration_ms: int, rng: random.Random) -> dict:
    """Every event the script produces from start_ms on, until duration_ms.

    Measured before it went on stage: this timeline through
    scorer.compute_risk and main.py's decision rules, offline, 200 random runs
    of 8 flushes -> 200 blocked, lowest session score 93.2, median 94.8 (HOST
    bundle backend/model-sklearn1.8.0.pkl, trained 2026-09-25). The live runs
    used the container's bundle and are listed in the module docstring and in
    docs/canli-demo.md ("Ölçülenler").
    """
    end = start_ms + duration_ms
    mouse, clicks, keys = [], [], []

    x, y, t = 140.0, 120.0, start_ms
    while t < end:
        t += max(MOVE_EVERY_MS + int(rng.gauss(0, 10)), 1)
        x += 5.0 + rng.gauss(0, 3.0)
        y += 2.0 + rng.gauss(0, 2.2)
        mouse.append({"x": round(x, 1), "y": round(y, 1), "t": t})

    t = start_ms + 300
    while t < end:
        attempt_start = t
        clicks.append({"x": CVV[0], "y": CVV[1], "t": t})
        t += max(150 + int(rng.gauss(0, 8)), 1)
        for _ in range(3):                      # three CVV digits
            keys.append({"t": t})
            t += rng.randint(1, 4)
        t = max(t, attempt_start + ATTEMPT_EVERY_MS)
    return {"mouse_trajectory": [m for m in mouse if m["t"] <= end],
            "click_timing": [c for c in clicks if c["t"] <= end],
            "key_events": [k for k in keys if k["t"] <= end]}


class SdkWindow:
    """The SDK's buffers and flush rule, replayed over a precomputed timeline."""

    def __init__(self, timeline: dict):
        self.timeline = timeline
        self.hesitations = []          # (gap, t)
        self.last_event_at = None
        self.sent_upto = None

    def flush(self, now_ms: int):
        cutoff = now_ms - ROLLING_WINDOW_MS
        lists = {k: [e for e in v if cutoff <= e["t"] <= now_ms] for k, v in self.timeline.items()}
        # recordHesitation(): a gap >= 400 ms before an event counts; then the
        # flush-time idle checkpoint for silence nothing has closed yet.
        stamps = sorted(e["t"] for v in self.timeline.values() for e in v
                        if (self.sent_upto is None or e["t"] > self.sent_upto) and e["t"] <= now_ms)
        for s in stamps:
            if self.last_event_at is not None and s - self.last_event_at >= HESITATION_THRESHOLD_MS:
                self.hesitations.append((s - self.last_event_at, s))
            self.last_event_at = s
        if self.last_event_at is not None and now_ms - self.last_event_at >= HESITATION_THRESHOLD_MS:
            self.hesitations.append((now_ms - self.last_event_at, now_ms))
            self.last_event_at = now_ms
        self.hesitations = [h for h in self.hesitations if h[1] >= cutoff]
        self.sent_upto = now_ms
        if not any(lists.values()):
            return None
        pointer = len(lists["mouse_trajectory"]) + len(lists["click_timing"])
        return {
            **lists,
            "scroll_events": [],
            "hesitation_intervals": [g for g, _ in self.hesitations],
            "focus_changes": [],
            "client_signals": {"untrusted_events": 0, "webdriver": False,
                               "pointer_mouse": pointer, "pointer_pen": 0, "pointer_touch": 0},
            "client_sent_at": now_ms,
        }


# --- the store (apps/checkout, apps/checkout-server) ----------------------------
# checkout-web forwards /deepcheck/api/ to the core's /api/, so the SDK
# protocol below is the core's own, under a prefix; /api/ on the same origin
# belongs to the store's server. docs/architecture-two-apps.md is the contract.
STORE_SDK_PREFIX = "/deepcheck"
# Display fields only, as the store's page sends them: the full number and the
# CVV never leave the browser (the contract's stand-in for a payment
# provider's tokenisation). The values are arbitrary: there is no card number
# behind them, and the store never sees one.
BOT_CARD = {"last4": "0366", "brand": "visa", "exp_month": 9, "exp_year": 2028}
# The store answers with a payment status and nothing else: no score, no
# label, no reason. So the console cannot say WHY, and does not pretend to.
# requires_action in particular covers a detection AND a telemetry state (too
# little evidence after the store's hidden re-asks, a stale window, an unknown
# session); only the SOC can tell them apart.
STORE_RESULTS_TR = {
    "paid": ("ÖDEME ALINDI", 1),
    "declined": ("ÖDEME REDDEDİLDİ - satıcı sebep söylemez; sebep yalnızca SOC panelinde", 0),
    "requires_action": ("EK DOĞRULAMA İSTENDİ - bot kodu bilmiyor", 0),
}
STORE_NOTES_TR = {
    "paid": "Mağaza ödemeyi kabul etti. Demo mağazadır: gerçek tahsilat yapılmaz.",
    "declined": "Mağaza kararı DeepCheck'e sordu ve ödemeyi reddetti. Neden yalnızca SOC panelinde.",
    "requires_action": "Mağaza nedenini söylemez: bu bir tespit de olabilir, botun verisinin yetersiz "
                       "ya da eski olması da. Gerçek neden SOC panelinde («Son kaydedilen karar»).",
}


def solve_proof_of_work(challenge: str, bits: int) -> str:
    """SHA-256("<challenge>.<nonce>") with `bits` leading zero bits, as the SDK does."""
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}.{nonce}".encode()).digest()
        zeros = 0
        for byte in digest:
            if byte == 0:
                zeros += 8
                continue
            zeros += 8 - byte.bit_length()
            break
        if zeros >= bits:
            return str(nonce)
        nonce += 1


class Api:
    """JSON over HTTP that never raises for a network or protocol failure.

    Status 0 with a Turkish `detail` means "no usable answer": the server was
    not reached, the connection dropped, or a 2xx body was not a JSON object.
    Only HTTPError used to be caught, so a reset, a timeout or a
    RemoteDisconnected mid-run printed an English traceback on stage and
    exited 1 -- the code reserved for "the payment went through". A 2xx
    non-JSON body is what nginx's SPA fallback answers for any unknown path
    (index.html with 200), e.g. an old frontend image without the /api/ proxy.
    """

    TIMEOUT_S = 15

    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body=None, headers=None, timeout_s=None):
        timeout_s = timeout_s or self.TIMEOUT_S
        req = urllib.request.Request(
            self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as res:
                status, raw = res.status, res.read()
                content_type = res.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            # First: HTTPError is itself a URLError and an OSError.
            try:
                raw = exc.read()
            except (OSError, http.client.HTTPException):
                raw = b""
            return exc.code, _error_body(raw)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            # ValueError: a --url urllib cannot parse (http.client.InvalidURL
            # is an HTTPException; a bad host label raises UnicodeError).
            return 0, {"detail": _network_detail(exc, timeout_s)}
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = None
        if not isinstance(data, dict):
            # The server WAS reached -- a wrong port, an old frontend image
            # without the /api/ proxy, or nginx's SPA fallback -- so this is
            # not the network/firewall case main() prints for status 0.
            return 0, {"reached": True, "detail": (
                f"sunucu JSON yerine başka bir yanıt döndürdü (HTTP {status}, "
                f"{content_type or 'türü belirsiz'})")}
        return status, data


def _error_body(raw: bytes) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {"detail": raw.decode("utf-8", "replace")[:200]}
    return data if isinstance(data, dict) else {"detail": data}


def _network_detail(exc: BaseException, timeout_s: int) -> str:
    """The failure in Turkish, with the exception class for whoever debugs it.
    The OS message itself is left out: it is in the OS's language, not ours."""
    reason = getattr(exc, "reason", exc)  # URLError wraps the socket error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        what = f"sunucu {timeout_s} saniye içinde yanıt vermedi"
    elif isinstance(reason, ConnectionRefusedError):
        what = "bağlantı reddedildi: bu adreste ve portta çalışan bir sunucu yok"
    elif isinstance(reason, (ConnectionResetError, ConnectionAbortedError, http.client.RemoteDisconnected)):
        what = "bağlantı karşı taraftan kapatıldı"
    elif isinstance(reason, socket.gaierror):
        what = "adres çözümlenemedi"
    elif isinstance(reason, (http.client.InvalidURL, ValueError)):
        what = "adres geçersiz"
    elif isinstance(reason, OSError):
        what = "ağ hatası"
    else:
        # URLError("no host given") and the like carry a short English string.
        what = f"ağ hatası: {reason}"
    return f"{what} [{type(reason).__name__}]"


def detail_text(res: dict) -> str:
    """A server `detail` for one console line: FastAPI's 422 detail is a list."""
    detail = res.get("detail")
    if isinstance(detail, list):
        return f"geçersiz istek ({len(detail)} alan)"
    if detail is None or detail == "":
        return "ayrıntı yok"
    return str(detail)[:200]


def fmt_score(value) -> str:
    """A risk score, or an em dash when the server had none (unknown_session)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
        return f"{value:.1f}"
    return "-"


def fmt_label(value) -> str:
    return LABELS_TR.get(value, value) if value else "-"


def verdict_tr(action, reason) -> str:
    if action == "verify":
        return VERIFY_TR.get(reason, "EK DOĞRULAMA İSTENDİ")
    return ACTIONS_TR.get(action, f"TANIMLANMAMIŞ KARAR ({action})")


def base_url(url: str) -> str:
    """scheme://host:port of whatever was typed.

    http://IP:3000, http://IP:3000/demo, http://IP:3000/dashboard and a bare
    IP:3000 all mean the same site. Only "/demo" used to be stripped, so a
    pasted dashboard address sent /dashboard/api/health to nginx, whose SPA
    fallback answers any unknown path with index.html and a 200.

    "localhost" becomes 127.0.0.1. On Windows it resolves to ::1 first, docker
    publishes the port on IPv4 only, and every request then waited ~2 s for
    the IPv6 attempt to fail (measured: health, session and attest at 2.0 s
    each). The flushes then left late and the session reached the SOC panel
    about 12 s after the bot started instead of about 3."""
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    parts = urllib.parse.urlsplit(url)
    netloc = parts.netloc
    if parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
    return f"{parts.scheme}://{netloc}"


def say(msg=""):
    print(msg, flush=True)


def connection_lost(during: str, res: dict) -> int:
    say(f"\nBağlantı koptu ({during}): {res.get('detail')}")
    say("Ağı kontrol edip botu yeniden çalıştırın. Bu çalıştırmanın sonucu yok: engellendi sayılmaz.")
    return 2


def refused(during: str, status: int, res: dict) -> int:
    say(f"\nSunucu isteği kabul etmedi ({during}, HTTP {status}): {detail_text(res)}")
    if status == 429:
        say("Çok sık oturum açıldı; 60 saniye bekleyip tekrar deneyin.")
    elif status == 404 and during == "ödeme":
        say("Demo ödeme uç noktası kapalı: sunum bilgisayarının .env dosyasında DEMO_ENDPOINTS=1 olmalı.")
    return 2


def store_error_text(res: dict) -> str:
    """The store's error body for one console line. It names fields, never
    values (apps/checkout-server answers a 422 with the field names only)."""
    fields = res.get("fields")
    if isinstance(fields, list) and fields:
        return "geçersiz alan: " + ", ".join(str(f) for f in fields[:6])
    if res.get("error"):
        return str(res.get("error"))[:200]
    return detail_text(res)


def fmt_amount(value) -> str:
    """2038.8 -> 2.038,80, the way a Turkish price is written."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f"{value:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return "-"


def core_not_ready(status: int, health: dict):
    """None when the core's /api/health says the model is loaded, else the
    console lines and exit 2. The same checks, and the same words, in both
    modes."""
    if status in (502, 503, 504):
        say(f"\nSunucu hazır değil (HTTP {status}): sunum bilgisayarında backend henüz yanıt vermiyor.")
        say("Orada `docker compose logs -f backend` ile bakın; «Modeller egitiliyor» yazıyorsa bekleyin.")
        return 2
    if status != 200 or not health.get("model_loaded"):
        say(f"\nSunucu hazır değil (HTTP {status}): model yüklü değil ya da yanıt beklenmedik.")
        return 2
    return None


def unreachable(res: dict) -> int:
    say(f"\nSunucuya ulaşılamadı: {res.get('detail')}")
    say("Kontrol: aynı ağdasınız mı, adres doğru mu, sunum bilgisayarında 3000 portu açık mı?")
    return 2


def detect_mode(api: "Api", forced_legacy: bool):
    """("store" | "legacy", None), or (None, an exit code).

    The store is whatever answers JSON at /deepcheck/api/health: checkout-web
    forwards that path to the core. nginx answering 502-504 there is the store
    too, with the core still starting. Anything else that was reached -- the
    legacy frontend's SPA fallback (index.html with a 200), the core's own 404
    at :8000 -- falls through to the old flow's /api/health. A 2xx that is not
    JSON comes back from Api.call as status 0 with "reached", so it is never
    mistaken for the store.
    """
    if not forced_legacy:
        status, health = api.call("GET", STORE_SDK_PREFIX + "/api/health")
        if status == 0 and not health.get("reached"):
            return None, unreachable(health)
        if status == 200 or status in (502, 503, 504):
            code = core_not_ready(status, health)
            return ("store", None) if code is None else (None, code)

    status, health = api.call("GET", "/api/health")
    if status == 0 and health.get("reached"):
        say(f"\nBu adreste DeepCheck API'si yok: {health.get('detail')}.")
        say("Kontrol: mağaza için adres http://IP:3000 olmalı. Sunum bilgisayarındaki imajlar güncel mi"
            " (orada `docker compose up -d --build`)?")
        return None, 2
    if status == 0:
        return None, unreachable(health)
    if status == 200 and "core" in health and "model_loaded" not in health:
        # The store server's own health ({status, core}) answered /api/health.
        if forced_legacy:
            say("\nBu adres yeni mağaza. --legacy yalnızca çekirdek API (http://localhost:8000)"
                " içindir; mağaza için --legacy olmadan çalıştırın.")
        else:
            say("\nMağaza bulundu ama SDK yolu (/deepcheck/api/) yanıt vermiyor.")
            say("Kontrol: sunum bilgisayarındaki checkout imajı güncel mi (orada `docker compose up -d --build`)?")
        return None, 2
    code = core_not_ready(status, health)
    return ("legacy", None) if code is None else (None, code)


def check_store(api: "Api"):
    """The store's own server must be up before the bot spends a session.
    checkout-api refuses to start without its merchant credential, and nginx
    then answers 502 for everything under /api/. None, or an exit code."""
    status, health = api.call("GET", "/api/health")
    if status == 0 and not health.get("reached"):
        return connection_lost("mağaza sunucusu kontrol edilirken", health)
    if status in (0, 502, 503, 504):
        say(f"\nMağaza sunucusu hazır değil (HTTP {status}).")
        say("Sunum bilgisayarında `docker compose logs checkout-api` ile bakın: CHECKOUT_MERCHANT_ID ve"
            " CHECKOUT_MERCHANT_KEY tanımlı değilse sunucu başlamaz (.env.example).")
        return 2
    if status != 200:
        say(f"\nMağaza sunucusu beklenmeyen bir yanıt verdi (HTTP {status}): {store_error_text(health)}")
        return 2
    if health.get("core") is False:
        say("\nMağaza sunucusu DeepCheck çekirdeğine ulaşamıyor; bu durumda ödeme hiçbir zaman alınmaz.")
        say("Sunum bilgisayarında `docker compose ps` ve `docker compose logs checkout-api` ile bakın.")
        return 2
    return None


def open_session(api: "Api", prefix: str):
    """The SDK's registration: ((session_id, token), None) or (None, an exit code)."""
    status, minted = api.call("POST", prefix + "/api/session", {})
    if status == 0:
        return None, connection_lost("oturum açılırken", minted)
    if status != 201:
        return None, refused("oturum", status, minted)
    nonce = solve_proof_of_work(minted["challenge"], int(minted["difficulty_bits"]))
    status, attested = api.call("POST", prefix + "/api/session/attest", {
        "session_id": minted["session_id"], "challenge": minted["challenge"], "nonce": nonce,
        "runtime": {"clock_resolution_us": 100.0, "timer_lag_ms": 4.0}})
    if status == 0:
        return None, connection_lost("oturum doğrulanırken", attested)
    if status != 201:
        return None, refused("oturum doğrulama", status, attested)
    return (attested["session_id"], attested["token"]), None


def send_windows(api: "Api", prefix: str, session_id: str, headers: dict, flushes: int,
                 show_scores: bool = True):
    """The scripted form fill, flushed as the SDK flushes it. None, or an exit
    code when the connection dropped. What is SENT is identical in both modes:
    the measured behaviour does not know where it is being sent.

    Only the console line differs. The old flow prints each window's score, as
    it always has. On the store (show_scores=False) checkout-web's nginx asks
    the core for an acknowledgement, so the reply carries no score and the line
    says only that the window went out: the console shows the jury nothing the
    payer's browser is not shown. A store reply that still carries a score (an
    image from before that change) is not printed either; one line afterwards
    says so, so a rehearsal catches it."""
    start = int(time.time() * 1000) + 200
    window = SdkWindow(script_timeline(start, (flushes + 1) * FLUSH_INTERVAL_MS, random.Random()))
    say("Bot kart deniyor: her denemede CVV alanına tıklayıp 3 yeni rakam yazıyor...")
    score_returned = False
    for i in range(1, flushes + 1):
        send_at = start + i * FLUSH_INTERVAL_MS
        time.sleep(max(0.0, send_at / 1000 - time.time()))
        body = window.flush(int(time.time() * 1000))
        if body is None:
            continue
        status, res = api.call("POST", prefix + "/api/analyze", {"session_id": session_id, **body}, headers)
        if status == 0:
            return connection_lost(f"pencere {i} gönderilirken", res)
        if status != 200:
            say(f"  pencere {i}: sunucu kabul etmedi (HTTP {status}): {detail_text(res)}")
            continue
        if show_scores and "risk_score" in res:
            say(f"  pencere {i}: risk skoru {fmt_score(res.get('risk_score')):>5}  {fmt_label(res.get('label'))}")
        else:
            score_returned = score_returned or "risk_score" in res
            say(f"  pencere {i}: gönderildi")
    if score_returned:
        say("  Uyarı: mağaza, pencere yanıtlarında risk skorunu hâlâ tarayıcıya döndürüyor (burada gösterilmedi)."
            " Sunum bilgisayarındaki imajlar güncel mi (orada `docker compose up -d --build`)?")
    return None


def legacy_charge(api: "Api", session_id: str, headers: dict) -> int:
    """The old combined demo's merchant endpoint, with its old console output."""
    say("\nBot «Onayla»ya basıyor: POST /api/demo/charge")
    status, charge = api.call("POST", "/api/demo/charge", {"session_id": session_id, "amount": DEMO_AMOUNT}, headers)
    if status == 0:
        code = connection_lost("ödeme istenirken", charge)
        # The request may have reached the server before the line dropped, so
        # a verdict may exist; this script just never heard it.
        say("İstek sunucuya ulaşmış olabilir; kararı SOC panelinde görün.")
        return code
    if status != 200:
        return refused("ödeme", status, charge)
    decision = charge.get("decision") or {}
    action, reason = decision.get("action"), decision.get("reason")
    payment = PAYMENT_TR.get(charge.get("status"), f"ödeme durumu bilinmiyor ({charge.get('status')})")
    say("")
    say("#" * 64)
    say(f"  SUNUCU KARARI : {verdict_tr(action, reason)} - {payment}")
    say(f"  Risk skoru    : {fmt_score(decision.get('risk_score'))}  ({fmt_label(decision.get('label'))})")
    if action == "verify" and reason in VERIFY_NOTES_TR:
        say(f"  Açıklama      : {VERIFY_NOTES_TR[reason]}")
    say("#" * 64)
    return 1 if charge.get("status") == "charged" else 0


# The store may ask the core up to four times for one checkout (three hidden
# re-asks, 2 s apart, on insufficient evidence; apps/checkout-server), each
# with its own 4 s budget, so its answer can legitimately take longer than the
# 15 s every other call gets.
STORE_CHECKOUT_TIMEOUT_S = 30


def store_checkout(api: "Api", session_id: str, token: str) -> int:
    """POST /api/checkout as the store's page sends it, and the store's answer.

    A guest checkout, like the page's: no e-mail. The store names the customer
    per session, so every run is a new customer reference at the store and no
    run inherits per-customer state from the one before.
    """
    say(f"\nBot ödeme istiyor: POST /api/checkout  (kart •••• {BOT_CARD['last4']})")
    say("Mağaza sunucusu kararı DeepCheck'e soruyor; birkaç saniye sürebilir.")
    status, res = api.call("POST", "/api/checkout",
                           {"session_id": session_id, "token": token, "card": dict(BOT_CARD)},
                           timeout_s=STORE_CHECKOUT_TIMEOUT_S)
    if status == 0 and not res.get("reached"):
        code = connection_lost("ödeme istenirken", res)
        # As in the old flow: the request may have reached the store.
        say("İstek sunucuya ulaşmış olabilir; kararı SOC panelinde görün.")
        return code
    if status == 0:
        say(f"\nMağazadan beklenmeyen bir yanıt geldi: {res.get('detail')}. Bu çalıştırmanın sonucu yok.")
        return 2
    if status == 503:
        say("\nMağaza ödemeyi şu anda işleyemiyor (HTTP 503): DeepCheck çekirdeğine ulaşamadı ya da yanıt alamadı.")
        say("Ödeme alınmadı, ama bu bir engelleme değil: mağaza emin olamayınca ödemeyi hiç almaz."
            " Bu çalıştırmanın sonucu yok.")
        return 2
    if status == 429:
        say(f"\nMağaza isteği sınırladı (HTTP 429): {store_error_text(res)}.")
        say("Aynı adresten çok sık ödeme denendi; 60 saniye bekleyip tekrar deneyin.")
        return 2
    if status != 200:
        say(f"\nMağaza isteği kabul etmedi (HTTP {status}): {store_error_text(res)}")
        return 2
    outcome = STORE_RESULTS_TR.get(res.get("status"))
    if outcome is None:
        say(f"\nMağazadan tanımlanmamış bir yanıt geldi (durum: {res.get('status')}). Bu çalıştırmanın sonucu yok.")
        return 2
    text, code = outcome
    say("")
    say("#" * 64)
    say(f"  MAĞAZA YANITI : {text}")
    if res.get("status") == "paid" and res.get("order_id"):
        say(f"  Sipariş       : {res.get('order_id')}")
    say(f"  Açıklama      : {STORE_NOTES_TR[res.get('status')]}")
    say("#" * 64)
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DeepCheck canlı demo botu (API üzerinden betikli saldırı)")
    ap.add_argument("--url", default=None,
                    help="mağazanın adresi, ör. http://192.168.1.20:3000 (varsayılan http://localhost:3000;"
                         " --legacy ile http://localhost:8000)")
    ap.add_argument("--legacy", action="store_true",
                    help="eski birleşik demonun akışı: /api/... ve /api/demo/charge"
                         " (çekirdek API, http://localhost:8000)")
    ap.add_argument("--flushes", type=int, default=8, help="ödemeden önce gönderilecek davranış penceresi")
    args = ap.parse_args(argv)
    url = args.url or ("http://localhost:8000" if args.legacy else "http://localhost:3000")
    api = Api(base_url(url))

    say("=" * 64)
    say(" DeepCheck canlı demo - BETİKLİ BOT SALDIRISI")
    say(" Tarayıcı yok: bot, SDK protokolünü taklit edip sunucuya doğrudan")
    say(" makine davranışı gönderiyor, ardından ödeme istiyor.")
    say(" Kasıtlı olarak basit: modelin eğitimde gördüğü bot tipinin kopyası.")
    say("=" * 64)
    say(f"Hedef: {api.base}")

    mode, code = detect_mode(api, args.legacy)
    if mode is None:
        return code
    if mode == "store":
        say("Akış: TechStore mağaza ödemesi (POST /api/checkout)")
        code = check_store(api)
        if code is not None:
            return code
        status, cart = api.call("GET", "/api/cart")
        if status == 0 and not cart.get("reached"):
            return connection_lost("sepet alınırken", cart)
        if status != 200:
            say(f"\nMağaza sepeti vermedi (HTTP {status}): {store_error_text(cart)}")
            return 2
        say(f"Sepet tutarı: {fmt_amount(cart.get('total'))} {cart.get('currency') or ''}".rstrip())
        prefix = STORE_SDK_PREFIX
    else:
        say("Akış: eski birleşik demo (POST /api/demo/charge)")
        prefix = ""

    opened, code = open_session(api, prefix)
    if opened is None:
        return code
    session_id, token = opened
    headers = {"X-DeepCheck-Token": token}
    say(f"Oturum: {session_id[:8]}…  (SOC panelinde bu kimlikle görünür)\n")

    code = send_windows(api, prefix, session_id, headers, args.flushes, show_scores=(mode == "legacy"))
    if code is not None:
        return code
    if mode == "store":
        return store_checkout(api, session_id, token)
    return legacy_charge(api, session_id, headers)


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        say("\nDurduruldu (Ctrl+C).")
        code = 130
    except Exception as exc:  # noqa: BLE001 -- the exit code is the contract
        # Python's own exit code for an uncaught exception is 1, which here
        # means "the payment went through". Anything unforeseen -- a 201 without
        # the expected keys from a mismatched backend, say -- is a setup error.
        say(f"\nBeklenmeyen hata [{type(exc).__name__}]: {exc}")
        say("Sunum bilgisayarındaki sürümle bu dosyanın aynı olduğundan emin olun.")
        code = 2
    sys.exit(code)
