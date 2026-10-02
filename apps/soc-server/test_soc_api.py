"""Tests for soc-api. The core is replaced by httpx.MockTransport, the clock by
a value the test moves, so nothing here needs a network or waits on time.

    cd apps/soc-server && python -m pytest -q

The module is soc_api, not main: backend/main.py (and any other service's
main.py) would otherwise collide with it in one pytest session.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
import pytest
from fastapi.testclient import TestClient

import soc_api

KEY = "pano-anahtari-test-0123456789"
SECRET = "imza-sirri-test-0123456789abcdefghijklmnop"
T0 = 1_790_000_000.0


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Core:
    """A stand-in for the DeepCheck core that records what it was asked."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.sessions_body = (
            # Deliberately not what json.dumps would produce (spacing, key
            # order, a non-ASCII label): the proxy must pass bytes, not a
            # re-serialisation.
            b'[ {"session_id": "abc-1", "risk_score": 93.4, "label": "Bot Tespit Edildi",'
            b' "is_synthetic": false, "z": 1, "a": "Ger\xc3\xa7ek"} ]'
        )
        self.reply: httpx.Response | Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        if self.reply is not None:
            return self.reply
        if request.url.path == "/api/sessions":
            return httpx.Response(200, content=self.sessions_body, headers={"content-type": "application/json"})
        if request.url.path == "/api/score/abc-1":
            return httpx.Response(200, json={"session_id": "abc-1", "history": [], "last_decision": None})
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "sağlıklı"})
        return httpx.Response(404, json={"detail": "Oturum bulunamadı"})


def settings(**overrides) -> soc_api.Settings:
    base = {"core_url": "http://core.test", "dashboard_key": KEY, "session_secret": SECRET, "cookie_secure": False}
    base.update(overrides)
    return soc_api.Settings(**base)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def core() -> Core:
    return Core()


@pytest.fixture
def client(core: Core, clock: Clock):
    app = soc_api.create_app(settings(), transport=httpx.MockTransport(core.handler), clock=clock)
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, key: str = KEY) -> httpx.Response:
    return client.post("/api/login", json={"key": key})


def cookie_value(response: httpx.Response) -> str:
    match = re.search(rf"{soc_api.COOKIE_NAME}=([^;]+)", response.headers["set-cookie"])
    assert match, response.headers.get("set-cookie")
    return match.group(1).strip('"')


# --- Configuration ---------------------------------------------------------------


@pytest.mark.parametrize(
    "env, needle",
    [
        ({"SOC_SESSION_SECRET": SECRET}, "DASHBOARD_KEY"),
        ({"DASHBOARD_KEY": KEY}, "SOC_SESSION_SECRET"),
        ({"DASHBOARD_KEY": KEY, "SOC_SESSION_SECRET": "   "}, "SOC_SESSION_SECRET"),
        ({"DASHBOARD_KEY": KEY, "SOC_SESSION_SECRET": "kisa"}, "en az"),
        ({"DASHBOARD_KEY": SECRET, "SOC_SESSION_SECRET": SECRET}, "aynı olamaz"),
        (
            {"DASHBOARD_KEY": KEY, "SOC_SESSION_SECRET": SECRET, "DEEPCHECK_CORE_URL": "backend:8000"},
            "DEEPCHECK_CORE_URL",
        ),
    ],
)
def test_refuses_to_start_without_its_secrets(env, needle):
    with pytest.raises(soc_api.ConfigError) as caught:
        soc_api.Settings.from_env(env)
    assert needle in str(caught.value)
    # A boot error is copied into every log; it names the variable, never a value.
    assert KEY not in str(caught.value) and SECRET not in str(caught.value)


def test_reads_the_environment_with_safe_defaults():
    loaded = soc_api.Settings.from_env({"DASHBOARD_KEY": KEY, "SOC_SESSION_SECRET": SECRET})
    assert loaded.core_url == "http://backend:8000"
    assert loaded.cookie_secure is False
    loaded = soc_api.Settings.from_env(
        {
            "DASHBOARD_KEY": KEY,
            "SOC_SESSION_SECRET": SECRET,
            "SOC_COOKIE_SECURE": "1",
            "DEEPCHECK_CORE_URL": "http://x:9/",
        }
    )
    assert loaded.cookie_secure is True
    assert loaded.core_url == "http://x:9"


def test_the_uvicorn_factory_reads_the_environment(monkeypatch):
    monkeypatch.delenv("DASHBOARD_KEY", raising=False)
    monkeypatch.setenv("SOC_SESSION_SECRET", SECRET)
    with pytest.raises(soc_api.ConfigError):
        soc_api.create_app()


