"""TechStore's own checkout server (checkout-api).

The store side of the integration in docs/architecture-two-apps.md. The
browser never talks to the DeepCheck core about money: it sends this server a
session id, the session's token and the card's DISPLAY fields, and this server
asks the core `POST /api/decision` server to server, with the merchant
credential, then charges, challenges or declines.

What the payer gets back is a payment status and nothing else. The core's
score, label, reason and message stay on this side of the wire: a page that
tells the scored party why it was refused is a tuning signal (CLAUDE.md,
rule 3), and a real store would not show it either. The analyst sees all of it
in the SOC, which reads the core's audit, not this server.

Nothing here takes money. "paid" means the core allowed the payment and a
real store would now call its payment provider; this demo has none.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

logger = logging.getLogger("checkout")


# --- Configuration -------------------------------------------------------------


class ConfigError(RuntimeError):
    """A setting the server cannot run without is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    core_url: str
    merchant_id: str
    merchant_key: str
    customer_ref_key: str
    debug: bool


# The core parses DEEPCHECK_MERCHANT_KEYS as "id:key,id:key", so an id holding
# ":" or "," and a key holding "," can never match an entry there. Header values
# must also be printable ASCII for httpx to send them at all. Checked at boot so
# a typo is a refusal to start with a sentence, not a 503 on every payment.
_MERCHANT_ID_RE = re.compile(r"[\x21-\x2b\x2d-\x39\x3b-\x7e]{1,64}")
_MERCHANT_KEY_RE = re.compile(r"[\x21-\x2b\x2d-\x7e]{1,256}")
# backend/main.py DEMO_MERCHANT_ID: reserved for the core's demo namespace
# (/api/demo/charge), refused by the core for any real merchant.
_RESERVED_MERCHANT_IDS = frozenset({"demo"})
# The customer-reference key is an HMAC key nobody types: 32 characters is
# the floor below which it stops looking like `secrets.token_urlsafe(32)`
# output (43 characters) and starts looking like a password someone chose.
# A floor on length, not a measurement of entropy -- 32 repeated letters pass.
MIN_CUSTOMER_REF_KEY_LENGTH = 32


