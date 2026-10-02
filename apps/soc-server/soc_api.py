"""soc-api: the SOC's backend-for-frontend (docs/architecture-two-apps.md).

Why this service exists. The core's analyst endpoints (GET /api/sessions,
GET /api/score/{id}) expose every customer's live session id and score, so
they sit behind DASHBOARD_KEY. The old dashboard asked the analyst for that key
and kept it in the browser (sessionStorage) to send as a header on every poll:
anything that can run script in that tab -- an extension, an XSS -- could read
it, and a screenshot of the devtools network tab published it. Here the key is
typed ONCE, compared on this server, and the browser receives only a signed,
httpOnly, SameSite=Strict, Path=/api cookie that expires after eight hours.
The key itself is never sent back, never stored in the browser and never
logged.

Where the cookie goes, honestly: it is HOST-scoped, not origin-scoped. It has
no Domain attribute, so it is a host-only cookie for "localhost", and browsers
do not scope cookies by port: the analyst's browser also sends it with every
request to http://localhost:<any port>/api/... -- on laptop A that is the
store's checkout-api (:3000/api/), the core itself on its loopback port
(:8000/api/) and any other local app that happens to serve /api/. None of the
DeepCheck services reads it (only this one knows SOC_SESSION_SECRET), and
httpOnly keeps it out of page scripts, but it does travel. SameSite=Strict does
not narrow this: a site is scheme plus registrable host, ignoring the port, so
every localhost port is the same site. Origin scoping would need the SOC on a
host name of its own (e.g. soc.localhost) or a path no other app uses.

What the cookie is, honestly: proof that someone typed the shared dashboard
key within the last eight hours. It is not an operator identity -- there is one
key, not one per analyst -- and there is no audit trail of who looked at what.
The per-operator answer exists in the core for the profile review endpoint
(PROFILE_REVIEW_KEYS); this service does not pretend to be that.

Run with ONE worker. The login rate limit and the logout revocation list live
in this process's memory: a second worker would keep its own, halving the
limit's effect and forgetting the other's logouts. The SOC serves a handful of
analysts on loopback, and every request here is I/O bound, so one worker is
not a throughput limit. Both are also lost on restart (see _Revocations).

    uvicorn --factory soc_api:create_app --host 0.0.0.0 --port 8200 --workers 1
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("deepcheck.soc")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

COOKIE_NAME = "deepcheck_soc"
# Path=/api: the cookie goes with API calls only, never with the HTML, the JS
# bundle or the fonts. Nothing outside /api/ has a use for it. The path is the
# only narrowing beyond the host: other localhost ports' /api/ receive it too
# (module docstring).
COOKIE_PATH = "/api"
SESSION_TTL_S = 8 * 3600
# Versioned so a change to the signed layout invalidates every old cookie
# instead of being parsed under new rules.
_COOKIE_VERSION = "v1"
_COOKIE_DOMAIN = b"deepcheck-soc-session"

# The core's session ids are uuid4 strings; the dashboard's own fixtures use
# readable ids. This is the charset both fit in, and nothing in it can change
# the path it is interpolated into ("/", ".", "%", "?" and "#" are all out).
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

# Login attempts per client address per minute, and for all addresses
# together. The second one is the backstop: behind soc-web every request
# arrives from the nginx container's address (see _client_address), and a
# client that could vary its address would otherwise get a fresh bucket each
# time.
LOGIN_WINDOW_S = 60.0
LOGIN_LIMIT_PER_ADDRESS = 10
LOGIN_LIMIT_GLOBAL = 50

# Short on purpose. The dashboard polls every 3 s and never stacks requests
# (pages/Dashboard.jsx, inFlight), so a core that takes longer than this is
# reported as unreachable rather than left hanging behind nginx's own timeout.
CORE_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)

# Minimum length of the cookie-signing secret. secrets.token_urlsafe(32) is 43
# characters; 32 is a floor that rules out a word or a short passphrase.
MIN_SESSION_SECRET_LEN = 32

# Every message a caller can see from this service, in Turkish (CLAUDE.md,
# rule 1). The SPA prints its own wording for most of them; these are what a
# person reading the raw response, or nginx's error page, gets.
MSG_UNAUTHENTICATED = "Oturum açılmamış ya da oturumun süresi dolmuş"
MSG_BAD_KEY = "Pano erişim anahtarı geçersiz"
MSG_RATE_LIMITED = "Çok fazla giriş denemesi — bir dakika sonra tekrar deneyin"
MSG_BAD_REQUEST = "Geçersiz istek"
MSG_BAD_SESSION_ID = "Geçersiz oturum kimliği"
MSG_NOT_FOUND = "Oturum bulunamadı"
MSG_CORE_UNREACHABLE = "Çekirdek API'ye ulaşılamadı"
MSG_CORE_REJECTED_KEY = "Çekirdek API pano anahtarını reddetti — DASHBOARD_KEY iki serviste aynı olmalı"
MSG_CORE_ERROR = "Çekirdek API hata döndürdü"
MSG_CORE_BAD_BODY = "Çekirdek API'den geçersiz yanıt alındı"
MSG_ROUTE_NOT_FOUND = "Bulunamadı"
MSG_METHOD_NOT_ALLOWED = "Bu yöntem desteklenmiyor"

# Scores and session ids belong to one moment; no cache between here and the
# browser may keep them. soc-web's nginx adds the same header.
_NO_STORE = {"Cache-Control": "no-store"}


class ConfigError(RuntimeError):
    """The process must not start. Messages name the variable, never a value."""


@dataclass(frozen=True)
class Settings:
    core_url: str
    dashboard_key: str
    session_secret: str
    cookie_secure: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        core_url = env.get("DEEPCHECK_CORE_URL", "").strip() or "http://backend:8000"
        dashboard_key = env.get("DASHBOARD_KEY", "").strip()
        session_secret = env.get("SOC_SESSION_SECRET", "").strip()
        cookie_secure = env.get("SOC_COOKIE_SECURE", "0").strip() == "1"

        # No fallback in any mode, unlike the core's DEBUG=1 development
        # values: a SOC that starts without its key would answer every poll
        # with 502, and one that starts without its signing secret would have
        # to invent one -- a random per-process secret silently logs every
        # analyst out on restart, a fixed one is public in this repository.
        if not dashboard_key:
            raise ConfigError(
                "DASHBOARD_KEY tanımlı değil: soc-api çekirdeğin pano anahtarı olmadan başlamaz "
                "(çekirdekle aynı değer olmalı)."
            )
        if not session_secret:
            raise ConfigError(
                "SOC_SESSION_SECRET tanımlı değil: SOC oturum çerezleri imzalanamaz. "
                'Üretmek için: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if len(session_secret) < MIN_SESSION_SECRET_LEN:
            raise ConfigError(f"SOC_SESSION_SECRET en az {MIN_SESSION_SECRET_LEN} karakter olmalı.")
        # The cookie secret must not be the key the cookie stands in for:
        # anyone who learned one would hold both.
        if hmac.compare_digest(session_secret.encode("utf-8"), dashboard_key.encode("utf-8")):
            raise ConfigError("SOC_SESSION_SECRET, DASHBOARD_KEY ile aynı olamaz.")
        if not re.match(r"https?://[^/\s]+", core_url):
            raise ConfigError("DEEPCHECK_CORE_URL http:// ya da https:// ile başlayan bir adres olmalı.")
        return cls(
            core_url=core_url.rstrip("/"),
            dashboard_key=dashboard_key,
            session_secret=session_secret,
            cookie_secure=cookie_secure,
        )


# --- Session cookie ------------------------------------------------------------
#
# Value: "v1.<expiry unix seconds>.<nonce>.<hex HMAC-SHA256>". Stateless, so
# any instance holding the secret can check it; the nonce exists so that one
# cookie can be revoked at logout without logging out every other analyst.
# Rotating SOC_SESSION_SECRET invalidates every cookie at once.


def _sign(secret: str, expiry: int, nonce: str) -> str:
    message = _COOKIE_DOMAIN + b"|" + f"{_COOKIE_VERSION}.{expiry}.{nonce}".encode("ascii")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_cookie(secret: str, now: float) -> tuple[str, int, str]:
    expiry = int(now) + SESSION_TTL_S
    nonce = secrets.token_urlsafe(16)
    return f"{_COOKIE_VERSION}.{expiry}.{nonce}.{_sign(secret, expiry, nonce)}", expiry, nonce


_COOKIE_RE = re.compile(r"v1\.(\d{1,12})\.([A-Za-z0-9_-]{8,64})\.([0-9a-f]{64})")


def read_cookie(value: str | None, secret: str, now: float) -> tuple[int, str] | None:
    """(expiry, nonce) of a valid, unexpired cookie, else None."""
    if not value:
        return None
    match = _COOKIE_RE.fullmatch(value)
    if match is None:
        return None
    expiry, nonce, presented = int(match.group(1)), match.group(2), match.group(3)
    if not hmac.compare_digest(_sign(secret, expiry, nonce), presented):
        return None
    if expiry <= now:
        return None
    # A signature can only come from this secret, so an expiry further out
    # than one TTL means the TTL was shortened since it was issued. It is
    # held to the current rule, not the one it was minted under.
    if expiry > now + SESSION_TTL_S + 60:
        return None
    return expiry, nonce


class _Revocations:
    """Nonces of cookies logged out before they expired.

    In memory: a restart forgets them, and a cookie copied before its logout
    is then accepted again until its own expiry (at most eight hours). Holding
    them in a store would need one this service does not otherwise have; the
    exposure is a cookie that was already exfiltrated, on a loopback SOC.
    """

    def __init__(self) -> None:
        self._until: dict[str, int] = {}

    def revoke(self, nonce: str, expiry: int, now: float) -> None:
        self._prune(now)
        self._until[nonce] = expiry

    def is_revoked(self, nonce: str) -> bool:
        return nonce in self._until

    def _prune(self, now: float) -> None:
        for nonce in [n for n, until in self._until.items() if until <= now]:
            del self._until[nonce]


class _LoginLimiter:
    """Sliding one-minute window of login ATTEMPTS, right or wrong.

    Every attempt counts, not only failures: it is the simpler rule, it does
    not need to know the outcome before deciding, and an analyst logs in once
    per eight hours, so it never gets in a legitimate user's way.
    """

    def __init__(self, per_address: int, overall: int, window_s: float) -> None:
        self.per_address = per_address
        self.overall = overall
        self.window_s = window_s
        self._by_address: dict[str, collections.deque[float]] = {}
        self._all: collections.deque[float] = collections.deque()

    def allow(self, address: str, now: float) -> bool:
        horizon = now - self.window_s
        while self._all and self._all[0] <= horizon:
            self._all.popleft()
        bucket = self._by_address.setdefault(address, collections.deque())
        while bucket and bucket[0] <= horizon:
            bucket.popleft()
        # Bounded memory on a page left open for days: drop idle addresses.
        if len(self._by_address) > 1024:
            for key in [k for k, q in self._by_address.items() if not q and k != address]:
                del self._by_address[key]
        if len(bucket) >= self.per_address or len(self._all) >= self.overall:
            return False
        bucket.append(now)
        self._all.append(now)
        return True


def _client_address(request: Request) -> str:
    """The TCP peer, not a header this code reads.

    Behind soc-web every request comes from the nginx container, so the
    per-address limit is in practice one bucket for everyone using the SOC --
    a few analysts on the presenter's laptop. Reading X-Forwarded-For here
    would let any container on the compose network name any address it liked;
    the backend's _client_ip makes the same choice for the same reason.
    uvicorn itself replaces the peer with X-Forwarded-For only when the peer
    is listed in FORWARDED_ALLOW_IPS (default 127.0.0.1, which the nginx
    container is not); soc-web overwrites that header with the address it
    saw, so listing soc-web there would give each browser its own bucket.
    """
    return request.client.host if request.client else "unknown"


class LoginBody(BaseModel):
    # Bounded so a multi-megabyte "key" is turned away by validation rather
    # than hashed. A real key is far shorter.
    key: str = Field(max_length=4096)


def _error(status: int, detail: str, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers={**_NO_STORE, **(headers or {})})


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Builds the app. uvicorn calls this with no arguments (--factory), which
    reads the environment and refuses to start on a missing secret; tests pass
    Settings, a mock transport for the core and a clock they control."""
    settings = settings or Settings.from_env()
    limiter = _LoginLimiter(LOGIN_LIMIT_PER_ADDRESS, LOGIN_LIMIT_GLOBAL, LOGIN_WINDOW_S)
    revocations = _Revocations()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One client for the life of the process: connection reuse to the
        # core, and one place the timeouts are set.
        async with httpx.AsyncClient(
            base_url=settings.core_url,
            timeout=CORE_TIMEOUT,
            transport=transport,
            follow_redirects=False,
        ) as client:
            app.state.core = client
            yield

    # No /docs, /redoc or /openapi.json: nothing here is for a browser to
    # explore, and nginx forwards only /api/ anyway.
    app = FastAPI(title="DeepCheck SOC API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body is English and echoes the input back --
        # for /api/login, that input is a key attempt.
        return _error(422, MSG_BAD_REQUEST)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Starlette's own 404 / 405 bodies ("Not Found", "Method Not
        # Allowed"); every route above returns its own Turkish detail.
        if exc.status_code == 404:
            return _error(404, MSG_ROUTE_NOT_FOUND)
        if exc.status_code == 405:
            return _error(405, MSG_METHOD_NOT_ALLOWED, exc.headers)
        return _error(exc.status_code, MSG_BAD_REQUEST, exc.headers)

    def _session(request: Request) -> tuple[int, str] | None:
        found = read_cookie(request.cookies.get(COOKIE_NAME), settings.session_secret, clock())
        if found is None or revocations.is_revoked(found[1]):
            return None
        return found

    def _set_cookie(response: Response, value: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            value,
            max_age=SESSION_TTL_S,
            path=COOKIE_PATH,
            httponly=True,
            samesite="strict",
            # Off by default: the SOC is served over plain http on loopback,
            # and a Secure cookie would never be sent back there. Turn it on
            # (SOC_COOKIE_SECURE=1) the moment TLS is in front of it.
            secure=settings.cookie_secure,
        )

    def _clear_cookie(response: Response) -> None:
        response.delete_cookie(
            COOKIE_NAME,
            path=COOKIE_PATH,
            httponly=True,
            samesite="strict",
            secure=settings.cookie_secure,
        )

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request) -> Response:
        if not limiter.allow(_client_address(request), clock()):
            logger.warning("SOC girişi hız sınırına takıldı")
            return _error(429, MSG_RATE_LIMITED, {"Retry-After": str(int(LOGIN_WINDOW_S))})
        # Bytes, not str: hmac.compare_digest refuses a str with non-ASCII
        # characters (TypeError -> 500). A key typed with "ş" is a wrong key,
        # and a wrong key is a 401.
        presented = body.key.encode("utf-8")
        if not hmac.compare_digest(presented, settings.dashboard_key.encode("utf-8")):
            # Neither the attempt nor the client address is logged.
            logger.warning("SOC girişi reddedildi")
            return _error(401, MSG_BAD_KEY)
        value, _expiry, _nonce = issue_cookie(settings.session_secret, clock())
        response = Response(status_code=204, headers=_NO_STORE)
        _set_cookie(response, value)
        logger.info("SOC girişi kabul edildi")
        return response

    @app.post("/api/logout")
    async def logout(request: Request) -> Response:
        # Always 204 and always clears: logging out with an expired or forged
        # cookie still leaves the browser with none.
        found = _session(request)
        if found is not None:
            revocations.revoke(found[1], found[0], clock())
        response = Response(status_code=204, headers=_NO_STORE)
        _clear_cookie(response)
        return response

    @app.get("/api/me")
    async def me(request: Request) -> Response:
        if _session(request) is None:
            return _error(401, MSG_UNAUTHENTICATED)
        return Response(status_code=204, headers=_NO_STORE)

    async def _forward(request: Request, path: str) -> Response:
        """GET one core endpoint with the dashboard key and pass its JSON on.

        Only the key header is sent: nothing of the browser's request -- its
        cookie above all -- reaches the core.
        """
        client: httpx.AsyncClient = request.app.state.core
        try:
            upstream = await client.get(
                path,
                headers={"X-Dashboard-Key": settings.dashboard_key, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            # The exception's class only: its text can carry the URL, and the
            # key travels in a header, not the URL, but a log line has no
            # business being one refactor away from printing it.
            logger.warning("Çekirdek API isteği başarısız: %s", type(exc).__name__)
            return _error(502, MSG_CORE_UNREACHABLE)

        if upstream.status_code == 404:
            # The one core error the dashboard handles itself: a session swept
            # by retention ("Bu oturum artık sunucuda yok").
            return _error(404, MSG_NOT_FOUND)
        if upstream.status_code == 401:
            # NOT passed on as 401. To the SPA a 401 means "your SOC login
            # ended, log in again"; this one means the two services disagree
            # about DASHBOARD_KEY, and logging in again would loop forever.
            logger.error("Çekirdek API pano anahtarını reddetti (DASHBOARD_KEY uyuşmuyor)")
            return _error(502, MSG_CORE_REJECTED_KEY)
        if upstream.status_code != 200:
            logger.warning("Çekirdek API %d döndürdü", upstream.status_code)
            return _error(502, MSG_CORE_ERROR)
        try:
            # Parsed only to check it IS JSON; the bytes go on untouched, so
            # what the analyst sees is exactly what the core sent.
            json.loads(upstream.content)
        except ValueError:
            return _error(502, MSG_CORE_BAD_BODY)
        return Response(content=upstream.content, media_type="application/json", headers=_NO_STORE)

    @app.get("/api/sessions")
    async def sessions(request: Request) -> Response:
        if _session(request) is None:
            return _error(401, MSG_UNAUTHENTICATED)
        return await _forward(request, "/api/sessions")

    @app.get("/api/score/{session_id}")
    async def score(session_id: str, request: Request) -> Response:
        # Authentication before validation, so the shape of a valid id is not
        # something an unauthenticated caller can probe.
        if _session(request) is None:
            return _error(401, MSG_UNAUTHENTICATED)
        if not SESSION_ID_RE.fullmatch(session_id):
            return _error(400, MSG_BAD_SESSION_ID)
        return await _forward(request, f"/api/score/{session_id}")

    @app.get("/api/health")
    async def health(request: Request) -> JSONResponse:
        # Unauthenticated and content-free: whether this process is up, and
        # whether the core answered its own health check just now. soc-api
        # stays "ok" while the core is down -- it is up, and says so.
        client: httpx.AsyncClient = request.app.state.core
        try:
            core_ok = (await client.get("/api/health", timeout=2.0)).status_code == 200
        except httpx.HTTPError:
            core_ok = False
        return JSONResponse({"status": "ok", "core": core_ok}, headers=_NO_STORE)

    return app