# --- Login and the cookie ------------------------------------------------------------


def test_login_sets_a_signed_httponly_strict_cookie_scoped_to_api(client):
    response = login(client)
    assert response.status_code == 204
    header = response.headers["set-cookie"]
    flags = [part.strip().lower() for part in header.split(";")]
    assert "httponly" in flags
    assert "samesite=strict" in flags
    assert "path=/api" in flags
    assert f"max-age={8 * 3600}" in flags
    # Plain http on loopback by default: a Secure cookie would never come back.
    assert "secure" not in flags
    value = cookie_value(response)
    assert re.fullmatch(r"v1\.\d+\.[A-Za-z0-9_-]+\.[0-9a-f]{64}", value)
    assert KEY not in header
    assert response.headers["cache-control"] == "no-store"


def test_secure_cookie_when_configured(core, clock):
    app = soc_api.create_app(settings(cookie_secure=True), transport=httpx.MockTransport(core.handler), clock=clock)
    with TestClient(app) as secure_client:
        header = login(secure_client).headers["set-cookie"]
    assert "secure" in [part.strip().lower() for part in header.split(";")]


def test_wrong_key_is_401_in_turkish_and_sets_nothing(client, core):
    response = login(client, "yanlis-anahtar")
    assert response.status_code == 401
    assert response.json() == {"detail": soc_api.MSG_BAD_KEY}
    assert "set-cookie" not in response.headers
    assert client.get("/api/me").status_code == 401
    assert core.requests == []


@pytest.mark.parametrize("key", ["anahtarş", "İstanbul-ğüşöç", KEY + "ı", "", " " + KEY])
def test_non_ascii_or_near_miss_key_is_401_not_500(client, key):
    # hmac.compare_digest on two str raises TypeError for non-ASCII input;
    # compared as bytes, it is simply a wrong key.
    response = login(client, key)
    assert response.status_code == 401
    assert response.json() == {"detail": soc_api.MSG_BAD_KEY}


def test_a_non_ascii_dashboard_key_works_when_typed_exactly(core, clock):
    app = soc_api.create_app(
        settings(dashboard_key="gizli-ŞİFRE-ğüş-123"), transport=httpx.MockTransport(core.handler), clock=clock
    )
    with TestClient(app) as unicode_client:
        assert login(unicode_client, "gizli-ŞİFRE-ğüş-123").status_code == 204
        assert login(unicode_client, "gizli-SIFRE-gus-123").status_code == 401


def test_oversized_or_malformed_body_is_a_turkish_422_that_echoes_nothing(client):
    response = client.post("/api/login", json={"key": "x" * 5000})
    assert response.status_code == 422
    assert response.json() == {"detail": soc_api.MSG_BAD_REQUEST}
    assert "xxxx" not in response.text
    # A form post is not JSON: refused, so a cross-site form cannot log in.
    assert client.post("/api/login", data={"key": KEY}).status_code == 422
    assert client.post("/api/login", json={}).status_code == 422


def test_login_attempts_are_rate_limited_per_address_and_recover(client, clock):
    for _ in range(soc_api.LOGIN_LIMIT_PER_ADDRESS):
        assert login(client, "yanlis").status_code == 401
    # The limit counts attempts, so even the right key waits.
    blocked = login(client)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": soc_api.MSG_RATE_LIMITED}
    assert blocked.headers["retry-after"] == "60"
    assert "set-cookie" not in blocked.headers

    clock.now += soc_api.LOGIN_WINDOW_S + 1
    assert login(client).status_code == 204


def test_the_overall_login_limit_holds_across_addresses():
    limiter = soc_api._LoginLimiter(per_address=10, overall=50, window_s=60)
    allowed = sum(limiter.allow(f"10.0.0.{i}", T0) for i in range(200))
    assert allowed == 50
    assert limiter.allow("10.0.1.1", T0 + 61) is True