def _names(missing: list[str]) -> str:
    return missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " ve " + missing[-1]


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Reads the environment. Raises ConfigError, never falls back.

    There is deliberately no default merchant credential and no default
    customer-reference key, not even with DEBUG=1: a default key is a key
    everyone who has read this file holds, and the core would accept the
    merchant one for whichever merchant it was configured for.
    """
    env = os.environ if environ is None else environ
    merchant_id = (env.get("CHECKOUT_MERCHANT_ID") or "").strip()
    merchant_key = (env.get("CHECKOUT_MERCHANT_KEY") or "").strip()
    customer_ref_key = (env.get("CHECKOUT_CUSTOMER_REF_KEY") or "").strip()

    missing = [
        name
        for name, value in (
            ("CHECKOUT_MERCHANT_ID", merchant_id),
            ("CHECKOUT_MERCHANT_KEY", merchant_key),
            ("CHECKOUT_CUSTOMER_REF_KEY", customer_ref_key),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            f"{_names(missing)} tanımlı değil; ödeme sunucusu bunlar olmadan başlamaz "
            "(.env.example: CHECKOUT_MERCHANT_ID ve CHECKOUT_MERCHANT_KEY, DEEPCHECK_MERCHANT_KEYS içindeki "
            "bir 'id:anahtar' çiftiyle aynı olmalı; CHECKOUT_CUSTOMER_REF_KEY yalnızca bu sunucuya verilen "
            f"ayrı bir rastgele değerdir, en az {MIN_CUSTOMER_REF_KEY_LENGTH} karakter)"
        )
    if not _MERCHANT_ID_RE.fullmatch(merchant_id) or merchant_id in _RESERVED_MERCHANT_IDS:
        raise ConfigError(
            "CHECKOUT_MERCHANT_ID geçersiz: yalnızca ':' ve ',' içermeyen yazdırılabilir ASCII olabilir "
            "ve ayrılmış 'demo' kimliği kullanılamaz"
        )
    if not _MERCHANT_KEY_RE.fullmatch(merchant_key):
        raise ConfigError("CHECKOUT_MERCHANT_KEY geçersiz: yalnızca ',' içermeyen yazdırılabilir ASCII olabilir")
    if len(customer_ref_key) < MIN_CUSTOMER_REF_KEY_LENGTH:
        raise ConfigError(
            f"CHECKOUT_CUSTOMER_REF_KEY çok kısa: en az {MIN_CUSTOMER_REF_KEY_LENGTH} karakter olmalı "
            "(örneğin: python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
        )
    # The whole point of a separate key (customer_ref_for): the core holds the
    # merchant key. The same value under two names would undo that silently.
    if customer_ref_key == merchant_key:
        raise ConfigError(
            "CHECKOUT_CUSTOMER_REF_KEY, CHECKOUT_MERCHANT_KEY ile aynı olamaz: satıcı anahtarı DeepCheck "
            "çekirdeğinde de durur, müşteri referansı anahtarı yalnızca bu sunucuda durmalıdır"
        )

    core_url = (env.get("DEEPCHECK_CORE_URL") or "http://backend:8000").strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(:\d+)?(/[^\s]*)?", core_url):
        raise ConfigError("DEEPCHECK_CORE_URL geçersiz: http:// ya da https:// ile başlayan bir adres olmalı")

    debug = (env.get("DEBUG") or "0").strip() == "1"
    return Settings(
        core_url=core_url,
        merchant_id=merchant_id,
        merchant_key=merchant_key,
        customer_ref_key=customer_ref_key,
        debug=debug,
    )


# Short on purpose. The core answers a decision in milliseconds when it is
# healthy (/api/decision p95 7.4 ms with the profile layer off and 34.8 ms with
# it on, measured in the container topology before the 2026-09-25 retrain,
# docs/profile-evaluation.md section 11), so a call that has not answered in
# 4 s is a core in trouble, and the payer is better served by
# "try again in a few minutes" than by a spinner that never ends. Connect is
# shorter still: on the compose network a refused or unroutable connect is
# immediate.
CORE_TIMEOUT = httpx.Timeout(4.0, connect=2.0)


# --- The cart --------------------------------------------------------------------

# The amount is the server's. A client-sent amount is never read, so editing
# the page cannot make a 2038.80 TRY order cost 1 TRY. The page no longer
# lists the item (its order summary shows only Ara toplam, KDV and Toplam), but
# GET /api/cart still returns it, and the page's cart check expects a non-empty
# list (apps/checkout/src/lib/api.js).
CART_ITEMS = (
    {"name": "Mekanik Klavye - RGB Aydınlatmalı", "unit_price": Decimal("1699.00")},
)
VAT_RATE = Decimal("0.20")  # KDV %20
CURRENCY = "TRY"
_CENTS = Decimal("0.01")


def cart_totals() -> tuple[Decimal, Decimal, Decimal]:
    subtotal = sum((item["unit_price"] for item in CART_ITEMS), Decimal(0)).quantize(_CENTS)
    vat = (subtotal * VAT_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return subtotal, vat, subtotal + vat


# risk_context.amount_band, from the SERVER's total. The core records it on the
# decision audit row and enforces nothing on it in v1 (RiskContext in
# backend/main.py), so these cut-offs decide nothing today. They are judgement,
# not measurement -- no transaction data exists to fit them to:
#   low     total <  500 TRY
#   medium  500 TRY <= total < 5000 TRY
#   high    total >= 5000 TRY
# The demo cart (2038.80 TRY) is "medium".
AMOUNT_BANDS = ((Decimal(500), "low"), (Decimal(5000), "medium"))


def amount_band(total: Decimal) -> str:
    for upper, band in AMOUNT_BANDS:
        if total < upper:
            return band
    return "high"


def customer_ref_for(session_id: str, key: str) -> str:
    """The store's name for this checkout's customer, as sent to the core.

    Guest checkout: the page asks for no e-mail address, account or other
    identifier, so there is no customer to recognise across visits, and the
    reference is per SESSION. It still has to exist: the core writes a plain
    `allow` to decision_audit only when a customer is named (main.py
    _learn_and_audit), and the SOC's "Son kaydedilen karar" reads that record.
    One session gives one reference, so the hidden re-asks and the code step of
    one checkout are filed together, and no two checkouts share the per-customer
    profile budget -- a rehearsal cannot use up the stage run's.

    HMAC-SHA256 under CHECKOUT_CUSTOMER_REF_KEY, a key that never leaves this
    server, so the reference cannot be computed from the session id by anyone
    else and carries nothing about the payer. Never the merchant key: the core
    holds that one. 24 hex characters (96 bits) keep collisions out of reach
    while staying short in the SOC table.
    """
    message = ("session:" + session_id).encode("utf-8")
    digest = hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return "misafir-" + digest[:24]


# --- Request bodies ---------------------------------------------------------------

# Printable ASCII without spaces: the core's session ids are UUIDs and its
# tokens "<issued>.<mac>". A tighter shape is the core's business.
_OPAQUE = r"^[\x21-\x7e]+$"
CARD_BRANDS = ("visa", "mastercard", "troy", "amex")
# A card that expires further out than this is a typo, not a card.
MAX_CARD_YEARS_AHEAD = 20


class CardDisplay(BaseModel):
    """What the page may send about a card: what a receipt shows.

    No PAN, no CVV. A real store would hand those to its payment provider's
    own form and get a token back; here the page keeps them and sends only the
    display fields. extra="forbid", so a page that started sending `number`
    is refused instead of having it quietly accepted.
    """

    model_config = ConfigDict(extra="forbid")

    last4: str = Field(pattern=r"^[0-9]{4}$")
    brand: Literal["visa", "mastercard", "troy", "amex"]
    exp_month: StrictInt = Field(ge=1, le=12)
    exp_year: StrictInt = Field(ge=2000, le=2100)

    @model_validator(mode="after")
    def _not_expired(self) -> CardDisplay:
        now = datetime.now(UTC)
        if (self.exp_year, self.exp_month) < (now.year, now.month):
            raise ValueError("expired")
        if self.exp_year > now.year + MAX_CARD_YEARS_AHEAD:
            raise ValueError("expiry too far ahead")
        return self


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE)
    token: str = Field(min_length=1, max_length=256, pattern=_OPAQUE)
    card: CardDisplay


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE)
    token: str = Field(min_length=1, max_length=256, pattern=_OPAQUE)
    # The core's DEMO_VERIFY_CODE is configurable; the SPA asks for six digits.
    code: str = Field(pattern=r"^[0-9]{4,10}$")


# --- Rate limiting ------------------------------------------------------------------

# In-process sliding windows, keyed by client address, like the core's own
# limiter -- and with the same two stated limits: counters live in THIS
# process (run one worker; see the Dockerfile) and are lost on restart, which
# is acceptable for abuse control and would not be for billing.
#
# Ten a minute per address is judgement, not measurement: a person retrying a
# mistyped card a few times stays well inside it, and a script cycling cards
# through one address does not. One checkout counts once however many times the
# hidden insufficient-evidence retry below asks the core.
RATE_LIMITS = {
    "checkout": (10, 60.0),
    "verify": (10, 60.0),
}
_RATE_KEY_CAP = 20_000


class SlidingWindowLimiter:
    def __init__(self, limits: dict[str, tuple[int, float]], key_cap: int = _RATE_KEY_CAP):
        self.limits = limits
        self.key_cap = key_cap
        self.hits: dict[tuple[str, str], deque[float]] = {}

    def take(self, bucket: str, key: str, now: float | None = None) -> int | None:
        """Records one hit and returns None, or returns Retry-After seconds
        when the window is full (and records nothing)."""
        limit, window = self.limits[bucket]
        now = time.monotonic() if now is None else now
        hits = self.hits.get((bucket, key))
        if hits is None:
            if len(self.hits) >= self.key_cap:
                self._evict(now)
            hits = self.hits.setdefault((bucket, key), deque())
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(hits[0] + window - now) + 1)
        hits.append(now)
        return None

    def sweep(self, now: float | None = None) -> int:
        """Forgets every address whose window has fully passed. Returns how
        many were dropped. Run periodically (see _sweep_forever), so an
        address is held for about one window after its last request instead
        of until the key cap is reached."""
        now = time.monotonic() if now is None else now
        dead = [k for k, h in self.hits.items() if not h or h[-1] <= now - self.limits[k[0]][1]]
        for k in dead:
            self.hits.pop(k, None)
        return len(dead)

    def _evict(self, now: float) -> None:
        # An attacker rotating addresses must not grow this without bound.
        # Drop what has fully expired; if nothing has, the least recently
        # used half.
        dead = [k for k, h in self.hits.items() if not h or h[-1] <= now - self.limits[k[0]][1]]
        for k in dead:
            self.hits.pop(k, None)
        if len(self.hits) >= self.key_cap:
            oldest = sorted(self.hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
            for k, _ in oldest[: len(oldest) // 2]:
                self.hits.pop(k, None)


_limiter = SlidingWindowLimiter(RATE_LIMITS)


def _client_address(request: Request) -> str:
    """The peer address as uvicorn resolved it.

    Behind checkout-web's nginx the TCP peer is nginx for every visitor;
    uvicorn's proxy-headers middleware replaces it with X-Forwarded-For only
    when the peer is trusted (FORWARDED_ALLOW_IPS, set in the Dockerfile with
    the reason). nginx OVERWRITES that header with the address it saw
    (apps/checkout/nginx.conf), so a browser cannot choose its own bucket.
    """
    return request.client.host if request.client else "unknown"


def _rate_limited(bucket: str):
    async def dependency(request: Request) -> None:
        retry_after = _limiter.take(bucket, _client_address(request))
        if retry_after is not None:
            raise _RateLimited(retry_after)

    return dependency


class _RateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


# --- Pending challenges ---------------------------------------------------------------

# A checkout the core answered "verify" for. /api/checkout/verify is accepted
# ONLY for a session listed here, which keeps three things true:
#   * a session the core declined (block) cannot be steered into the OTP path;
#   * the decision after the code names the same customer reference and
#     amount band as the one before it (both stored here, not recomputed), so
#     the SOC sees both decisions of one checkout against one reference;
#   * the receipt shows the card the challenge was raised for.
# In memory, per process: a restart forgets pending challenges and the page
# then asks the payer to reload, which starts a fresh session.
PENDING_TTL_S = 600.0
PENDING_CAP = 10_000
# Wrong codes per challenge before the attempt is declined outright. The core
# also limits guesses per session (its "decision" bucket); this ends one
# challenge without waiting for that. Judgement, not measurement.
MAX_OTP_ATTEMPTS = 5


@dataclass
class PendingChallenge:
    customer_ref: str
    amount_band: str
    last4: str
    brand: str
    created: float = field(default_factory=time.monotonic)
    wrong_codes: int = 0


_pending: dict[str, PendingChallenge] = {}


def _remember_challenge(session_id: str, challenge: PendingChallenge) -> None:
    now = time.monotonic()
    _pending.pop(session_id, None)
    if len(_pending) >= PENDING_CAP:
        for sid in [s for s, c in _pending.items() if now - c.created > PENDING_TTL_S]:
            _pending.pop(sid, None)
        while len(_pending) >= PENDING_CAP:
            # Insertion order is age order, because a re-challenge re-inserts.
            _pending.pop(next(iter(_pending)))
    _pending[session_id] = challenge


def _pending_challenge(session_id: str) -> PendingChallenge | None:
    challenge = _pending.get(session_id)
    if challenge is not None and time.monotonic() - challenge.created > PENDING_TTL_S:
        _pending.pop(session_id, None)
        return None
    return challenge


def sweep_pending(now: float | None = None) -> int:
    """Drops every challenge older than PENDING_TTL_S. Returns how many."""
    now = time.monotonic() if now is None else now
    expired = [sid for sid, c in _pending.items() if now - c.created > PENDING_TTL_S]
    for sid in expired:
        _pending.pop(sid, None)
    return len(expired)


# How often the in-memory state is swept. The lookups above already ignore
# anything expired; the sweep is what actually lets go of it. Without it, a
# challenge the payer abandoned (closed the tab, pressed "Vazgeç") and a
# visitor's address stayed in memory until a restart or the key cap -- which
# is not what /gizlilik tells the payer.
SWEEP_INTERVAL_S = 15.0


async def _sweep_forever() -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_S)
        _limiter.sweep()
        sweep_pending()


def reset_state() -> None:
    """For tests: forget every counter and pending challenge."""
    _limiter.hits.clear()
    _pending.clear()


# --- Talking to the core ---------------------------------------------------------------


class CoreUnavailable(Exception):
    """The core did not give a usable answer. Always fails closed."""


class CoreRateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class CoreSessionGone(Exception):
    """The core no longer accepts this browser session. Either
    /api/demo/verify answered 404 (no observed session to attach a step-up
    to, or DEMO_ENDPOINTS is off), or a call answered 401 for the session
    TOKEN: expired after the core's SESSION_TOKEN_TTL_S (30 min from page
    load), or signed with a secret the core no longer holds. No code and no
    retry can help, and "try again in a few minutes" would loop for ever:
    only a new session can, which the page gets by reloading (answered 409,
    _session_gone)."""


# How the core words a refused merchant credential (backend/main.py,
# require_merchant). /api/decision checks the session token first and the
# merchant credential second, so its 401 is one or the other, and they must
# not be confused: a refused merchant credential is this server's
# misconfiguration -- every payment fails the same way, a reload cannot help,
# and the operator needs the log line to say so. Every other 401 there is the
# token's. test_core_401_details_match_the_core reads the core's source to
# keep this in step with it.
CORE_MERCHANT_REFUSED_DETAIL = "Yetkisiz satici"


def _core_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return detail if isinstance(detail, str) else ""


DECISION_ACTIONS = frozenset({"allow", "warn", "verify", "block"})

# The hidden retry. The core answers verify("insufficient_evidence") while
# fewer than three of the session's recent flushes were observed -- which is
# what a real payer who fills the card in quickly and presses "Öde" looks like
# for the first few seconds. Asking them for an SMS code then would be a false
# challenge; asking the core again after the SDK's next flushes (one every 2 s)
# usually is not. Three retries, 2 s apart (contract), then whatever the core
# says is final and a remaining "verify" is a challenge -- never a silent
# approval.
INSUFFICIENT_RETRIES = 3
RETRY_DELAY_S = 2.0
# Module-level so the tests can count the waits instead of sleeping them.
_sleep = asyncio.sleep


def _retry_after(response: httpx.Response) -> int:
    try:
        return max(1, int(response.headers.get("retry-after", "30")))
    except ValueError:
        return 30


async def _core_post(client: httpx.AsyncClient, path: str, *, body: dict, headers: dict) -> httpx.Response:
    try:
        return await client.post(path, json=body, headers=headers)
    except httpx.HTTPError as exc:
        # The class only. An httpx error message can carry the request URL,
        # and nothing about the payer belongs in a log line anyway.
        logger.warning("Çekirdeğe %s isteği başarısız (%s)", path, type(exc).__name__)
        raise CoreUnavailable() from None


async def ask_decision(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    session_id: str,
    token: str,
    customer_ref: str,
    band: str,
) -> tuple[str, str]:
    """One POST /api/decision. Returns (action, public reason).

    Only `action` and `reason` are read; the score, label and message are
    never even parsed into a variable, so no later edit can leak them into a
    response by accident.
    """
    response = await _core_post(
        client,
        "/api/decision",
        body={"session_id": session_id, "customer_ref": customer_ref, "risk_context": {"amount_band": band}},
        headers={
            "X-DeepCheck-Token": token,
            "X-Merchant-Id": settings.merchant_id,
            "X-Merchant-Key": settings.merchant_key,
        },
    )
    if response.status_code == 429:
        raise CoreRateLimited(_retry_after(response))
    if response.status_code == 401:
        if _core_detail(response) == CORE_MERCHANT_REFUSED_DETAIL:
            logger.error(
                "Çekirdek satıcı kimliğini reddetti: CHECKOUT_MERCHANT_ID / CHECKOUT_MERCHANT_KEY, "
                "DEEPCHECK_MERCHANT_KEYS içindeki bir çiftle uyuşmuyor"
            )
            raise CoreUnavailable()
        logger.info("Çekirdek oturum jetonunu kabul etmedi (401); sayfadan yenileme istenecek")
        raise CoreSessionGone()
    if response.status_code != 200:
        # No decision, and no decision is never a payment.
        logger.warning("Çekirdek /api/decision %s döndürdü", response.status_code)
        raise CoreUnavailable()
    try:
        payload = response.json()
    except ValueError:
        logger.warning("Çekirdek /api/decision JSON olmayan bir yanıt döndürdü")
        raise CoreUnavailable() from None
    action = payload.get("action") if isinstance(payload, dict) else None
    reason = payload.get("reason") if isinstance(payload, dict) else None
    if action not in DECISION_ACTIONS:
        logger.warning("Çekirdek /api/decision tanınmayan bir karar döndürdü")
        raise CoreUnavailable()
    return action, reason if isinstance(reason, str) else ""


async def decide_with_retry(client: httpx.AsyncClient, settings: Settings, **kwargs) -> str:
    for attempt in range(INSUFFICIENT_RETRIES + 1):
        action, reason = await ask_decision(client, settings, **kwargs)
        if action == "verify" and reason == "insufficient_evidence" and attempt < INSUFFICIENT_RETRIES:
            await _sleep(RETRY_DELAY_S)
            continue
        return action
    raise AssertionError("unreachable")  # pragma: no cover


async def submit_step_up(client: httpx.AsyncClient, *, session_id: str, token: str, code: str) -> bool:
    """POST /api/demo/verify. True: recorded. False: wrong code."""
    response = await _core_post(
        client,
        "/api/demo/verify",
        body={"session_id": session_id, "code": code},
        headers={"X-DeepCheck-Token": token},
    )
    if response.status_code == 400:
        return False
    # 401 here is always the session token: /api/demo/verify checks no
    # merchant credential (none is sent).
    if response.status_code in (401, 404):
        raise CoreSessionGone()
    if response.status_code == 429:
        raise CoreRateLimited(_retry_after(response))
    if response.status_code != 200:
        logger.warning("Çekirdek /api/demo/verify %s döndürdü", response.status_code)
        raise CoreUnavailable()
    try:
        payload = response.json()
    except ValueError:
        raise CoreUnavailable() from None
    if not isinstance(payload, dict) or payload.get("verified") is not True:
        raise CoreUnavailable()
    return True


# --- Responses --------------------------------------------------------------------------
#
# Every body below is built from literals and this server's own values. None
# of them is derived from a core response body, which is what keeps the
# score, label, reason and message off the payer's screen.

_NO_STORE = {"Cache-Control": "no-store"}


def _json(body: dict, status_code: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status_code, headers={**_NO_STORE, **(headers or {})})


def _paid(last4: str, brand: str) -> JSONResponse:
    _, _, total = cart_totals()
    return _json(
        {
            "status": "paid",
            # Not a payment reference: nothing was charged. Random so it says
            # nothing about how many orders came before it.
            "order_id": "TS-" + secrets.token_hex(5).upper(),
            "amount": float(total),
            "last4": last4,
            "brand": brand,
        }
    )


def _requires_action() -> JSONResponse:
    return _json({"status": "requires_action", "challenge": "otp"})


def _declined() -> JSONResponse:
    return _json({"status": "declined"})


def _unavailable() -> JSONResponse:
    return _json({"status": "error"}, status_code=503)


def _rate_limited_response(retry_after: int) -> JSONResponse:
    return _json(
        {"status": "error", "error": "rate_limited"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def _session_gone() -> JSONResponse:
    # The page answers this by asking the payer to reload, which starts a
    # new session. Not part of the contract's happy statuses: it is the
    # "no pending challenge / no observed session / session token refused"
    # case, where an OTP box or a 503's "try again later" could only loop.
    return _json({"status": "error", "error": "session"}, status_code=409)


# --- The app -------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn configures only its own loggers; without a root handler this
    # module's lines would reach stderr through logging's last-resort handler
    # at WARNING and above only.
    if not logging.getLogger().handlers:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.critical("Ödeme sunucusu başlatılmadı: %s", exc)
        raise
    logging.getLogger().setLevel(logging.DEBUG if settings.debug else logging.INFO)
    # The HTTP client's own loggers stay at WARNING in every mode: httpx logs
    # one INFO line per request, and httpcore's DEBUG trace follows each
    # request -- the one that carries the merchant key -- through the
    # connection. Nothing in either is needed to run a store; this module logs
    # its own failures (class and status only).
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    app.state.settings = settings
    # One client for the life of the process: connection reuse to the core,
    # and one place where the timeouts are set. Tests put a MockTransport in
    # app.state.core_transport before starting the app; production leaves it
    # unset and httpx opens real connections.
    app.state.client = httpx.AsyncClient(
        base_url=settings.core_url,
        timeout=CORE_TIMEOUT,
        transport=getattr(app.state, "core_transport", None),
        follow_redirects=False,
        headers={"User-Agent": "techstore-checkout/1"},
    )
    sweeper = asyncio.create_task(_sweep_forever())
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass
        await app.state.client.aclose()


app = FastAPI(
    title="TechStore checkout-api",
    lifespan=lifespan,
    # The interactive docs would list this API to anyone on the network; the
    # contract is docs/architecture-two-apps.md.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(RequestValidationError)
async def _invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default 422 echoes every offending input back, and the input
    # here is a session token and card details. Name the fields, never the
    # values.
    fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) or "body" for err in exc.errors()})
    return _json({"status": "error", "error": "invalid_request", "fields": fields}, status_code=422)


@app.exception_handler(_RateLimited)
async def _rate_limited_handler(_request: Request, exc: _RateLimited) -> JSONResponse:
    return _rate_limited_response(exc.retry_after)


def _client(request: Request) -> httpx.AsyncClient:
    return request.app.state.client


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    core_ok = False
    try:
        response = await _client(request).get("/api/health", timeout=2.0)
        core_ok = response.status_code == 200
    except httpx.HTTPError:
        core_ok = False
    return _json({"status": "sağlıklı" if core_ok else "çekirdeğe ulaşılamıyor", "core": core_ok})


@app.get("/api/cart")
async def cart() -> JSONResponse:
    subtotal, vat, total = cart_totals()
    return _json(
        {
            "items": [{"name": item["name"], "unit_price": float(item["unit_price"])} for item in CART_ITEMS],
            "subtotal": float(subtotal),
            "vat": float(vat),
            "total": float(total),
            "currency": CURRENCY,
        }
    )


@app.post("/api/checkout", dependencies=[Depends(_rate_limited("checkout"))])
async def checkout(payload: CheckoutRequest, request: Request) -> JSONResponse:
    settings = _settings(request)
    _, _, total = cart_totals()
    band = amount_band(total)
    customer_ref = customer_ref_for(payload.session_id, settings.customer_ref_key)

    try:
        action = await decide_with_retry(
            _client(request),
            settings,
            session_id=payload.session_id,
            token=payload.token,
            customer_ref=customer_ref,
            band=band,
        )
    except CoreSessionGone:
        _pending.pop(payload.session_id, None)
        return _session_gone()
    except CoreRateLimited as exc:
        return _rate_limited_response(exc.retry_after)
    except CoreUnavailable:
        return _unavailable()

    if action in ("allow", "warn"):
        # "warn" is a payment that goes through. Whatever the core wanted the
        # merchant to note is in its audit, for the SOC; the payer is not told
        # they looked unusual.
        _pending.pop(payload.session_id, None)
        return _paid(payload.card.last4, payload.card.brand)
    if action == "block":
        _pending.pop(payload.session_id, None)
        return _declined()
    _remember_challenge(
        payload.session_id,
        PendingChallenge(
            customer_ref=customer_ref,
            amount_band=band,
            last4=payload.card.last4,
            brand=payload.card.brand,
        ),
    )
    return _requires_action()


@app.post("/api/checkout/verify", dependencies=[Depends(_rate_limited("verify"))])
async def checkout_verify(payload: VerifyRequest, request: Request) -> JSONResponse:
    challenge = _pending_challenge(payload.session_id)
    if challenge is None:
        return _session_gone()

    client = _client(request)
    try:
        accepted = await submit_step_up(client, session_id=payload.session_id, token=payload.token, code=payload.code)
    except CoreSessionGone:
        # Typically the token pinned when the checkout was sent has outlived
        # the core's TTL while the payer read the code. The challenge can
        # never be completed under it: drop it, and the page asks for a
        # reload instead of "try again later" on every "Doğrula".
        _pending.pop(payload.session_id, None)
        return _session_gone()
    except CoreRateLimited as exc:
        return _rate_limited_response(exc.retry_after)
    except CoreUnavailable:
        return _unavailable()

    if not accepted:
        challenge.wrong_codes += 1
        if challenge.wrong_codes >= MAX_OTP_ATTEMPTS:
            _pending.pop(payload.session_id, None)
            return _declined()
        return _json({"status": "requires_action", "error": "code"})

    # The step-up is recorded on the core; the next decision for the session
    # is what spends it (one step-up = one approval, backend/main.py
    # _consume_step_up). No hidden retry here: a fresh step-up already lifts
    # "verify", whatever its reason.
    try:
        action, _reason = await ask_decision(
            client,
            _settings(request),
            session_id=payload.session_id,
            token=payload.token,
            customer_ref=challenge.customer_ref,
            band=challenge.amount_band,
        )
    except CoreSessionGone:
        _pending.pop(payload.session_id, None)
        return _session_gone()
    except CoreRateLimited as exc:
        return _rate_limited_response(exc.retry_after)
    except CoreUnavailable:
        # The challenge stays pending: pressing "Doğrula" again records the
        # step-up again and asks again, instead of forcing a reload because
        # the core blinked between the two calls.
        return _unavailable()

    # Answered: this challenge is over whatever the answer was.
    _pending.pop(payload.session_id, None)
    if action in ("allow", "warn"):
        return _paid(challenge.last4, challenge.brand)
    # "block" stays a block after any code. A "verify" that a fresh step-up
    # did not lift (a concurrent decision spent it first) ends the attempt
    # here rather than asking for another code: the payer has already done
    # what was asked, and an OTP box that can be cycled forever is not a
    # control.
    return _declined()
