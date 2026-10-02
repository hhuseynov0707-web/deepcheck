"""checkout-api against a scripted core (httpx.MockTransport). No network.

The properties that matter, each pinned by a test below:
  * every core outcome maps to the contracted status, and anything the core
    cannot answer cleanly is a 503 -- never "paid";
  * nothing from the core's answer (score, label, reason, message) reaches the
    browser, in any state;
  * the e-mail address never reaches the core, only its digest under a key
    the core does not hold;
  * the OTP path exists only for a session this server challenged;
  * a session token the core refuses is a 409 (reload), never a 503 that
    "try again later" would loop on.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import main
import pytest
from fastapi.testclient import TestClient

MERCHANT_ID = "techstore"
MERCHANT_KEY = "test-merchant-key-not-for-production"
CUSTOMER_REF_KEY = "test-customer-ref-key-not-for-production"
SESSION = "3f0c2a52-1b7e-4a55-9d1e-6b8f0a1c2d3e"
TOKEN = "1759320000.c2lnbmF0dXJl"
NEXT_YEAR = datetime.now(UTC).year + 1

# Distinctive values the core puts in every scripted verdict. If any of them
# turns up in a response body, the store leaked the core's answer.
CORE_SCORE = 87.65
CORE_LABEL = "Bot Tespit Edildi"
CORE_MESSAGE = "Islem Reddedildi - Supheli Davranis Tespit Edildi"
LEAK_MARKERS = (
    "risk_score",
    "87.65",
    "label",
    "Bot Tespit",
    "reason",
    "message",
    "Islem",
    "Supheli",
    "score",
    "step_up",
    "insufficient",
    "stale",
    "confidence",
)


def verdict(action: str, reason: str = "score") -> dict:
    return {
        "action": action,
        "risk_score": CORE_SCORE,
        "label": CORE_LABEL,
        "message": CORE_MESSAGE,
        "reason": reason,
    }


class FakeCore:
    """Replays scripted answers per path and records every request."""

    def __init__(self) -> None:
        self.queues: dict[str, list] = {}
        self.requests: list[httpx.Request] = []

    def reply(self, path: str, *answers) -> None:
        self.queues.setdefault(path, []).extend(answers)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.queues.get(request.url.path)
        if not queue:
            return httpx.Response(500, json={"detail": "no scripted answer"})
        answer = queue.pop(0)
        if isinstance(answer, type) and issubclass(answer, Exception):
            raise answer("scripted failure", request=request)
        if isinstance(answer, httpx.Response):
            return answer
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return httpx.Response(status, json=body)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def core() -> FakeCore:
    return FakeCore()


@pytest.fixture
def client(core, monkeypatch):
    monkeypatch.setenv("CHECKOUT_MERCHANT_ID", MERCHANT_ID)
    monkeypatch.setenv("CHECKOUT_MERCHANT_KEY", MERCHANT_KEY)
    monkeypatch.setenv("CHECKOUT_CUSTOMER_REF_KEY", CUSTOMER_REF_KEY)
    monkeypatch.setenv("DEEPCHECK_CORE_URL", "http://core.test")
    main.reset_state()
    sleeps: list[float] = []

    async def no_wait(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(main, "_sleep", no_wait)
    main.app.state.core_transport = httpx.MockTransport(core.handle)
    with TestClient(main.app) as test_client:
        test_client.sleeps = sleeps
        yield test_client
    main.app.state.core_transport = None
    main.reset_state()


def checkout_body(**overrides) -> dict:
    body = {
        "session_id": SESSION,
        "token": TOKEN,
        "card": {"last4": "1111", "brand": "visa", "exp_month": 12, "exp_year": NEXT_YEAR},
    }
    body.update(overrides)
    return body


def assert_no_leak(response) -> None:
    text = response.text
    for marker in LEAK_MARKERS:
        assert marker not in text, f"{marker!r} leaked into {text}"


# --- cart, health, amount band ---------------------------------------------------


def test_cart_is_the_fixed_demo_cart(client):
    response = client.get("/api/cart")
    assert response.status_code == 200
    assert response.json() == {
        "items": [{"name": "Mekanik Klavye - RGB Aydınlatmalı", "unit_price": 1699.0}],
        "subtotal": 1699.0,
        "vat": 339.8,
        "total": 2038.8,
        "currency": "TRY",
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("total", "band"),
    [("0.01", "low"), ("499.99", "low"), ("500", "medium"), ("2038.80", "medium"), ("4999.99", "medium"), ("5000", "high")],
)
def test_amount_band_thresholds(total, band):
    assert main.amount_band(Decimal(total)) == band


def test_health_reports_the_core(client, core):
    core.reply("/api/health", {"status": "sağlıklı"})
    assert client.get("/api/health").json() == {"status": "sağlıklı", "core": True}

    core.reply("/api/health", httpx.ConnectError)
    assert client.get("/api/health").json() == {"status": "çekirdeğe ulaşılamıyor", "core": False}


# --- status mapping ------------------------------------------------------------------


@pytest.mark.parametrize("action", ["allow", "warn"])
def test_allow_and_warn_are_paid(client, core, action):
    core.reply("/api/decision", verdict(action))
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "order_id", "amount", "last4", "brand"}
    assert body["status"] == "paid"
    assert body["amount"] == 2038.8
    assert body["last4"] == "1111"
    assert body["brand"] == "visa"
    assert body["order_id"].startswith("TS-")
    assert_no_leak(response)


def test_block_is_declined_and_says_nothing_else(client, core):
    core.reply("/api/decision", verdict("block"))
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 200
    assert response.json() == {"status": "declined"}
    assert_no_leak(response)


@pytest.mark.parametrize("reason", ["step_up", "stale", "score", "unknown_session"])
def test_verify_is_a_challenge(client, core, reason):
    core.reply("/api/decision", verdict("verify", reason))
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 200
    assert response.json() == {"status": "requires_action", "challenge": "otp"}
    assert len(core.calls("/api/decision")) == 1
    assert client.sleeps == []
    assert_no_leak(response)


def test_insufficient_evidence_is_retried_quietly(client, core):
    core.reply(
        "/api/decision",
        verdict("verify", "insufficient_evidence"),
        verdict("verify", "insufficient_evidence"),
        verdict("allow"),
    )
    response = client.post("/api/checkout", json=checkout_body())

    assert response.json()["status"] == "paid"
    assert len(core.calls("/api/decision")) == 3
    assert client.sleeps == [2.0, 2.0]


def test_insufficient_evidence_three_retries_then_a_challenge(client, core):
    core.reply("/api/decision", *[verdict("verify", "insufficient_evidence")] * 5)
    response = client.post("/api/checkout", json=checkout_body())

    assert response.json() == {"status": "requires_action", "challenge": "otp"}
    # One ask plus three retries, two seconds apart -- never a silent approval.
    assert len(core.calls("/api/decision")) == 4
    assert client.sleeps == [2.0, 2.0, 2.0]


def test_a_failure_during_the_retry_fails_closed(client, core):
    core.reply("/api/decision", verdict("verify", "insufficient_evidence"), (502, {"detail": "bad gateway"}))
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 503
    assert response.json() == {"status": "error"}


@pytest.mark.parametrize(
    "answer",
    [
        (500, {"detail": "boom"}),
        (502, {"detail": "bad gateway"}),
        (503, {"detail": "unavailable"}),
        (401, {"detail": main.CORE_MERCHANT_REFUSED_DETAIL}),
        (404, {"detail": "Not Found"}),
        (422, {"detail": []}),
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        httpx.ConnectError,
        httpx.Response(200, content=b"<html>not json</html>"),
        (200, {"action": "approve"}),
        (200, ["allow"]),
    ],
    ids=["500", "502", "503", "401-merchant", "404", "422", "read-timeout", "connect-timeout", "connect-error",
         "not-json", "unknown-action", "not-an-object"],
)
def test_core_failure_is_503_never_paid(client, core, answer):
    core.reply("/api/decision", answer)
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 503
    assert response.json() == {"status": "error"}


# What the core answers for a session token it no longer accepts
# (backend/main.py, _require_session_token), plus a 401 with no readable
# detail: anything but the merchant credential's own wording is the token's.
TOKEN_REFUSALS = [
    (401, {"detail": "Gecersiz oturum jetonu"}),
    (401, {"detail": "Oturum jetonunun suresi doldu"}),
    httpx.Response(401, content=b"<html>unauthorized</html>"),
]
TOKEN_REFUSAL_IDS = ["invalid", "expired", "not-json"]


@pytest.mark.parametrize("answer", TOKEN_REFUSALS, ids=TOKEN_REFUSAL_IDS)
def test_a_refused_session_token_asks_for_a_reload(client, core, answer):
    # The token was minted at page load and lives SESSION_TOKEN_TTL_S on the
    # core. Past that no retry can succeed, so "try again in a few minutes"
    # (503) would loop for ever; 409 is what the page turns into its reload
    # notice, and a reload starts a new session.
    core.reply("/api/decision", answer)
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 409
    assert response.json() == {"status": "error", "error": "session"}
    assert len(core.calls("/api/decision")) == 1


def test_a_refused_merchant_credential_is_a_503_and_says_so_in_the_log(client, core, caplog):
    # This server's misconfiguration, not the payer's session: a reload cannot
    # help, and the operator needs the log line to name the variables.
    caplog.set_level(logging.ERROR, logger="checkout")
    core.reply("/api/decision", (401, {"detail": main.CORE_MERCHANT_REFUSED_DETAIL}))

    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 503
    assert any("CHECKOUT_MERCHANT_KEY" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


def _function_source(source: str, name: str) -> str:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"backend/main.py has no function {name}")


def _401_details(function_source: str) -> set[str]:
    return set(re.findall(r'status_code=401, detail="([^"]*)"', function_source))


def test_core_401_details_match_the_core():
    # The store tells a refused merchant credential (503, its own fault) from
    # a refused session token (409, reload) by the core's `detail` text. Read
    # from the core's source, so a reworded detail there fails here instead of
    # quietly turning every misconfiguration into "reload the page".
    source = (Path(__file__).resolve().parents[2] / "backend" / "main.py").read_text(encoding="utf-8")

    assert _401_details(_function_source(source, "require_merchant")) == {main.CORE_MERCHANT_REFUSED_DETAIL}
    token_details = _401_details(_function_source(source, "_require_session_token"))
    assert token_details and main.CORE_MERCHANT_REFUSED_DETAIL not in token_details
    # The decision endpoint checks the token before the merchant credential,
    # so its 401 is one or the other, never a third kind.
    decision = _function_source(source, "decision")
    assert decision.index("_require_session_token(") < decision.index("_profile_request(")


def test_core_rate_limit_is_passed_on_as_429(client, core):
    core.reply("/api/decision", httpx.Response(429, json={"detail": "Cok fazla"}, headers={"Retry-After": "17"}))
    response = client.post("/api/checkout", json=checkout_body())

    assert response.status_code == 429
    assert response.json() == {"status": "error", "error": "rate_limited"}
    assert response.headers["retry-after"] == "17"


# --- what goes to the core ------------------------------------------------------------


def expected_ref(session_id: str, key: str = CUSTOMER_REF_KEY) -> str:
    digest = hmac.new(key.encode(), ("session:" + session_id).encode(), hashlib.sha256).hexdigest()
    return "misafir-" + digest[:24]


def test_decision_request_carries_token_merchant_credential_and_band(client, core):
    core.reply("/api/decision", verdict("allow"))
    client.post("/api/checkout", json=checkout_body())

    [request] = core.calls("/api/decision")
    assert request.method == "POST"
    assert request.headers["x-deepcheck-token"] == TOKEN
    assert request.headers["x-merchant-id"] == MERCHANT_ID
    assert request.headers["x-merchant-key"] == MERCHANT_KEY
    assert json.loads(request.content) == {
        "session_id": SESSION,
        "customer_ref": expected_ref(SESSION),
        "risk_context": {"amount_band": "medium"},
    }
    # The core holds the merchant key it was just sent; the reference must not
    # be computable from it.
    assert expected_ref(SESSION) != expected_ref(SESSION, key=MERCHANT_KEY)


def test_customer_ref_is_per_session_and_keyed():
    a = main.customer_ref_for(SESSION, CUSTOMER_REF_KEY)
    assert a == main.customer_ref_for(SESSION, CUSTOMER_REF_KEY) == expected_ref(SESSION)
    assert a.startswith("misafir-") and len(a) == len("misafir-") + 24
    assert all(c in "0123456789abcdef" for c in a[len("misafir-"):])
    # Keyed: another key gives another reference for the same session...
    assert main.customer_ref_for(SESSION, "another-customer-ref-key-0123456789") != a
    # ...and every checkout session is its own guest.
    assert main.customer_ref_for(SESSION + "x", CUSTOMER_REF_KEY) != a
    # Nothing of the session id is readable in the reference.
    assert SESSION not in a


def test_one_checkout_files_its_retries_and_code_step_under_one_reference(client, core):
    core.reply("/api/decision", verdict("verify", "step_up"))
    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", verdict("allow", "verified"))
    client.post("/api/checkout", json=checkout_body())
    client.post("/api/checkout/verify", json={"session_id": SESSION, "token": TOKEN, "code": "482913"})

    refs = [json.loads(r.content).get("customer_ref") for r in core.calls("/api/decision")]
    assert refs == [expected_ref(SESSION)] * 2


def test_an_email_field_is_refused_because_none_is_collected(client, core):
    # Guest checkout: the page sends no address, and the server refuses a body
    # that carries one rather than accept data it has no use for.
    response = client.post("/api/checkout", json={**checkout_body(), "email": "ayse@example.com"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert response.json().get("fields") == ["email"]
    assert core.requests == []


# --- validation -------------------------------------------------------------------------


def card(**overrides) -> dict:
    return {"last4": "1111", "brand": "visa", "exp_month": 12, "exp_year": NEXT_YEAR, **overrides}


@pytest.mark.parametrize(
    "body",
    [
        checkout_body(session_id=""),
        checkout_body(token="has space"),
        checkout_body(card=card(last4="12a4")),
        checkout_body(card=card(last4="11111")),
        checkout_body(card=card(brand="discover")),
        checkout_body(card=card(exp_month=13)),
        checkout_body(card=card(exp_month=0)),
        checkout_body(card=card(exp_month="12")),
        checkout_body(card=card(exp_year=2020)),
        checkout_body(card=card(exp_year=datetime.now(UTC).year + 40)),
        checkout_body(amount=1.0),
    ],
)
def test_invalid_input_is_refused_before_the_core(client, core, body):
    response = client.post("/api/checkout", json=body)

    assert response.status_code == 422
    assert response.json()["status"] == "error"
    assert response.json()["error"] == "invalid_request"
    assert core.requests == []


def test_a_full_card_number_or_cvv_is_refused_and_never_echoed(client, core):
    body = checkout_body(card={**card(), "number": "4111111111111111", "cvv": "737"})
    response = client.post("/api/checkout", json=body)

    assert response.status_code == 422
    assert "4111111111111111" not in response.text
    assert "737" not in response.text
    assert core.requests == []


def test_an_expired_card_is_refused(client, core):
    now = datetime.now(UTC)
    last_month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    response = client.post(
        "/api/checkout", json=checkout_body(card=card(exp_year=last_month[0], exp_month=last_month[1]))
    )
    assert response.status_code == 422

    core.reply("/api/decision", verdict("allow"))
    this_month = client.post("/api/checkout", json=checkout_body(card=card(exp_year=now.year, exp_month=now.month)))
    assert this_month.json()["status"] == "paid"


# --- OTP ------------------------------------------------------------------------------------


def challenge(client, core, last4="4444", brand="mastercard"):
    core.reply("/api/decision", verdict("verify", "step_up"))
    response = client.post("/api/checkout", json=checkout_body(card=card(last4=last4, brand=brand)))
    assert response.json() == {"status": "requires_action", "challenge": "otp"}


def verify(client, code="482913"):
    return client.post("/api/checkout/verify", json={"session_id": SESSION, "token": TOKEN, "code": code})


def test_verify_happy_path(client, core):
    challenge(client, core)
    core.reply("/api/demo/verify", {"verified": True, "message": "Ek dogrulama basariyla tamamlandi"})
    core.reply("/api/decision", verdict("allow", "verified"))

    response = verify(client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paid"
    assert (body["last4"], body["brand"], body["amount"]) == ("4444", "mastercard", 2038.8)
    assert_no_leak(response)

    [step_up] = core.calls("/api/demo/verify")
    assert json.loads(step_up.content) == {"session_id": SESSION, "code": "482913"}
    assert step_up.headers["x-deepcheck-token"] == TOKEN
    # The decision after the code names the same customer and band, so the SOC
    # sees both decisions against one reference.
    second = json.loads(core.calls("/api/decision")[1].content)
    assert second == {
        "session_id": SESSION,
        "customer_ref": expected_ref(SESSION),
        "risk_context": {"amount_band": "medium"},
    }
    # The challenge is spent: the same code again has nothing to verify.
    assert verify(client).status_code == 409


def test_wrong_code_stays_on_the_challenge_then_the_right_one_pays(client, core):
    challenge(client, core)
    core.reply("/api/demo/verify", (400, {"detail": "Dogrulama kodu hatali"}))

    wrong = verify(client, "000000")
    assert wrong.status_code == 200
    assert wrong.json() == {"status": "requires_action", "error": "code"}
    assert core.calls("/api/decision")[1:] == []

    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", verdict("allow", "verified"))
    assert verify(client).json()["status"] == "paid"


def test_too_many_wrong_codes_decline(client, core):
    challenge(client, core)
    core.reply("/api/demo/verify", *[(400, {"detail": "Dogrulama kodu hatali"})] * main.MAX_OTP_ATTEMPTS)

    answers = [verify(client, "000000").json() for _ in range(main.MAX_OTP_ATTEMPTS)]

    assert answers[:-1] == [{"status": "requires_action", "error": "code"}] * (main.MAX_OTP_ATTEMPTS - 1)
    assert answers[-1] == {"status": "declined"}
    assert verify(client).status_code == 409


def test_verify_without_a_challenge_is_refused_without_asking_the_core(client, core):
    response = verify(client)

    assert response.status_code == 409
    assert response.json() == {"status": "error", "error": "session"}
    assert core.requests == []


def test_a_declined_session_cannot_be_steered_into_the_otp_path(client, core):
    core.reply("/api/decision", verdict("block"))
    assert client.post("/api/checkout", json=checkout_body()).json() == {"status": "declined"}

    assert verify(client).status_code == 409
    assert core.calls("/api/demo/verify") == []


@pytest.mark.parametrize(("after", "status"), [("block", "declined"), ("verify", "declined"), ("warn", "paid")])
def test_decision_after_a_passed_code(client, core, after, status):
    challenge(client, core)
    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", verdict(after, "step_up"))

    response = verify(client)

    assert response.json()["status"] == status
    assert_no_leak(response)


def test_unknown_session_at_verify_asks_for_a_reload(client, core):
    challenge(client, core)
    core.reply("/api/demo/verify", (404, {"detail": "Oturum bulunamadi"}))

    response = verify(client)

    assert response.status_code == 409
    assert response.json() == {"status": "error", "error": "session"}


@pytest.mark.parametrize("answer", TOKEN_REFUSALS, ids=TOKEN_REFUSAL_IDS)
def test_a_refused_token_at_the_code_asks_for_a_reload_and_drops_the_challenge(client, core, answer):
    # The OTP is sent under the token pinned at checkout. If that token
    # expires while the payer reads the code, every "Doğrula" would fail the
    # same way; a 503's "try again later" would loop, so the store says
    # "reload" once and lets the challenge go.
    challenge(client, core)
    core.reply("/api/demo/verify", answer)

    response = verify(client)

    assert response.status_code == 409
    assert response.json() == {"status": "error", "error": "session"}
    assert SESSION not in main._pending
    assert verify(client).status_code == 409
    assert len(core.calls("/api/demo/verify")) == 1


@pytest.mark.parametrize("answer", TOKEN_REFUSALS, ids=TOKEN_REFUSAL_IDS)
def test_a_refused_token_after_a_passed_code_asks_for_a_reload(client, core, answer):
    challenge(client, core)
    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", answer)

    response = verify(client)

    assert response.status_code == 409
    assert response.json() == {"status": "error", "error": "session"}
    assert SESSION not in main._pending


def test_a_refused_token_at_checkout_drops_an_earlier_challenge(client, core):
    challenge(client, core)
    core.reply("/api/decision", (401, {"detail": "Oturum jetonunun suresi doldu"}))

    assert client.post("/api/checkout", json=checkout_body()).status_code == 409
    assert SESSION not in main._pending


@pytest.mark.parametrize(
    "answer",
    [(500, {"detail": "x"}), (502, {"detail": "x"}), httpx.ReadTimeout],
    ids=["500", "502", "read-timeout"],
)
def test_verify_core_failure_is_503_and_the_challenge_survives(client, core, answer):
    challenge(client, core)
    core.reply("/api/demo/verify", answer)

    response = verify(client)
    assert response.status_code == 503
    assert response.json() == {"status": "error"}

    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", verdict("allow", "verified"))
    assert verify(client).json()["status"] == "paid"


@pytest.mark.parametrize(
    "answer",
    [(503, {"detail": "x"}), (401, {"detail": main.CORE_MERCHANT_REFUSED_DETAIL})],
    ids=["503", "401-merchant"],
)
def test_decision_failure_after_a_passed_code_keeps_the_challenge(client, core, answer):
    challenge(client, core)
    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", answer)
    assert verify(client).status_code == 503

    core.reply("/api/demo/verify", {"verified": True, "message": "ok"})
    core.reply("/api/decision", verdict("allow", "verified"))
    assert verify(client).json()["status"] == "paid"


def test_malformed_code_is_refused(client, core):
    challenge(client, core)
    response = verify(client, "12ab56")
    assert response.status_code == 422
    assert core.calls("/api/demo/verify") == []


# --- rate limit ---------------------------------------------------------------------------


def test_checkout_is_rate_limited_per_address(client, core):
    limit, _window = main.RATE_LIMITS["checkout"]
    core.reply("/api/decision", *[verdict("allow")] * (limit + 1))

    statuses = [client.post("/api/checkout", json=checkout_body()).status_code for _ in range(limit + 1)]

    assert statuses == [200] * limit + [429]
    assert len(core.calls("/api/decision")) == limit
    refused = client.post("/api/checkout", json=checkout_body())
    assert refused.json() == {"status": "error", "error": "rate_limited"}
    assert int(refused.headers["retry-after"]) >= 1


def test_verify_is_rate_limited_per_address(client, core):
    limit, _window = main.RATE_LIMITS["verify"]
    statuses = [verify(client).status_code for _ in range(limit + 1)]
    assert statuses == [409] * limit + [429]


def test_limiter_keys_and_window():
    limiter = main.SlidingWindowLimiter({"b": (2, 60.0)})
    assert limiter.take("b", "10.0.0.1", now=0.0) is None
    assert limiter.take("b", "10.0.0.1", now=1.0) is None
    assert limiter.take("b", "10.0.0.1", now=2.0) is not None
    # Another address has its own bucket.
    assert limiter.take("b", "10.0.0.2", now=2.0) is None
    # The window slides.
    assert limiter.take("b", "10.0.0.1", now=61.0) is None


def test_sweep_forgets_addresses_after_their_window():
    limiter = main.SlidingWindowLimiter({"b": (5, 60.0)})
    limiter.take("b", "10.0.0.1", now=0.0)
    limiter.take("b", "10.0.0.2", now=30.0)

    assert limiter.sweep(now=59.0) == 0
    assert limiter.sweep(now=60.0) == 1  # 10.0.0.1's last hit is a full window old
    assert set(limiter.hits) == {("b", "10.0.0.2")}
    assert limiter.sweep(now=90.0) == 1
    assert limiter.hits == {}


def test_sweep_drops_abandoned_challenges(client, core):
    # A payer who closes the OTP dialog never tells the store; the sweep is
    # what lets the pending challenge (customer reference, last 4) go.
    challenge(client, core)
    created = main._pending[SESSION].created

    assert main.sweep_pending(now=created + main.PENDING_TTL_S) == 0
    assert SESSION in main._pending
    assert main.sweep_pending(now=created + main.PENDING_TTL_S + 1) == 1
    assert main._pending == {}


def test_http_client_loggers_stay_quiet_even_in_debug(core, monkeypatch):
    monkeypatch.setenv("DEBUG", "1")
    main.app.state.core_transport = httpx.MockTransport(core.handle)
    try:
        with TestClient(main.app):
            assert logging.getLogger().level == logging.DEBUG
            assert logging.getLogger("httpx").level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
    finally:
        main.app.state.core_transport = None


def test_limiter_memory_is_bounded():
    limiter = main.SlidingWindowLimiter({"b": (5, 60.0)}, key_cap=100)
    for i in range(1000):
        limiter.take("b", f"10.0.{i // 256}.{i % 256}", now=float(i) / 1000)
    assert len(limiter.hits) <= 100


# --- configuration ----------------------------------------------------------------------------


# A complete, valid environment; each test below breaks one thing in it.
VALID_ENV = {
    "CHECKOUT_MERCHANT_ID": "techstore",
    "CHECKOUT_MERCHANT_KEY": "k",
    "CHECKOUT_CUSTOMER_REF_KEY": CUSTOMER_REF_KEY,
}


def test_missing_settings_refuse_to_start():
    with pytest.raises(main.ConfigError) as all_three:
        main.load_settings({})
    for name in ("CHECKOUT_MERCHANT_ID", "CHECKOUT_MERCHANT_KEY", "CHECKOUT_CUSTOMER_REF_KEY"):
        assert name in str(all_three.value)

    with pytest.raises(main.ConfigError) as key_only:
        main.load_settings({**VALID_ENV, "CHECKOUT_MERCHANT_KEY": "", "DEBUG": "1"})
    assert str(key_only.value).startswith("CHECKOUT_MERCHANT_KEY tanımlı değil")

    # No fallback to the merchant key, not even with DEBUG=1.
    with pytest.raises(main.ConfigError) as ref_only:
        main.load_settings({"CHECKOUT_MERCHANT_ID": "techstore", "CHECKOUT_MERCHANT_KEY": "k", "DEBUG": "1"})
    assert str(ref_only.value).startswith("CHECKOUT_CUSTOMER_REF_KEY tanımlı değil")


@pytest.mark.parametrize(
    "overrides",
    [
        {"CHECKOUT_MERCHANT_ID": "demo"},
        {"CHECKOUT_MERCHANT_ID": "tech:store"},
        {"CHECKOUT_MERCHANT_KEY": "a,b"},
        {"CHECKOUT_MERCHANT_KEY": "anahtarı"},
        {"DEEPCHECK_CORE_URL": "backend:8000"},
    ],
)
def test_malformed_settings_refuse_to_start(overrides):
    with pytest.raises(main.ConfigError):
        main.load_settings({**VALID_ENV, **overrides})


def test_customer_ref_key_shorter_than_the_floor_refuses_to_start():
    floor = main.MIN_CUSTOMER_REF_KEY_LENGTH
    with pytest.raises(main.ConfigError) as short:
        main.load_settings({**VALID_ENV, "CHECKOUT_CUSTOMER_REF_KEY": "x" * (floor - 1)})
    assert "CHECKOUT_CUSTOMER_REF_KEY" in str(short.value)
    # Surrounding whitespace does not count towards the length.
    with pytest.raises(main.ConfigError):
        main.load_settings({**VALID_ENV, "CHECKOUT_CUSTOMER_REF_KEY": "  " + "x" * (floor - 1) + "  "})

    assert main.load_settings({**VALID_ENV, "CHECKOUT_CUSTOMER_REF_KEY": "x" * floor}).customer_ref_key == "x" * floor


def test_customer_ref_key_equal_to_the_merchant_key_refuses_to_start():
    # The core holds the merchant key; the same value under the other name
    # would hand it the pseudonymisation key too.
    shared = "s" * main.MIN_CUSTOMER_REF_KEY_LENGTH
    with pytest.raises(main.ConfigError) as same:
        main.load_settings({**VALID_ENV, "CHECKOUT_MERCHANT_KEY": shared, "CHECKOUT_CUSTOMER_REF_KEY": shared})
    assert "CHECKOUT_CUSTOMER_REF_KEY" in str(same.value) and "CHECKOUT_MERCHANT_KEY" in str(same.value)
    # Compared after trimming, as both are used.
    with pytest.raises(main.ConfigError):
        main.load_settings({**VALID_ENV, "CHECKOUT_MERCHANT_KEY": shared, "CHECKOUT_CUSTOMER_REF_KEY": f" {shared} "})


def test_settings_defaults():
    settings = main.load_settings(VALID_ENV)
    assert settings.core_url == "http://backend:8000"
    assert settings.debug is False
    assert settings.customer_ref_key == CUSTOMER_REF_KEY


def test_the_app_does_not_start_without_a_merchant_key(monkeypatch, caplog):
    monkeypatch.delenv("CHECKOUT_MERCHANT_KEY", raising=False)
    caplog.set_level(logging.CRITICAL, logger="checkout")

    with pytest.raises(main.ConfigError), TestClient(main.app):
        pass

    assert any("başlatılmadı" in r.getMessage() and "CHECKOUT_MERCHANT_KEY" in r.getMessage() for r in caplog.records)


def test_the_app_does_not_start_without_a_customer_ref_key(monkeypatch, caplog):
    monkeypatch.delenv("CHECKOUT_CUSTOMER_REF_KEY", raising=False)
    caplog.set_level(logging.CRITICAL, logger="checkout")

    with pytest.raises(main.ConfigError), TestClient(main.app):
        pass

    assert any(
        "başlatılmadı" in r.getMessage() and "CHECKOUT_CUSTOMER_REF_KEY" in r.getMessage() for r in caplog.records
    )