def test_me_reports_the_session(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me").json() == {"detail": soc_api.MSG_UNAUTHENTICATED}
    login(client)
    assert client.get("/api/me").status_code == 204


def test_an_expired_cookie_is_rejected(client, clock, core):
    login(client)
    clock.now += 8 * 3600 - 1
    assert client.get("/api/me").status_code == 204
    clock.now += 2
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/sessions").status_code == 401
    assert core.requests == []


def _with_cookie(client: TestClient, path: str, value: str) -> httpx.Response:
    client.cookies.clear()
    return client.get(path, headers={"Cookie": f"{soc_api.COOKIE_NAME}={value}"})


def test_a_tampered_cookie_is_rejected(client, core):
    value = cookie_value(login(client))
    assert _with_cookie(client, "/api/me", value).status_code == 204

    version, expiry, nonce, signature = value.split(".")
    flipped = "0" if signature[-1] != "0" else "1"
    forged = [
        f"{version}.{int(expiry) + 3600}.{nonce}.{signature}",  # extended expiry
        f"{version}.{expiry}.{nonce}x.{signature}",  # another nonce
        f"{version}.{expiry}.{nonce}.{signature[:-1]}{flipped}",  # bit flip
        f"v2.{expiry}.{nonce}.{signature}",  # another version
        f"{version}.{expiry}.{nonce}",  # no signature
        "",
        "garbage",
        KEY,  # the key itself is not a session
    ]
    for bad in forged:
        assert _with_cookie(client, "/api/me", bad).status_code == 401, bad
        assert _with_cookie(client, "/api/sessions", bad).status_code == 401, bad
    assert core.requests == []


def test_a_cookie_signed_with_another_secret_is_rejected(clock):
    value, _, _ = soc_api.issue_cookie("ein-anderes-geheimnis-0123456789abcdef", clock())
    assert soc_api.read_cookie(value, SECRET, clock()) is None
    assert soc_api.read_cookie(value, "ein-anderes-geheimnis-0123456789abcdef", clock()) is not None


def test_a_cookie_valid_longer_than_the_ttl_is_rejected(clock):
    # Signed with the right secret but further out than one TTL: the TTL was
    # shortened since it was issued, and the current rule wins.
    expiry = int(clock()) + soc_api.SESSION_TTL_S + 3600
    nonce = "abcdefghijklmnop"
    value = f"v1.{expiry}.{nonce}.{soc_api._sign(SECRET, expiry, nonce)}"
    assert soc_api.read_cookie(value, SECRET, clock()) is None


def test_logout_clears_the_cookie_and_revokes_a_copy_of_it(client):
    value = cookie_value(login(client))
    response = client.post("/api/logout")
    assert response.status_code == 204
    header = response.headers["set-cookie"].lower()
    assert f"{soc_api.COOKIE_NAME}=" in header and "path=/api" in header
    assert "max-age=0" in header or "expires=" in header
    assert client.get("/api/me").status_code == 401
    # A copy taken before logout no longer works either.
    assert _with_cookie(client, "/api/me", value).status_code == 401


def test_logout_without_a_session_still_answers_204(client):
    assert client.post("/api/logout").status_code == 204


def test_another_analysts_cookie_survives_one_logout(client):
    first = cookie_value(login(client))
    client.cookies.clear()
    second = cookie_value(login(client))
    _with_cookie(client, "/api/me", first)
    client.post("/api/logout", headers={"Cookie": f"{soc_api.COOKIE_NAME}={first}"})
    assert _with_cookie(client, "/api/me", first).status_code == 401
    assert _with_cookie(client, "/api/me", second).status_code == 204


# --- The proxy ---------------------------------------------------------------------


def test_sessions_needs_a_session_and_never_reaches_the_core_without_one(client, core):
    response = client.get("/api/sessions")
    assert response.status_code == 401
    assert response.json() == {"detail": soc_api.MSG_UNAUTHENTICATED}
    assert client.get("/api/score/abc-1").status_code == 401
    assert core.requests == []


def test_sessions_passes_the_core_json_through_with_the_key_header(client, core):
    login(client)
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.content == core.sessions_body
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"

    (upstream,) = core.requests
    assert upstream.method == "GET"
    assert str(upstream.url) == "http://core.test/api/sessions"
    assert upstream.headers["x-dashboard-key"] == KEY
    # Nothing of the browser's request goes on -- its session cookie above all.
    assert "cookie" not in upstream.headers


def test_score_is_forwarded_for_a_valid_id(client, core):
    login(client)
    response = client.get("/api/score/abc-1")
    assert response.status_code == 200
    assert response.json() == {"session_id": "abc-1", "history": [], "last_decision": None}
    assert core.requests[-1].url.path == "/api/score/abc-1"
    assert core.requests[-1].headers["x-dashboard-key"] == KEY


def test_a_uuid_session_id_is_accepted(client, core):
    login(client)
    client.get("/api/score/0b6f3c1e-8a7d-4c1b-9f2e-1a2b3c4d5e6f")
    assert core.requests[-1].url.path == "/api/score/0b6f3c1e-8a7d-4c1b-9f2e-1a2b3c4d5e6f"


@pytest.mark.parametrize("bad", ["a" * 129, "bad.id", "with%20space", "%C5%9F", "abc%3Fx%3D1", "abc;x", "..."])
def test_an_unsafe_session_id_is_refused_before_the_core(client, core, bad):
    login(client)
    response = client.get(f"/api/score/{bad}")
    assert response.status_code == 400
    assert response.json() == {"detail": soc_api.MSG_BAD_SESSION_ID}
    assert core.requests == []


def test_an_unknown_session_is_a_turkish_404(client, core):
    login(client)
    response = client.get("/api/score/yok-0001")
    assert response.status_code == 404
    assert response.json() == {"detail": soc_api.MSG_NOT_FOUND}


@pytest.mark.parametrize(
    "reply, detail",
    [
        (httpx.Response(500, json={"detail": "boom"}), soc_api.MSG_CORE_ERROR),
        (httpx.Response(503, text="Service Unavailable"), soc_api.MSG_CORE_ERROR),
        (httpx.Response(502, text="<html>Bad Gateway</html>"), soc_api.MSG_CORE_ERROR),
        (httpx.Response(422, json={"detail": []}), soc_api.MSG_CORE_ERROR),
        (httpx.Response(302, headers={"location": "/elsewhere"}), soc_api.MSG_CORE_ERROR),
        # The core rejecting OUR key is a configuration fault, not the
        # analyst's: a 401 would send the SPA back to its login screen forever.
        (httpx.Response(401, json={"detail": "Yetkisiz erisim"}), soc_api.MSG_CORE_REJECTED_KEY),
        (httpx.Response(200, text="<html>not json</html>"), soc_api.MSG_CORE_BAD_BODY),
        (httpx.ReadTimeout("slow"), soc_api.MSG_CORE_UNREACHABLE),
        (httpx.ConnectError("refused"), soc_api.MSG_CORE_UNREACHABLE),
        (httpx.ConnectTimeout("no route"), soc_api.MSG_CORE_UNREACHABLE),
    ],
)
def test_core_failures_become_502_with_a_turkish_detail(client, core, reply, detail):
    login(client)
    core.reply = reply
    for path in ("/api/sessions", "/api/score/abc-1"):
        response = client.get(path)
        assert response.status_code == 502, path
        assert response.json() == {"detail": detail}
        assert response.headers["cache-control"] == "no-store"
    # The SOC login itself is untouched by a core failure.
    core.reply = None
    assert client.get("/api/me").status_code == 204


def test_an_unknown_route_answers_in_turkish(client):
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.json() == {"detail": soc_api.MSG_ROUTE_NOT_FOUND}
    response = client.delete("/api/sessions")
    assert response.status_code == 405
    assert response.json() == {"detail": soc_api.MSG_METHOD_NOT_ALLOWED}


def test_health_says_whether_the_core_answers_without_a_session(client, core):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "core": True}
    core.reply = httpx.ConnectError("down")
    assert client.get("/api/health").json() == {"status": "ok", "core": False}
    core.reply = httpx.Response(500)
    assert client.get("/api/health").json() == {"status": "ok", "core": False}


def test_neither_the_key_nor_the_cookie_is_ever_logged(client, core, caplog):
    caplog.set_level(logging.DEBUG)
    login(client, "yanlis-ama-gizli-deneme")
    value = cookie_value(login(client))
    client.get("/api/sessions")
    client.get("/api/score/abc-1")
    core.reply = httpx.Response(401)
    client.get("/api/sessions")
    core.reply = httpx.ReadTimeout("slow")
    client.get("/api/sessions")
    client.post("/api/logout")
    text = caplog.text
    assert text, "expected the login and proxy paths to log something"
    for secret in (KEY, SECRET, value, value.split(".")[-1], "yanlis-ama-gizli-deneme"):
        assert secret not in text
    # And no client address either.
    assert "testclient" not in text


def test_no_api_docs_are_served(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_the_core_body_reaches_the_browser_byte_for_byte_even_with_unicode(client, core):
    payload = {"label": "Gerçek Kullanıcı", "reason": "Değerlendiriliyor", "n": [1, 2.50, None]}
    core.sessions_body = json.dumps(payload, ensure_ascii=False, indent=3).encode("utf-8")
    login(client)
    assert client.get("/api/sessions").content == core.sessions_body
