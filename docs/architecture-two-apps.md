# Two apps around the core: store checkout and SOC

Status: design, 2026-10-01. Implemented in `apps/`. Supersedes the single page
that showed the risk score to the payer. Revised the same day after review: the
SDK's analyze reply on the store no longer carries the score, the customer
reference has a key of its own, and the cookie and rate-limit scopes below are
stated as they are, not as intended. Revised 2026-10-02: that single-page demo
(`frontend/`, its compose service on port 3200, `docker-compose.dev.yml` and its
CI job) is **deleted**, not kept beside the two apps; the store is a guest
checkout that asks for no e-mail address, and its server names each checkout
by a per-session reference (below).

## Why

The old demo (deleted 2026-10-02) put the checkout and the analyst view in one
frontend, and the checkout page showed the live risk score to the person paying. That is not how
the product is meant to be deployed, and it gives the scored party a tuning
signal (CLAUDE.md, rule 3). Hiding the score in the UI was not enough on its
own: the first version of the store still received it in the SDK's
`/api/analyze` reply every 2 s, readable in DevTools or from a script. The core
now answers the store's SDK calls with an acknowledgement only (below). The
jury demo now shows the real integration:

- a **store** (TechStore) whose checkout looks like any modern hosted payment
  page and shows **no score, band, label or reason** to the payer;
- the store's **own server**, which asks the core `POST /api/decision`
  server-to-server -- the production path, with the merchant key -- and then
  charges, challenges or declines;
- a separate **SOC** app for the analyst, with its own server holding the
  dashboard key, so the key never reaches a browser.

## Components

```
 laptop B (payer / bot)                         laptop A (presenter)
 ---------------------                          --------------------------------------------
 browser ──http://IP:3000──►  checkout-web (nginx, :3000, LAN when BIND_ADDR=0.0.0.0)
                                │  /               → checkout SPA (apps/checkout)
                                │  /deepcheck.js   → SDK file (sdk/deepcheck.js)
                                │  /deepcheck/api/ → backend:8000/api/   (allow list: session, attest, analyze, health;
                                │                                         adds X-DeepCheck-Reply: ack)
                                │  /api/           → checkout-api:8100/api/
                                ▼
                              checkout-api (FastAPI, internal)  ── X-DeepCheck-Token, X-Merchant-Id/Key ──►
                                                                                                          backend (core)
 analyst browser ──http://localhost:3100──► soc-web (nginx, 127.0.0.1:3100)                               :8000, internal + loopback
                                │  /     → SOC SPA (apps/soc)                                              ▲
                                │  /api/ → soc-api:8200/api/                                               │
                                ▼                                                                         │
                              soc-api (FastAPI, internal, holds DASHBOARD_KEY) ── X-Dashboard-Key ────────┘
```

Six compose services: db, backend (the core), checkout-api, checkout-web,
soc-api, soc-web. Only checkout-web is ever published beyond loopback. The SOC
(3100) and the core (8000) are reachable from laptop A only, whatever BIND_ADDR
says (8000 opens only with `API_BIND_ADDR`). Nothing listens on 3200 any more.

## Contracts

### Core -- what the new apps call

- `POST /api/analyze` (the SDK, through checkout-web's `/deepcheck/api/`).
  checkout-web's nginx sets `X-DeepCheck-Reply: ack` on every SDK call it
  forwards (`proxy_set_header`, so it replaces whatever the browser sent under
  that name), and the core then answers `{session_id, accepted: true}` and
  nothing else: no score, label, confidence, SHAP or timing
  (`ANALYZE_ACK_REPLY`, `AnalyzeAck` in `backend/main.py`). The window is
  checked, scored and stored exactly as without the header, and a refused
  window still gets its error status, so `accepted` is never sent for a window
  that was not taken. The SDK fires `onUpdate` and the `deepcheck:update`
  event only for a reply that carries a score, so the payer's browser on
  :3000 receives no score anywhere, DevTools included. Without the header the
  reply is the full one, which the lab tools still get (`lab/bot_lab.py` and
  `live_bot.py --legacy`, both against the core's own loopback port :8000).
  The header can only remove information, so a client that sends it itself
  gains nothing. What it does not cover: a client holding a session token that
  can reach the core's port directly -- on laptop A itself, or from the network
  with `API_BIND_ADDR=0.0.0.0` -- can post its window there and read the full
  reply.
- `POST /api/decision` body `{session_id, customer_ref?, risk_context?}`;
  headers `X-DeepCheck-Token` (the browser session's token, required),
  `X-Merchant-Id` + `X-Merchant-Key` (required when `customer_ref` is sent).
  Returns `{action: allow|warn|verify|block, risk_score, label, message, reason}`
  with the PUBLIC reason (`score|verified|insufficient_evidence|stale|unknown_session|step_up`).
- `POST /api/demo/verify` body `{session_id, code}`, header `X-DeepCheck-Token`
  (needs `DEMO_ENDPOINTS=1`). Records a passed step-up; the next decision for
  the session is `allow` with reason `verified` (one step-up = one approval).
- `GET /api/sessions`, `GET /api/score/{id}`, header `X-Dashboard-Key`.

A customer reference with no stored profile still yields a profile context
(`no_profile`), so the decision IS written to `decision_audit` and the SOC can
show it, including a plain `allow` -- while the profile layer is on
(`PROFILE_LAYER=1`); with it off nothing is written. No consent call is made:
no biometric profile is created for checkout customers. Each row holds the
session id, the merchant id, the action, the internal and the public reason,
the score at decision time and the amount band; no customer reference and no
profile id (a `no_profile` customer has none). The store's merchant id is not
the reserved `demo` namespace, so these rows are kept for
`DECISION_AUDIT_RETENTION_DAYS` (default 90 days), not the demo namespace's
24 hours; one checkout can write up to four of them (the hidden retry) and
the OTP step one more. `docs/kvkk-aydinlatma.md` says so to the payer.

Every decision that names a customer also spends that customer's bucket in
the core's per-customer limit (`RATE_LIMITS["profile"]`, 60 per hour per
worker), hidden retries included, whether or not a profile exists: the bucket
is taken before the profile row is read. Past it, with
`PROFILE_ESCALATION=1`, an approval becomes the code step for up to an hour.
The store's reference is per session (below), so every checkout has a bucket
of its own: an ordinary checkout spends up to five decisions from it (four
asks plus the code step), and rehearsals cannot use up the stage payment's
budget. This
was not so while the reference came from an e-mail address, when rehearsing
with one address could change what the stage payment did.

### checkout-api (store server)

- `GET /api/health` → `{status, core: bool}`.
- `GET /api/cart` → the fixed demo cart `{items:[{name, unit_price}], subtotal, vat, total, currency:"TRY"}`.
  The amount is the server's; a client-sent amount is never trusted. The page
  shows only the amounts (Ara toplam, KDV, Toplam): no product line and no
  illustration. The item stays in the server's cart.
- `POST /api/checkout` body
  `{session_id, token, card: {last4, brand, exp_month, exp_year}}`, and nothing
  else (`extra="forbid"`). Guest checkout: the page asks for no e-mail address,
  account or other identifier. The browser never sends the full card number,
  the CVV or the cardholder name: the SPA keeps them and sends only the display
  fields (a stand-in for a payment provider's tokenisation). The name field
  drops digits and symbols as they are typed ("İsimde rakam kullanılamaz.").
  `customer_ref` =
  `"misafir-" + HMAC-SHA256(CHECKOUT_CUSTOMER_REF_KEY, "session:" + session_id)[:24]`
  (`customer_ref_for`), one reference per session. With no identifier there is
  no customer to recognise across visits; the reference exists because the
  core writes a plain `allow` to `decision_audit` only when a customer is named
  (`main._learn_and_audit`), and the SOC's "Son kaydedilen karar" reads that
  row. One session gives one reference, so the hidden re-asks and the code
  step of one checkout are filed together, and no two checkouts share a
  per-customer bucket. The key is held by the store server only, so nobody
  else can compute the reference from the session id; it carries nothing about
  the payer. It is deliberately not the merchant key: the core holds that one
  too (`DEEPCHECK_MERCHANT_KEYS`) and receives it with every decision.
  checkout-api refuses to start when the key is missing, shorter than 32
  characters or equal to `CHECKOUT_MERCHANT_KEY`.
  Calls `/api/decision` with `risk_context.amount_band`. Maps:
  - `allow` / `warn` → `200 {status: "paid", order_id, amount, last4, brand}`;
  - `verify` with public reason `insufficient_evidence` → the server waits 2 s
    and asks again, at most 3 times, then treats it as a challenge;
  - `verify` (any other reason) → `200 {status: "requires_action", challenge: "otp"}`;
  - `block` → `200 {status: "declined"}`;
  - the core refuses the browser session's token (401: older than the core's
    30-minute `SESSION_TOKEN_TTL_S`, or signed with a secret it no longer
    holds) → `409 {status: "error", error: "session"}`; the page asks for a
    reload, which starts a new session;
  - core unreachable / 5xx / a refused merchant credential → `503 {status: "error"}`
    (fail closed: never paid).
  The response carries **no** score, label, reason or message from the core.
- `POST /api/checkout/verify` body `{session_id, token, code}` → core
  `/api/demo/verify`, then `/api/decision` again → `paid` | `declined` |
  `{status:"requires_action", error:"code"}` on a wrong code. The token is the
  one pinned when the checkout was sent; if it has expired by the time the
  code is typed, or the core has no observed session to attach the step-up
  to, the answer is the same `409` (reload), not a `503` that "try again
  later" could never fix.
- Rate limit on `/api/checkout*` (in memory, 10 per minute each), keyed by the
  address checkout-web's nginx saw. Under Docker Desktop that is **not** the
  visitor's address: Docker Desktop's port forwarder hands nginx the compose
  network's gateway as the peer (measured from the host: `/proc/net/tcp` in
  deepcheck-checkout-web showed `172.20.0.1`, the gateway of
  `172.20.0.0/16`). So on the jury laptop the limits are per **host**, one
  bucket for everyone: a bot looping from laptop A and a person paying from
  laptop B draw from it together. LAN connections were not measured from a
  second machine; the forwarder is expected to present them the same way. The
  core's session-minting limit behind the same nginx collapses the same way,
  whatever `FORWARDED_ALLOW_IPS` says.

### soc-api (SOC backend-for-frontend)

- `POST /api/login` body `{key}` → constant-time compare with `DASHBOARD_KEY`
  (bytes, so a non-ASCII key is a 401, not a 500) → `204` and an httpOnly,
  `SameSite=Strict`, `Path=/api` cookie holding an HMAC-signed expiry
  (8 h, `SOC_SESSION_SECRET`). Login attempts are rate limited.
  The cookie is **host-scoped, not origin-scoped**: it has no `Domain`
  attribute, so it belongs to the host `localhost`, and browsers do not scope
  cookies by port. The analyst's browser therefore also sends it with every
  request to `http://localhost:<any port>/api/...`: on laptop A that is the
  store's checkout-api (:3000), the core itself (:8000) and any other local
  app serving `/api/`. None of the DeepCheck services
  reads it (only soc-api holds `SOC_SESSION_SECRET`), and httpOnly keeps it
  away from page scripts, but it does travel. `SameSite=Strict` does not
  narrow this: every localhost port is the same site. Origin scoping would
  need the SOC on a host name of its own (e.g. `soc.localhost`) or an API path
  no other app uses; neither is done.
- `POST /api/logout` → clears the cookie.
- `GET /api/me` → `204` with a valid cookie, else `401`.
- `GET /api/sessions`, `GET /api/score/{id}` → valid cookie required (401
  otherwise) → forwarded to the core with `X-Dashboard-Key`; the core's JSON is
  passed through unchanged; core errors map to `502`.
- `GET /api/health`.

## Failure modes

| Failure | Behaviour |
|---|---|
| Core down or slow | checkout-api answers 503, the SPA says "Ödeme şu anda işlenemiyor" and does NOT show success. soc-api answers 502, the SOC shows its existing error alert. |
| SDK never registered (blocked network) | No session token: the SPA cannot call checkout; it says the page must be reloaded. Never a payment. |
| Too little behaviour at "Öde" | Hidden retry on the server (3 × 2 s); then the OTP step, never a silent approval. |
| Wrong OTP | Stays on the OTP step with a field error; the core rate-limits guesses. |
| Bot | Same path as a person: decision `block` → `declined`. The store never says why; the SOC does. |
| SOC key leaked in a screenshot | The key is typed once into soc-api; the browser holds only a signed, httpOnly cookie. It is scoped to the host and `/api`, not to the SOC's origin: other localhost ports' `/api/` receive it too (soc-api above). |
| Session token expired at the OTP step (page open > 30 min) | checkout-api answers 409 and the page asks for a reload; no endless "try again later". |
| A rehearsal loop just before the hand payment | Under Docker Desktop both laptops share one rate-limit bucket (above): a 429 on laptop B. Leave a quiet minute before going live. |

## What is verified, and how

- Unit tests for both servers (core mocked with httpx MockTransport) and both
  SPAs (vitest), in CI.
- End to end on laptop A: compose up; a scripted browser checkout (plumbing
  only -- a script is not a person); `lab/live_bot.py` through
  `http://127.0.0.1:3000` → `declined`, the SOC shows `Bot Tespit Edildi` /
  `Engellendi`; the SOC login and live follow.
- The real-person payment can only be checked by a person: rehearse it.
- Not measured: the rate-limit key as seen from a second machine on the LAN
  (only from the host), and the cookie reaching other ports (that is how
  browsers scope host-only cookies, not something this project tested).

## Trade-offs

- **Duplicated UI primitives.** The SOC app was built by copying the dashboard
  and its components from the old `frontend/`, and the store's `/gizlilik`
  renderer is a copy of its `KvkkNotice.jsx`, instead of a shared package.
  Since `frontend/` was deleted (2026-10-02) the copies in `apps/` are the
  only ones, so there is no other copy for them to drift from; the SOC's copies
  of backend names and numbers (decision gate, reason and action labels,
  profile states, feature glosses) are pinned against the backend by
  `backend/test_profiles.py`. The two apps do
  not share components with each other; a shared package would add a build
  step to two images for no current benefit.
- **A BFF for the SOC.** One more service, in exchange for the key never
  reaching a browser and the SOC staying on loopback.
- **Demo OTP through the core's demo endpoint.** A real store would run its
  own 3-D Secure and report the outcome; here `/api/demo/verify` keeps the
  core's "one step-up = one approval" rule visible in the SOC.
