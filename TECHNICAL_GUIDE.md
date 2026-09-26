# DeepCheck — Technical Guide

A complete walk-through of what DeepCheck is, how every part works, why it
was built that way, and honest answers to the questions a technical jury is
likely to ask. Written for a mid-level AI / software engineer.

---

## 1. The problem and the idea

Online payment forms are attacked by automated scripts: card testing (trying
thousands of stolen card numbers), credential stuffing, and scripted
checkouts. Traditional defences look at *what* is submitted (card number,
IP, device fingerprint). DeepCheck looks at *how* the page is used.

Humans and bots interact with a page differently:

| Signal | Human | Bot / script |
|---|---|---|
| Mouse path | Curved, jittery, variable speed | Straight, constant speed, or absent |
| Pauses before actions | 200 – 1500 ms, irregular | Near zero, or perfectly regular |
| Typing rhythm | Uneven | Fixed interval |
| Clicks | Few, spaced out | Many, evenly spaced, pixel-perfect |
| Scroll | Bursts with varying speed | None, or constant |
| Tab focus | Occasionally switches away | Never |

DeepCheck turns those differences into twelve numbers, feeds them to a
Random Forest, and returns a **risk score from 0 to 100** every two
seconds while the user is on the page, with a Turkish label. Every score
carries a SHAP attribution of which behaviours drove it — stored on the row and
shown to the SOC analyst, and deliberately **not** returned to the party being
scored, because naming the three features that convicted a caller is a tuning
signal.

**Risk score = 100 × (the forest's vote share for the fraud class)**, which is
what the code computes. It is not a calibrated P(fraud | behaviour): nothing
has calibrated it against a base rate, because there is no labelled real
traffic to calibrate against.

There is a second, separate thing here, off by default: a **per-customer
behavioural profile** that compares a session against that customer's own past
sessions. It may only ask for extra verification, never block and never change
a score. §18 and §19.

---

## 2. System overview

```
 Browser (customer)                     Backend (FastAPI, Python 3.11)          SOC Dashboard (React)
 +----------------------+   POST every 2 s   +--------------------------+   GET every 3 s   +------------------+
 | payment page         | -----------------> | /api/analyze             | <---------------- | /api/sessions    |
 | + sdk/deepcheck.js   |  raw telemetry     |  validate -> features -> |                   | /api/score/{id}  |
 |   (mouse, click,     | <----------------- |  Random Forest --------->|                   |  table, D3 chart,|
 |    scroll, keydown,  |  score + label     |  SHAP -> smooth -> store |                   |  SHAP bars,      |
 |    focus timestamps) |  (SHAP: SOC only)  +------------+-------------+                   |  profile card    |
 +----------------------+                                 |                                 +------------------+
                                                          v
                                              PostgreSQL 16 (sessions, behavior_data,
                                                          ^    + 4 profile tables)
 Merchant backend  ---- POST /api/decision ---------------'
 (it knows who the customer is)   + X-Merchant-Id / X-Merchant-Key
                                  + customer_ref -> per-customer profile (off by default)
```

Everything runs from one `docker-compose up --build`: three containers,
database, backend, frontend. The backend trains the model on first start if
no model file exists.

Two paths, and they answer different questions. `/api/analyze` scores the
**behaviour** of whoever is on the page and is for display; `/api/decision` is
the only enforcement point, and it is called by the merchant's server, never by
the browser. The per-customer profile layer (§18, §19) hangs off that second
path alone: it needs a merchant credential and a customer reference, and it
ships off.

---

## 3. Repository map

| Path | Role |
|---|---|
| `sdk/deepcheck.js` | Browser SDK. Collects behaviour, posts it, emits results. About 800 lines, no dependencies. |
| `backend/main.py` | FastAPI app: request validation, four endpoints, smoothing, persistence. |
| `backend/scorer.py` | Feature extraction, model loading, inference, SHAP, labelling. |
| `backend/lstm_model.py` | The canonical `FEATURE_NAMES` list, and the LSTM definition (not served). |
| `backend/model_selection.py` | The study behind the model choice: seven families, temporal variants, held-out scenarios. |
| `backend/train_model.py` | Synthetic data generator (four personas, plus per-identity latents for the profile lab) and training. |
| `backend/profiles.py` | The per-customer profile statistic (§19). Pure: no database, no FastAPI, no model bundle. |
| `backend/profile_lab.py` | Measures the profile layer on synthetic identities and generates `docs/profile-evaluation.md`. Reads the served bundle, never writes it. |
| `backend/demo_seed.py` | Seeds the three **synthetic** demo customers for the jury prototype; `--status`, `--reset`, `--simulate`. |
| `backend/benchmark.py` | Form-fill generator and latency/score benchmarks. |
| `backend/record_session.py` / `backend/evaluate.py` | Record a labelled real session to `data/real/`; score those recordings and write `docs/evaluation.md`. |
| `backend/models.py` | SQLAlchemy tables: `sessions`, `behavior_data`, and the four profile tables (§10). |
| `backend/database.py` | Async engine, session factory, `init_db` and the additive migrations. |
| `backend/test_scorer.py` | Scoring scenarios, API authorization, enforcement, tokens and the sequential rule. |
| `backend/test_profiles.py` | The profile statistic, the endpoints, retention and the decision wiring. |
| `backend/test_demo.py` | The synthetic demo customers and the labelling that keeps them out of measurements. |
| `backend/entrypoint.sh` | Trains if needed, then starts uvicorn (2 workers by default). |
| `frontend/src/pages/Demo.jsx` | Turkish payment form with the SDK embedded and a live risk badge. |
| `frontend/src/pages/Dashboard.jsx` | SOC view: session table, D3 risk history, SHAP bars, the profile card. |
| `frontend/src/pages/KvkkNotice.jsx` | Serves `docs/kvkk-aydinlatma.md` at `/kvkk`, imported at build time so the two cannot drift. |
| `frontend/src/components/*` | RiskBadge, SessionTable, RiskChart (D3), VerificationModal, MetricCard, ProfilePanel, SyntheticBadge. |
| `lab/capture.py`, `lab/bot_lab.py` | Drive a real Chromium through the real SDK and record labelled telemetry. |
| `docker-compose.yml` | Postgres + backend + frontend. |
| `docs/index.html` | Landing page served by GitHub Pages. |

---

## 4. The browser SDK in detail

`sdk/deepcheck.js` is an IIFE that exposes `window.DeepCheck = { init, stop,
getSessionId, getToken, ready, flush }`. About 800 lines, no dependencies:
34 kB unminified, 12 kB gzipped.

### 4.1 What it listens to

| Event | Stored as | Why |
|---|---|---|
| `pointermove` (primary pointer), or `mousemove` where Pointer Events do not exist | `{x, y, t}` | Trajectory shape and acceleration. Listening only to `mousemove` left touch devices with no trajectory at all. |
| `click` | `{x, y, t}` | Click density and timing regularity |
| `scroll` | `{scrollY, t}` | Scroll speed variance |
| `keydown` | `{t}` only | Typing rhythm. **The key itself is never read.** |
| `visibilitychange` (hidden) | `t` | Focus loss count |

All listeners are `passive`, so they never delay the page.

Alongside these, each flush carries three **provenance counters**: how many
events arrived with `isTrusted` false, whether `navigator.webdriver` is set,
and the pointer-type mix (mouse / pen / touch). They are stored and shown in
the session detail but **do not reach the model or the score**. Two reasons.
First, they are self-reported by the very client being judged, so they are
evidence only once measured. Second, each catches a different and partial
thing: `isTrusted` is false for events synthesised by page JavaScript but
true for a browser driven by Playwright or Puppeteer, while
`navigator.webdriver` flags the driven browser and is trivially patched out.
Collecting them now means their value can be measured against the real
evaluation set instead of assumed.

### 4.2 Hesitation

Every tracked event calls `recordHesitation()`. If the gap since the previous
event is 400 ms or more it is stored as `{gap, t}`. At each flush, if the
user has been silent since the last event, that silence is also recorded and
the clock is advanced so it is not counted twice.

### 4.3 Rolling window and flush

A timer fires every 2000 ms (`intervalMs`). On each flush:

1. Every buffer is pruned to the last **10 000 ms** (`ROLLING_WINDOW_MS`).
   Buffers are *not* cleared, so a quiet two seconds does not reset the
   feature vector to "no data".
2. Each buffer is capped to the server's `max_length` (2000 mouse points,
   500 clicks, 1000 scrolls, 500 hesitations, 200 focus, 1000 keys), keeping
   the newest entries.
3. If nothing was collected at all, the flush is skipped.
4. `POST {apiUrl}/api/analyze` with the JSON payload and the session's
   `X-DeepCheck-Token` header. Without a valid token the API answers 401.
5. Non-2xx or a malformed body throws. `onError` fires and a
   `deepcheck:error` DOM event is dispatched. **A failed request never
   reaches `onUpdate`**, so a dead backend cannot look like a clean score.
   The one exception is 401, which the SDK answers by registering a **new**
   session, once, and resending. There is deliberately **no** back-off on 429
   or 503 today; the design for one is in §17.7.
6. On success, `onUpdate(result)` and a `deepcheck:update` DOM event.

Two more things each flush carries. `client_sent_at` is the sender's own clock
at send time: the server checks event **age** against it and the stability of
the per-session clock offset, rather than comparing an absolute clock, so a
user whose machine is ten minutes off is not blocked. And a flush with no
timestamped events at all is skipped outright rather than posted, because an
empty payload used to collide on the global replay hash and 422 an idle user.

`DeepCheck.flush()` returns a promise that resolves when the in-flight flush
finishes and **never rejects**; a host page calls it before asking for a
decision so the checkout is judged on the behaviour that just happened. Flushes
never overlap.

### 4.4 Session identity

`init()` calls `POST /api/session`, and the server returns the id together
with a signed proof-of-work challenge; `POST /api/session/attest` then
exchanges the solved challenge for the token. The token is
`<issued_s>.<HMAC-SHA256>` over the session id and its issue second, in a
domain separate from the challenge's, and it is valid for 30 minutes
(`SESSION_TOKEN_TTL_S`); an expired token gets 401, and the SDK then registers
a new session once. The id is **not** generated in the browser: a client that could name its own id could post telemetry under
another customer's session, and a bot could present an id at checkout that
it had never sent behavior for. Listeners attach before the request goes
out, so the first two seconds of behavior are buffered rather than lost;
flushes are dropped until the token arrives, and the rolling buffers mean
the next flush re-sends that window.

`DeepCheck.getSessionId()`, `DeepCheck.getToken()` and `DeepCheck.ready()`
expose what a host page needs to call `/api/decision` at checkout.

---

## 5. The API

All thirteen endpoints are in `backend/main.py`. The four per-customer
profile routes are described in §19; they do nothing at all unless the profile
layer is switched on, which it is not by default.

### `POST /api/session`

Opens a session and returns `{session_id, challenge, difficulty_bits}` — a
signed proof-of-work challenge and **no token**. Takes no input, deliberately:
the id is never accepted from the caller. The challenge carries the session it
belongs to and the moment it was minted, signed, so a solution cannot be moved
to another session or replayed once it expires; the server stores nothing.

### `POST /api/session/attest`

Exchanges a solved challenge for the token. Requires a nonce whose SHA-256 has
`POW_DIFFICULTY_BITS` leading zero bits, plus two runtime measurements: the
clamp on `performance.now()` and the median delay of `setTimeout(..., 0)`.
Both are properties of the engine and the machine rather than of the page.

What the token shows is narrower than it sounds: some client did the proof
of work for this session inside the challenge window, and the two runtime
values it reported are inside browser-plausible bounds. It does not show that
the client is a browser, that it ran the SDK, or that a person is present --
both values are self-reported. A plain Python client (hashlib, hard-coded
values) obtained a valid token 50 times out of 50 (`backend/main.py`, the
runtime-attestation comment). There is no separate flag and no stored state.

Measured rather than asserted: a 12-bit proof costs Chromium about 75 ms over
7,600 hashes and a Python script about 2 ms over 1,500. The asymmetry runs the
wrong way, because JavaScript SHA-256 is roughly thirty times slower than
native, so raising the difficulty taxes customers harder than attackers. This
is evidence that code executed, **not** a cost barrier, and the runtime values
are trivially forged once the accepted ranges are known — they are in the
source. A script that has read the SDK passes it, and so does a bot driving
a real browser. It bounds how fast sessions can be minted; it is not what
stands between an attacker and the API.

Chromium reports its clock clamp as exactly 100.0 µs, the documented value. It writes no database row either, so a page that is opened and
never used leaves nothing behind; the row is created by the first flush.

### `POST /api/analyze`

Requires `X-DeepCheck-Token` matching the `session_id` in the body, compared
with `hmac.compare_digest`. There is no "mint an id if missing" fallback any
more.

Input is validated by Pydantic models before any maths runs:

- Timestamps: integers in `[0, year 2100 in ms]`.
- Coordinates: floats in `[-1e5, 1e5]`, `allow_inf_nan=False`.
- Hesitations: `[0, 1 h]`. Scroll Y: `[-1e7, 1e7]`.
- List lengths capped as above. Unknown keys ignored (older SDK builds).

This rejects the JSON `NaN` / `Infinity` literals Python would otherwise
accept, physically impossible values, and oversized payloads that could
cost hundreds of milliseconds of CPU.

Processing order:

1. `scorer.compute_risk(raw)` runs in a **threadpool** so the ~50 ms of
   CPU-bound sklearn / SHAP / torch work does not block the event loop.
2. Atomic get-or-create of the `sessions` row via `INSERT ... ON CONFLICT DO
   NOTHING`. Two concurrent first flushes cannot collide.
3. **Median smoothing**: the session's official score is the median of the
   last 5 per-flush scores. One odd reading cannot flip the verdict; a
   change has to persist for three flushes.
4. `sessions` row updated (smoothed score, label, confidence, SHAP, timing);
   a `behavior_data` row inserted with the raw telemetry, the twelve features
   and the *raw* per-flush score.
5. Response:

```json
{
  "session_id": "uuid",
  "risk_score": 73.4,
  "label": "Yüksek Risk",
  "confidence": 0.91,
  "shap_explanation": [
    {"feature": "etkilesim_entropisi", "value": 0.12, "impact": 28.3},
    {"feature": "tereddut_skoru",      "value": 0.00, "impact": 24.1},
    {"feature": "ivme_degisimi",       "value": 0.98, "impact": 19.7}
  ],
  "response_time_ms": 47
}
```

`shap_explanation` comes back **empty** unless `SHAP_IN_ANALYZE=1`. This
endpoint answers the party being assessed, and naming the three features that
drove their score is a tuning signal: submit, read the reason, adjust, repeat.
The adversarial run used exactly that loop. The explanation is still stored on
every row, and the SOC dashboard reads it from `GET /api/score/{id}` behind the
dashboard key.

Errors: 503 if the model is not loaded, 500 with a generic Turkish message
on any other failure (tracebacks are logged, never returned), 422 for the
replay checks, 429 past the rate limit.

#### Replay protection

Before scoring, three checks reject telemetry that is not evidence about
the person at the keyboard right now. Each returns 422 with a Turkish
message and is logged.

1. **Clock skew.** The newest event in the flush must be within 15 s of the
   server clock. A recording is old by definition.
2. **Forward time.** Within a session, each flush's newest event must not be
   older than the previous flush's newest event.
3. **Fingerprint.** A SHA-256 of the telemetry with every timestamp rebased
   to the flush's first event, so shifting a recording's clock to "now" does
   not change it. The lookup is global across sessions: a fresh token does
   not launder a recording. Stored as `behavior_data.payload_hash`.

A recording that is perturbed as well as re-timed gets past the hash. That
is where replay stops being a transport problem and becomes a model
problem, which the real-session evaluation addresses.

### `POST /api/decision`

The enforcement point, and the only place the 40 / 60 / 80 ladder is
applied. Takes `{session_id}` plus the token — optionally `customer_ref` and
`risk_context` with a merchant credential, see §18 — and returns
`{action, risk_score, label, message, reason}` where action is `allow`,
`warn`, `verify` or `block`.

It fails closed, and it needs evidence. In order:

| Condition | Result | `reason` |
|---|---|---|
| No session row | `verify` | `unknown_session` |
| Evidence has not accumulated (a sequential stopping rule, with a floor of 3 analysed flushes) | `verify` | `insufficient_evidence` |
| Fewer than 3 of the newest ten flushes **observed a generator** (measured at least one structural feature) — neither approved nor blocked | `verify` | `insufficient_evidence` (internally `unobserved`) |
| Last flush older than 30 s | `verify` | `stale` |
| Many sessions sharing one behaviour bucket (off by default) | `verify` | `cluster` |
| The accumulated evidence crossed the bot bound while the smoothed score is below 60 | `verify` | `sequential` |
| An 80+ score that is unremarkable among held-out human sessions | `verify` | `conformal` |
| Enforcing, and this session deviates from **this customer's own** history | `verify` | `profile_deviation` |
| The ladder says block, but fewer than 3 of the five flushes behind the smoothed score were observed | `verify` | `insufficient_evidence` (internally `unobserved`) |
| Any `verify` outcome, and a step-up recorded within 5 min that has not been spent — the approval spends it | `allow` | `verified` |
| Otherwise, the ladder | as scored | `score` |

One plausible two-second window is cheap to fabricate; six seconds of
sustained behaviour is not.

**Only observed flushes count as evidence (2026-09-26).** A flush that measured
none of the six structural features is not evidence in either direction: an
empty window scores 99.1 on neutral fallbacks alone (enough, in one flush, to
force a step-up on a customer who had done nothing), and a thin window can be
*steered low* through the marginal features and the client-supplied
`hesitation_intervals` (3000 crafted unobserved payloads: median 29.7). So the
sequential statistic reads only observed flushes, and a decision resting on
fewer than three of them is `verify` — never an approval, and never an
irreversible block. The first version of this rule instead skipped the
sequential test and let the ladder decide; an adversarial review measured a
naive SDK-faithful script being charged 88 times in 300 under it, and it was
replaced. Every number, including the one case it does not close (automation
hidden among three or more *human-looking* observed flushes — not a new
capability, since the same attacker passes the old rule by staying silent), is
in `docs/evaluation.md` and above `main._structural_bits`.

**One step-up, one approval.** A verification is spent by the approval it
produces (`main._consume_step_up`, compare-and-set on the timestamp, leaving
`last_seen_at` alone). It used to upgrade every `verify` on the session for
five minutes. A block still rests on observed behaviour or it is held at
step-up, and **a keyboard-only script with randomised key timing is charged
119 of 120 times** under this rule and the one before it — a hole in the
model, stated in `docs/evaluation.md`, not claimed closed. The freshness rule stops a token lifted from a
shared machine being cashed in later on the real customer's earlier
browsing. A recorded verification only ever upgrades `verify`; a `block`
cannot be verified past.

**What the caller is told is not what is recorded.** `cluster`, `sequential`,
`conformal`, `profile_deviation` and `profile_rate_limited` all reach the
client as one collapsed `reason: "step_up"`, because naming the check that
convicted a caller is a tuning signal (§19.5). The internal reason is kept in
`decision_audit` and shown in the SOC panel. The reasons that describe the
*state of the telemetry* rather than the verdict — `unknown_session`,
`insufficient_evidence`, `stale`, `verified`, `score` — are returned as they
are, because the host page needs them to say "keep going, a few more seconds"
instead of showing an OTP box.

### `POST /api/demo/charge`

The merchant side of the pattern, in miniature. Takes `{session_id,
amount}` plus the token, runs the decision logic above, and returns
`{status: "charged", charge_id, ...}` only for `allow` or `warn`; otherwise
`{status: "declined", decision}`. In a real integration the merchant's own
backend calls `/api/decision` and then its payment provider. Here both live
in one endpoint so the property a jury will test for holds visibly: the
demo page contains no condition that could be edited to produce a charge.

### `POST /api/demo/verify`

Takes `{session_id, code}` plus the token. Checks the code against
`DEMO_VERIFY_CODE` in constant time and, on success, writes
`sessions.verified_at`. It stands in for an SMS or 3-D Secure provider; the
point is where the result lives. The browser can submit a code. Whether
that unlocks anything is decided on the server and read back by the charge
endpoint. A session with no row (a client that never sent a flush) cannot
be verified. The demo code is printed in the modal on purpose, so it reads
as a deliberate demo value rather than an "any six digits" bypass.

### The per-customer profile routes

All five need a merchant credential (`X-Merchant-Id` + `X-Merchant-Key`), and
all five answer 503 when the profile layer is off — which is the default. §19
is what they do; this is the shape.

| route | body | answers |
|---|---|---|
| `POST /api/profile/consent` | `{customer_ref, consent_basis}` | 201, or 409 if the customer has objected. **The only code path that creates a profile.** |
| `POST /api/profile/erase` | `{customer_ref, mode: erase\|object}` | always 204, identical whether or not a profile existed, so it is not an existence oracle |
| `POST /api/outcome` | `{session_id, customer_ref, outcome: settled\|disputed}` | always 204; `disputed` deletes that session's vector, `settled` promotes it |
| `GET /api/profile/review/{session_id}` | — | the stored vectors and what the layer said, behind a **per-operator** credential (`PROFILE_REVIEW_KEYS`), writing an access-audit row |

A raw `customer_ref` appears only inside a POST body — never in a path, never in
a query string, never in a log, and never echoed back by the 422 handler.

### `GET /api/score/{session_id}`

Session summary plus the last 200 `behavior_data` rows in chronological
order, each with timestamp, raw score and the twelve features. Feeds the
dashboard chart. Requires `X-Dashboard-Key`. When the profile layer had an
opinion about the session's last decision it also carries a bounded, uniformly
shaped profile block — state, modality, counts, p-values and top features, and
**never** `profile_id` or a raw vector.

### `GET /api/sessions`

Newest 200 sessions by `last_seen_at`. Feeds the dashboard table. Requires
`X-Dashboard-Key`: this endpoint lists every customer's live session id and
score, and used to be open to anyone who could reach the port.

### `GET /api/health`

`{"status": "sağlıklı" | "model yüklenmedi", "model_loaded": bool, "timestamp"}`.

CORS: allow-list from `CORS_ORIGINS`, default `*` for local use.
Credentials are only enabled when a real allow-list is set, since the CORS
spec forbids `*` with credentials.

---

## 6. Feature extraction

`scorer.extract_features(raw)` turns the payload into twelve floats, each
normalised to roughly 0 – 1. Canonical names and order live in
`lstm_model.FEATURE_NAMES`, shared by scorer, trainer and SHAP labels so
they can never drift apart.

| # | Feature | Computation | Human tends to | Bot tends to |
|---|---|---|---|---|
| 1 | `scroll_hizi_varyansi` | variance of scroll speed, log-percentile scaled | high | 0 or tiny |
| 2 | `tereddut_skoru` | mean hesitation gap in ms, log-percentile scaled | 0.3 – 0.8 | about 0 |
| 3 | `etkilesim_entropisi` | Shannon entropy of inter-event gaps, **per channel**, then weighted average | 0.7 – 1.0 | about 0 |
| 4 | `ivme_degisimi` | variance of mouse *acceleration*, log-percentile scaled | mid-range | near 0 |
| 5 | `tiklama_yogunlugu` | clicks in the last 5 s / 10 | low | high |
| 6 | `odak_degisimi` | focus-loss count / 5 | sometimes > 0 | 0 |
| 7 | `hiz_otokorelasyonu` | lag-1 autocorrelation of pointer speed, mapped from [-1, 1] onto [0, 1] | high — motion carries momentum | ~0.5 for IID jitter, ~1 for a linear script |
| 8 | `yon_tutarliligi` | mean cosine between consecutive move vectors | high — motion is target-directed | ~0.5 for re-rolled jitter, ~1 for a straight line |
| 9 | `zaman_kuantasyonu` | share of inter-event gaps repeating the **same millisecond**, per channel | ~0 | high — scripted timers repeat |
| 10 | `duraklama_dagilimi` | coefficient of variation of inter-event gaps, per channel | high — human gaps are heavy-tailed | ~0 for a fixed delay, capped well below human for `uniform(a,b)` |
| 11 | `tiklama_oncesi_hareket` | share of clicks preceded by pointer motion | high | low — a synthetic click teleports |
| 12 | `kanal_gecis_gecikmesi` | median delay when input switches between pointer and keyboard | real, a hand has to move | ~0 |

Features 1 – 6 are **marginal statistics** and an attacker reproduces them by
emitting independent per-step noise: measured, a straight-line bot with two
pixels of jitter halved its risk score and was approved. Features 7 – 12 measure
**structure** that independent noise does not have, which is a materially
higher bar to forge — but it is a designed mitigation, not a measured one
(§15.2), and four of the twelve saturate on real browser telemetry (§20).
Features 9 – 12 are count and ratio statistics by design, so they stay valid on
thin flushes where a variance estimate would be noise.

**Normalisation is learned, not guessed.** The three heavy-tailed features --
`scroll_hizi_varyansi`, `tereddut_skoru` and `ivme_degisimi`, two variances and
a mean duration -- are mapped onto 0..1 by taking
`log10` of the raw value and placing it between the 1st and 99th percentile of
the training distribution. Those endpoints are measured during training and
stored in the bundle as `feature_scaling`; a bundle without them is refused
rather than served, because the model would be reading a different coordinate
system than it learned.

This replaced three hand-picked divisors, and the reason is worth keeping.
Divided by 2.2e-6, `ivme_degisimi` read 1.000 for human motion, 1.000 for a
Bezier-path bot and 0.002 for a straight line: the feature had collapsed into
"does the pointer wobble at all?". Measured against the training distribution
afterwards, the real 99th percentile is 2.9e-6 -- the divisor had been sitting
at the very top of the range, so almost everything clipped. In an adversarial
test, adding two pixels of gaussian noise to an otherwise metronomic bot
halved its risk score and turned a refused session into an approved one. After
the change the same ablation moves the score from 90.4 to 88.6 and the verdict
does not change at all.

Design decisions worth knowing:

- **Entropy is binned on a fixed log-millisecond grid.** Binning over each
  session's own `[min, max]` made the feature mean opposite things in
  different sessions: one long pause, which is what a person reading produces,
  stretches the range until every ordinary gap lands in the first bin and the
  entropy collapses toward zero, while a metronomic bot keeps a narrow range
  and scores higher. Measured on realistic input it was inverted -- 0.174 for
  a human model against 0.548 for a headless script. Fixed edges make the
  number comparable between sessions.
- **Entropy is measured per channel, not on a merged stream.** Merging
  three perfectly regular channels with different periods produces a
  jagged combined gap sequence that scores as high entropy (a
  beat-frequency artefact; measured about 0.92 for three zero-entropy
  channels). Scoring each channel and averaging by gap count avoids that.
- **Acceleration, not speed delta.** Constant-velocity motion has zero
  acceleration variance whatever the speed; human motion always has some.
  The divisor was calibrated on real mouse traces so a natural trajectory
  lands near the human training mean.
- **Neutral fallbacks.** If a feature cannot be computed (fewer than two or
  three samples), it is set to the midpoint between the human and bot
  training means, not 0.0. Zero sits at the *bot* end of every feature, so
  "no data" used to be scored as "more suspicious than a bot". Click
  density and focus count keep 0 because zero is a real measurement there.
- Any non-finite value is replaced by its neutral default before the
  model, and a non-finite final probability becomes 0.5. NaN can never be
  written to the database.

---

## 7. The models

One model is served. `scorer.ModelBundle` loads `model.pkl` once per worker.

| Model | Library | Config | Role | Weight |
|---|---|---|---|---|
| Random Forest | scikit-learn | 200 trees, depth 12, min leaf 5 | Supervised classifier — the score | 1.0 |
| LSTM | PyTorch | 2 layers, hidden 32, dropout 0.2, Adam 1e-3, 8 epochs | Trained only with `TRAIN_LSTM=1`, **not served** — see “Why the LSTM was dropped” | 0.0 |
| Isolation Forest | scikit-learn | 200 trees, contamination 0.05 | Trained and stored, **weight 0** — see “Why the Isolation Forest was dropped” below | 0.0 |

Inference:

```
scaled     = StandardScaler(features)
P(fraud)   = RF.predict_proba(scaled)[fraud]
score      = round(100 * P(fraud), 1)
session    = smooth_session_score(previous per-flush scores, score)
```

**Why the LSTM was dropped.** `backend/model_selection.py` compares seven
model families and four temporal variants on the same splits. The LSTM had
only ever seen simulated sessions, and on the 234 browser-lab flushes its
output collapsed toward "human". Its ROC-AUC was 0.947, but its Brier score
was 0.51. Blended at 0.4, it lowered the share of bots reaching 60 from 0.90
to 0.79 without flagging a single extra human. On simulated mid-session
handovers it reached p ≥ 0.5 only at the tenth flush, where the forest,
reading the current flush alone, did so on the first automated one. Its one
job was being done by the forest plus a disagreement rule. That rule is now
`smooth_session_score`, which lets a jump of 35 points past the recent median
through the smoothing: 185/185 handovers caught on the first automated flush.

The cost is real and stated. The LSTM damped every score, including
legitimate ones. Simulating whole form fills through the decision layer:

- typical users approved at once went from 95% to 89%, with the rest sent to
  step-up;
- slow typists blocked outright went from 5.2% to 0.2%, because the
  disagreement rule had been escalating on them.

Those before/after figures were measured at the time of the change against
commit `705a63f`, using `benchmark.py`'s form-fill generator split into
rolling SDK windows, 500 sessions per style.

**Why gradient boosting was not adopted.** LightGBM, XGBoost and
HistGradientBoosting matched the forest's ROC-AUC. They also caught more bots
at 80 on scenarios they had trained on: +0.27, 95% run-level bootstrap CI
[+0.13, +0.40]. With a scenario held out of training, they extrapolated
confidently.

**One sentence here is retracted.** This guide used to say "LightGBM blocked
74% of the unseen H1 humans at 80, and the forest blocked none." That was
measured on the frozen lab rows and **does not reproduce**: the same protocol on
the re-captured data gives LightGBM **0.04** and the forest **0.00**. The
conclusion survived the retrial — on the worst unseen human scenario the forest
is challenged at 0.05 against ExtraTrees' 0.63, LogReg's 0.42 and LightGBM's
0.30 — but the evidence quoted for it was wrong, and a conclusion is only worth
the measurement under it. Real customers are unseen by construction, so without
real data the conservative model is the right one.

**Why the Isolation Forest was dropped — and the first reason retracted.**
It held 0.2 of the blend until it was measured against held-out real browser
rows, where its standalone ROC-AUC came out at **0.340**, and this guide called
it *inverted*: systematically ranking the attacker as the more normal party.

**That number does not survive.** It was measured on the 234 lab rows that were
frozen to a superseded scale (§20.6). Re-measured on the re-captured lab plus
the one recorded person — 301 real rows, and with an honest empirical-CDF
calibration rather than a raw decision function — the same model scores
**0.657**. It is not inverted. It is merely useless in the direction that
matters, and that is the reason it stays out:

- it puts **38% of legitimate flushes**, **76% of the recorded person's
  flushes** and **87% of unseen `H1_human` flushes** at or above the step-up
  line;
- it gives a stored-card checkout a median session score of **54.0** where the
  forest gives **0.8**;
- **every blend that gives it weight loses** — the former 0.5 RF / 0.2 LSTM /
  0.3 IsoF mix reaches `tpr@0.8` of 0.12 against the forest alone at 0.81.

The structural reason stands, and it is why no amount of retuning fixes this:
it is fitted on human rows only, so “normal” to it *means* the human
distribution, and the whole threat this product addresses is automation built
to sit inside that distribution. The only thing a one-class detector can be
confident about here is that an **unusual human** is unusual. It was answering
the wrong question well.

Replaying identical telemetry through both weightings (`scratchpad`
A/B, 25 sessions per persona, everything downstream of the blend held
fixed). This table was measured **before** the correction above, in the old
coordinate system — it is kept because the decision was taken on it, not
because it is current:

| persona | mean score, with IsoF | without | AUC with | AUC without |
|---|---|---|---|---|
| human | 19.0 | **9.3** | — | — |
| bot_naive | 32.2 | 27.7 | 0.44 | 0.51 |
| bot_linear | 86.0 | **92.4** | 1.00 | 1.00 |
| bot_mimic | 25.0 | 16.7 | 0.83 | 0.84 |
| bot_adaptive | 13.2 | 3.0 | 0.00 | 0.02 |

Read the first column against the second: it was adding much the same
offset to humans and to bots, which inflates every score without
separating anything — and an inflated human score is the expensive kind
of error at a payment gate. Separation improves slightly or holds
everywhere; the gap between a human and obvious automation widens from
67 points to 83. It does **not** fix `bot_adaptive`, and nothing about
this change claims it does.

It also cost latency. End-to-end `compute_risk` measured 31.7 ms with the
call and 17.7 ms without it — the Isolation Forest was 44 % of the scoring
budget for a term that hurt accuracy.

The model is still trained and still saved in the bundle. That is
deliberate: the decision above rests on 82 held-out real rows, and it
should be re-measured once there are more real human recordings. Nothing
reads it per request, because computing a number only to multiply it by
zero is latency spent on nothing.

**Explanation.** `shap.TreeExplainer(rf)` gives a per-feature contribution
for the fraud class. The three largest absolute contributions are returned
as `impact` (x100). Only the Random Forest is explained; that is the model
the jury can inspect, and SHAP on trees is exact and fast.

Performance settings baked into loading: `n_jobs=1` on both forests
(per-call thread-pool setup cost about 3x the actual work for a single row)
and `torch.set_num_threads(1)`. Measured `compute_risk` 17.7 ms once the
Isolation Forest left the score (31.7 ms with it, `backend/scorer.py`); an
older "about 42 ms" was the three-model ensemble, which no longer exists. The
`response_time_ms` field reports it on every call.

### 7.0 Update, 2026-09-25: the training distribution was corrected

Everything above this line was measured against a simulator that got one thing
wrong, and the error only became visible when the first real person was scored.
A browser dispatches pointer and scroll events on **renderer frame boundaries**,
so a real user repeats a millisecond gap by construction — in the one recording
there is (`data/real/human/`, 52 flushes, person p01), 52.1% of deduplicated
pointer gaps are exactly 17 ms and 30.0% are 16 ms. The simulator drew
timestamps from continuous distributions and essentially never repeated one
(modal-gap share 0.087 against the recorded person's 0.500), so the forest had
learned "repeated millisecond gap = script". `zaman_kuantasyonu` was the top
SHAP feature in **52 of 52** of that person's flushes, and the session scored
**Yüksek Risk**.

`backend/train_model.py` changed — the **training distribution only**, no
feature definition and no threshold:

1. a frame clock on pointer and scroll timestamps (60/120/144 Hz weighted
   0.70/0.20/0.10 — one machine was recorded, so the mix is an **assumption**;
   3% dropped frames, modelled as coalesced). Clicks and keydowns are *not*
   frame-stamped: in the recording they land on a frame multiple at chance;
2. pointer motion as a **burst on a minimum-jerk path** (median 10.5 samples,
   417 ms rest, fitted to the recording) rather than an i.i.d. random walk;
3. a **`human_autofill` persona** (17.6% of the human class) — before it, every
   human training window carried at least 15 keystrokes, so "no typing" could
   only come from a bot;
4. `BOT_REAL_CLOCK_RATE = 0.50` — half of `bot_sophisticated` gets the same
   frame clock, so the fix cannot become a new one-bit pass.

Measured against the retrained bundle (full table and method in
`docs/evaluation.md`):

| measured on | before | after |
|---|---|---|
| p01 — smoothed session score, the value `/api/decision` reads | 62.7 (verify) | **1.2 (allow)** |
| p01 — share of 52 flushes ≥ 60 | 90.4% | **5.8%** |
| stored-card autofill, at the confirm flush | 67.7 | **0.1** |
| hand-typed control, at the confirm flush | 66.4 | **0.3** |
| `bot` / `bot_sophisticated` detected, n=200 each | 100% / 100% | **100% / 100%** |
| `bot_sophisticated` forced onto the frame clock, n=300 | not measurable | **100% ≥ 80** |
| scoring latency p95, n=1200 | 67.6 ms | **19.2 ms** |

Autofill was never a separate defect, which corrects an earlier premise of this
guide: the same generator's hand-typed control scored 66.4 against autofill's
67.7. A real browser's pointer clock put an **ordinary checkout** near 70,
stored card or not.

The retrain also exposed a second fault rather than creating it: three of p01's
52 flushes carry no usable activity, the forest scores that neutral-fallback
coordinate 88–97, and against a session sitting at 0.0 that is a jump large
enough to take the `LEVEL_SHIFT_POINTS = 35` bypass — turning an allow into a
block. The bypass now additionally requires the current flush to have measured
at least one **structural** feature (§4); it is deliberately not gated on
`provisional`, because `headless_bot` is provisional too and gating on that
would reopen the mid-session handover hole the bypass exists to close.

**What this costs the comparisons above.** "Why the LSTM was dropped", "Why
gradient boosting was not adopted" and "Why the Isolation Forest was dropped"
were all measured on the **pre-correction** distribution. The conclusions have
not been withdrawn — the forest is still what is served — but they are being
re-run against the corrected one, and until that is written down here, they are
evidence from a distribution that is known to have been wrong about real
browsers in one specific way.

### 7.1 Labels and actions

| Score | Label | Colour | Action in the demo |
|---|---|---|---|
| 0 – 40 | Gerçek Kullanıcı | green | none |
| 40 – 60 | Şüpheli | yellow | warning shown |
| 60 – 80 | Yüksek Risk | orange | verification modal (step-up) |
| 80 – 100 | Bot Tespit Edildi | red | submit blocked |
| unknown | (none) | grey | treated as *verify*, never as *allow* |

`get_label` maps a non-finite score to 50 rather than letting it fall
through to the harshest label.

---

## 8. Training pipeline

`python train_model.py` (run automatically by the container if
the artifacts are absent or unusable; seed 42, reproducible).

1. **Simulate 25 000 sessions**, half human, half bot. Each session is ten
   consecutive flush windows — the same ~20 seconds the LSTM reads back out
   of Postgres at serving time — so the dataset is 250 000 extracted feature
   windows. The tabular models train on each session's final window; the
   LSTM trains on the whole ten-step sequence. Four personas:
   - `human` — curved mouse with jitter (gap 50 – 150 ms), 400 – 1200 ms
     pauses, 10 – 35 uneven keystrokes, scroll bursts, occasional focus loss.
   - `human_rushed` (10 % of humans) — a real person in a hurry: fewer
     pauses, faster typing, 30 % of the time typing only with almost no
     mouse.
   - `bot` — headless or scripted: 0 – 3 mouse points or a straight line at
     80 ms +/- 10 ms, clicks every 150 ms +/- 8, keys every 3 ms, no focus
     loss.
   - `bot_sophisticated` (10 % of bots) — mimics human ranges but too
     smooth: near-constant velocity, evenly spaced clicks at 500 ms +/- 15,
     typing at 150 ms +/- 8, hesitations inserted at regular intervals.

   A further 12 % of each class **changes persona mid-session**: human then
   robotic (labelled bot, a session handed to automation) and robotic then
   human. Those sessions are the only thing in the dataset a sequence model
   can learn that a single feature row cannot express.

   Session-level traits — keyboard-only, sparse mouse, headless — are drawn
   once and held for all ten windows. A real user does not stop being
   keyboard-only halfway through.

   The neutral fallback values feature extraction uses for a too-sparse
   flush are computed here, not hand-written in `scorer.py`, and stored in
   `model.pkl`. They have to be a *fixed point*: a pilot pass derives them,
   feeds them back into extraction, and the real dataset is then generated
   with the same values that ship. Skipping that step puts sparse sessions
   at inference into feature space the forest never saw — measured cost, a
   headless bot scoring p(fraud) = 0.000.
2. **Run every simulated payload through the same `extract_features()`
   used in production.** This is the single most important design choice
   in the pipeline: training and serving share one code path, so a
   feature bug cannot exist in one and not the other.
3. 80 / 20 stratified `train_test_split`, `StandardScaler` fit on train.
4. Train RF and IsoForest on the aggregate rows. The LSTM is trained on the
   ten-step sequences only with `TRAIN_LSTM=1`, for re-measurement.
5. Save `model-sklearn<version>.pkl` (scaler, rf, iso_forest,
   feature_names, neutral_defaults and the scikit-learn version), plus
   `lstm_model-sklearn<version>.pt` when the LSTM was trained. Both are
   git-ignored and regenerated on demand.

**Reproducibility.** Seed 42 covers the whole pipeline: `np.random.default_rng`
for the data, `random_state=42` for both forests, and `torch.manual_seed` for
the LSTM. That last one was missing until recently, and its absence mattered:
weight initialisation, dropout masks and batch shuffling all came from torch's
global RNG, so every training run produced a different sequence model while it
carried 30% of the ensemble weight. Retraining alone could move a session by
more than ten risk points and turn a detected bot into a merely "suspicious"
one. A test now asserts the seed is set.

**Why the filenames carry a version.** A pickle is only safely loadable by the
scikit-learn that wrote it; another version loads it with a warning about
"possibly invalid results" and scores anyway. `backend/` is bind-mounted into
the container, so a host-trained model is exactly what the container loads with
its own pinned version. Version-scoped names let both keep a correct model,
`ModelBundle` refuses a mismatched pickle outright, and `entrypoint.sh`
retrains when the check fails.

The 10 % contamination personas exist so the classes are *not* trivially
separable. A synthetic dataset where accuracy is 100 % is a modelling
smell, not a result.

---

## 9. Life of one session, end to end

1. Customer opens `/demo`. `index.html` loads `deepcheck.js`; `Demo.jsx`
   calls `DeepCheck.init({apiUrl, onUpdate, onError})`, which asks the
   server for a session id and its signed token.
2. The customer moves the mouse and starts typing a card number. Only
   coordinates, timestamps and keydown *times* are buffered.
3. At t = 2 s the first flush posts about 30 mouse points, 8 key timestamps
   and 2 hesitation gaps.
4. FastAPI validates the payload, extracts twelve features (scroll and click
   density fall back to neutral / zero because none happened yet), scores
   about 18, SHAP is stored but **not returned** to the page, and two rows
   are written.
5. The badge on the page turns green: "Gerçek Kullanıcı 18". It is
   display only, and nothing on the page compares it with a threshold.
6. Every 2 s the window slides forward; the median of the last five raw
   scores is the badge value.
7. Meanwhile the dashboard polls `/api/sessions` every 3 s and lists the
   session in green. Clicking it polls `/api/score/{id}` and draws the raw
   per-flush history with D3, the top-3 SHAP bars, and the profile card if
   the layer had an opinion.
8. The customer presses **Onayla**. The page first awaits `DeepCheck.flush()`
   so the decision is made on the behaviour that just happened, then calls
   `POST /api/demo/charge`, which runs the decision server-side and either
   charges or declines. The page renders whatever came back; it has no local
   payment path.
9. If a Playwright script drives the same page instead, mouse points arrive at
   a fixed 80 ms cadence in a straight line, hesitation is empty, entropy near
   0, acceleration variance near 0. The score climbs past 80 within three
   flushes and the badge turns red — but what actually stops the payment is
   the server's `block`, which no edit to the page can undo.

---

## 10. Data model

```
sessions                              behavior_data
-------------------------             --------------------------------------
id            TEXT PK (uuid)          id             SERIAL PK
created_at    TIMESTAMPTZ             session_id     TEXT FK -> sessions.id
last_seen_at  TIMESTAMPTZ (idx)       created_at     TIMESTAMPTZ
risk_score    FLOAT  0..100 (check)   mouse_trajectory, click_timing,
label         TEXT                    scroll_rhythm, hesitation_intervals,
confidence    FLOAT                   focus_changes, key_events   JSON
shap_explanation JSON                 twelve feature columns      FLOAT
response_time_ms FLOAT                risk_score     FLOAT 0..100 (check, raw per-flush)
                                      idx (session_id, created_at)
```

`sessions.risk_score` is the *smoothed* verdict; `behavior_data.risk_score`
is the raw per-flush signal kept for analysis and charting.
`behavior_data.measured_mask` is a bitmask saying which of the twelve features
this flush genuinely measured, as opposed to falling back to a neutral default
— without it nothing downstream can tell a measured 0.3 from a default 0.3.

Four more tables exist for the per-customer profile (§19). They hold **no raw
telemetry at all**:

```
customer_profiles                      customer_profile_vectors
----------------------------------     ----------------------------------------
id            VARCHAR(64) PK           id             BIGSERIAL PK
              (HMAC pseudonym)         profile_id     VARCHAR(64)   no FK
merchant_id   VARCHAR(32)              session_id     VARCHAR(64)
key_version   SMALLINT                 modality       touch|mouse|keyboard
consent_basis VARCHAR / 'objected'     feature_schema_version SMALLINT
consent_at, last_seen_at, ...          vec, disp      JSONB  (12 numbers each)
challenge counters, heal counters      flush_count    SMALLINT
is_demo, is_synthetic BOOL             probation      BOOL
                                       outcome        pending|settled|disputed
decision_audit                         is_synthetic   BOOL
----------------------------------     uniq (profile_id, session_id)
one row per decision, 90 days          idx (profile_id, modality, created_at)
action, reason, public_reason,
profile_state, deviation,              profile_access_audit
p_value, p_value_low, top_features,    ----------------------------------------
reference_n, probation_n,              who read which profile and when, 365 days
candidate_vec (only when it
deviated), shadow, amount_band,
new_beneficiary
```

Three schema decisions worth knowing:

- **No foreign key** on `sessions.profile_id`, `customer_profile_vectors.profile_id`
  or `decision_audit.profile_id`. Sessions live 24 h and profiles 180 days; a FK
  would let a profile delete abort the retention sweep's single transaction and
  silently stop raw-telemetry blanking for the whole database.
- **The vectors are the statistic.** There is no mean, no variance and no
  Welford state stored anywhere: centre and scale are recomputed from these rows
  on every read. That is what makes erasure exact and lost updates impossible,
  and it is what justifies keeping them under Art. 5(1)(c).
- **`JSONB(none_as_null=True)`** on `candidate_vec` and `top_features`. Without
  it SQLAlchemy writes Python `None` as the JSON value `null`, so
  `WHERE candidate_vec IS NOT NULL` — which is how an auditor asks the question
  — answered *every decision ever made*. Measured on Postgres 16 before the fix:
  10 of 10 rows matched, of which only 2 held a real vector.

**Migrations.** There is no Alembic. `init_db` applies a list of additive
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements at boot, under an advisory
lock, so an existing volume keeps working. That is a stop-gap and §15 says so.

---

## 11. Frontend

- **Demo.jsx** — Turkish card form (Kart Numarası, Son Kullanma, CVV,
  Tutar, Onayla). Card type icon and formatting are cosmetic. The risk
  badge is top-right and is **display only**. Pressing Onayla calls
  `POST /api/demo/charge` and renders whatever came back: charged,
  declined with a block message, a "not enough behaviour yet, try again"
  hint, or the verification modal. The page holds no threshold, no
  decision, and no local payment path; a charge it cannot complete is not a
  success and routes to step-up. The modal posts its code to
  `POST /api/demo/verify` and then charges again so the server can apply
  the recorded verification.
- **Dashboard.jsx** — dark SOC theme. Opens on a key prompt; the entered
  key goes to `sessionStorage` and is sent as `X-Dashboard-Key`. A 401 from
  either poll clears it and returns to the prompt. Then: session table
  coloured by label, D3 line chart of the selected session's raw history,
  horizontal SHAP bars with Turkish feature names, metric cards, 3 s refresh.
- **ProfilePanel.jsx** — the "Müşteri Profili" card, shown only when the
  layer had an opinion: state, input type, reference count, deviation and
  p-value, the most deviating features as z bars, and the "Gölge modu — karar
  etkilenmedi" / "Ek doğrulama istendi" badges. It prints **no** count when the
  backend never read the reference vectors, so "0 / 19" can never claim a
  customer has no history. The card reads the newest decision-audit row with a
  profile opinion, so a session watched *before* Onayla is pressed says "Profil
  yok" even for a mature customer — the card says so, rather than leaving a
  jury to misread it.
- **SyntheticBadge.jsx** — "Sentetik demo verisi", on simulated sessions and on
  decisions made against a synthetic profile. It appears only when the server
  sends the flag as exactly `true`, so an older server shows nothing rather
  than something wrong. The metric cards exclude simulated sessions and say how
  many they left out.
- **KvkkNotice.jsx** — serves `docs/kvkk-aydinlatma.md` at `/kvkk`, imported
  from that one file at build time (a small renderer building React elements,
  never HTML strings), so the link works offline and the notice and the
  document cannot drift apart.
- `VITE_API_URL` selects the backend and is compiled in at build time;
  defaults to `http://localhost:8000`. The dashboard key is deliberately not
  a build variable — see the comment at the top of `Dashboard.jsx`.

---

## 12. Security, robustness and privacy measures already in place

- Typed, bounded, NaN-free input validation at the API boundary.
- Payload size caps on both client and server.
- Scoring off the event loop; atomic session creation; median smoothing.
- No traceback ever returned to a caller.
- CORS allow-list support; credentials disabled under wildcard.
- Bounded reads (200 sessions, 200 history rows) so an all-day stand does
  not grow the dashboard payload without limit.
- Database check constraints keep scores inside 0 – 100.
- Model artefacts are reproducible from a fixed seed (data, forests and
  the LSTM alike) and kept out of git. A pickle written by a different
  scikit-learn is refused rather than scored with.
- Rate limits on minting, scoring and checkout, including the step-up code so
  a six-digit secret cannot be guessed at unlimited speed.
- Raw telemetry is blanked after an hour and rows deleted after a day, so
  behavioural recordings of real people do not accumulate.
- The served bundle is `model.pkl` alone; a missing or mismatched one fails
  loudly and `/api/health` reports it, rather than scoring anyway.
- Session tokens are bound to the session **and** its issue second, expire
  after 30 minutes, and use a MAC domain separate from the challenge's; a
  malformed or non-ASCII token gets 401, not 500.
- The global 422 handler strips FastAPI's `input` and `ctx`, which otherwise
  return the offending value — for a `customer_ref`, a phone number or e-mail —
  to the caller and into the access log.
- `customer_ref` and `profile_id` are **never** logged, not even truncated.
  Database failures on the profile endpoints log the exception *class* only,
  because a driver error renders the statement's bound parameters.
- Uvicorn and nginx access logs are off, so no client IP is written anywhere.
- Retention is swept in two independent transactions under **transaction-scoped**
  advisory locks: telemetry, and profiles. A failure in one cannot roll back or
  stop the other, and the lock cannot leak when a commit returns the connection
  to the pool.
- **Privacy:** the SDK never reads key values, field contents, or the DOM.
  Only coordinates, timestamps and the provenance counters leave the
  browser. The bot score stores nothing that names a person, and recordings
  are deleted on the retention schedule rather than kept indefinitely. The
  per-customer profile (off by default) is different: pseudonymous
  behavioural — biometric — data kept under explicit consent, erasable exactly,
  with an objection tombstone and a human-review route. §18 is how it is
  collected, §19 is what it does, and `docs/kvkk-aydinlatma.md` and
  `docs/dpia.md` are the notice and the assessment.

---

## 13. Tests

The backend suite is **186 tests** across three files, all passing as of
2026-09-26, and the frontend suite is **60** across six files.

```bash
cd backend && DEEPCHECK_SECRET=... DASHBOARD_KEY=... DEBUG=0 python -m pytest -q
cd frontend && npm test && npm run build
```

### `backend/test_scorer.py` — 71 tests

Seven scoring scenarios go through the real `compute_risk`: a natural human
scores low, a sparse typing-only human scores low, a headless bot scores high,
a scripted-motion bot scores high, a bot with one incidental pause still scores
high, a human with a fast burst still scores low, and a fast keyboard-only
session with no mouse scores high.

Authorization and enforcement:

- `/api/analyze` rejects a missing, wrong, or other-session token
- `/api/analyze` rejects telemetry whose events are too old on the sender's own
  clock, and a session whose clock offset drifts
- `/api/analyze` rejects a flush whose time runs backwards within its session
- `/api/analyze` rejects a recording replayed under a new token with its clock
  shifted
- `/api/decision` returns block / verify / warn / allow across the ladder, and
  verify for a session with no telemetry, too little telemetry, or stale
  telemetry
- `/api/demo/charge` never charges a blocked, unverified, thin or unknown
  session; `/api/demo/verify` rejects a wrong code, upgrades verify to allow,
  and cannot lift a block
- the SOC endpoints reject a missing or wrong dashboard key

Tokens and the sequential rule (§5, §15):

- a session token expires after 30 minutes, is bound to its session **and** its
  issue time, and 13 malformed shapes plus non-ASCII bytes all get 401 rather
  than 500
- a challenge signature is not a token, and a token is not a challenge signature
- crossing the bot bound is never charged, whatever the smoothed score says,
  and a fresh step-up unlocks it
- automated evidence ages out only with the window: 7 flushes at 95 followed by
  k at 10 is block or verify for k ≤ 4, ambiguous at 5–6, and allowed at 7 —
  that last row pins the stated limit rather than a property anyone wants
- the SDK's window constants are mirrored in the backend, read from
  `sdk/deepcheck.js`
- the model bundle logs whether the conformal guard can soften a block at all;
  as served it cannot (§15)

And the older defences: a burst past the rate limit gets 429 with
`Retry-After`; the limiter's key table stays bounded; a missing `model.pkl` is
reported by `/api/health`; a handover (a jump of 35+ points over the recent
median) is not smoothed away while a single calm window in a bot session still
is; training seeds torch; client provenance signals are stored but do not move
the score.

### `backend/test_profiles.py` — 97 tests

The statistic (`profiles.py`) without any infrastructure, and the wiring
(`main.py`) against an in-memory model of the profile tables that interprets
the statements themselves — WHERE trees, `IN` subqueries, `ON CONFLICT` with its
`WHERE`, `RETURNING`, `onupdate` — rather than matching SQL text. Highlights:

- the layer **never blocks and never changes the score**: 1,320 combinations
  over HTTP, across two endpoints
- a session is never calibrated against its own learned vector
- full conformal scores every point with one function: rotating the candidate
  role through all 21 points escalates at most `floor(alpha × scorable)` of them
- the same-person challenge rate is within alpha on synthetic identities
- a probation vector never evicts a reference, never shields the next attacker
  session, and is promoted only by a settlement or by a run of passed challenges
- an unanswered challenge never becomes an approval, and the per-customer rate
  bucket never refuses a checkout
- the retention sweeps use transaction-scoped advisory locks, run in their own
  transactions, never sweep an objection tombstone, and neither sweep's failure
  stops the other
- the published constants still match `docs/profile-evaluation.md`, and the
  frontend's Turkish labels still match the backend's

### `backend/test_demo.py` — 18 tests

The synthetic demo customers: seeding is idempotent, a synthetic profile is
never taught by anything but the seeder, the flag reaches the audit row and both
SOC endpoints, and `record_session.py` refuses a simulated session.

Backend tests run against a stub database rather than Postgres, deliberately: an
authorization check that needs infrastructure to test is an authorization check
that stops being tested. Where a property can only exist in Postgres — advisory
lock scope, `ON CONFLICT`, `IS NOT NULL` on a JSONB column — it was verified by
hand against a throwaway Postgres 16 container and the test asserts the code
shape that produces it.

---

## 14. Running it

```bash
docker-compose up --build
```

First start trains the model (about one to two minutes on a laptop).
Then:

- Demo: http://localhost:3000/demo
- Dashboard: http://localhost:3000/dashboard
- API docs: http://localhost:8000/docs

The dashboard asks for the access key on first open. It is the value of
`DASHBOARD_KEY`, typed rather than compiled in, and it is kept in
`sessionStorage` for the tab only.

Environment variables, all documented with their reasoning in `.env.example`:

| group | variables |
|---|---|
| secrets | `DEEPCHECK_SECRET` (signs session tokens), `DASHBOARD_KEY` (guards the SOC endpoints), `DEBUG` |
| network | `CORS_ORIGINS`, `BIND_ADDR`, `VITE_API_URL`, `UVICORN_WORKERS` (default 2) |
| database | `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`, `DATABASE_URL` |
| retention | `RAW_RETENTION_HOURS` (1), `ROW_RETENTION_HOURS` (24), `PROFILE_RETENTION_DAYS` (180), `DECISION_AUDIT_RETENTION_DAYS` (90), `PROFILE_ACCESS_RETENTION_DAYS` (365) |
| demo | `DEMO_ENDPOINTS`, `DEMO_VERIFY_CODE` |
| detection switches | `SHAP_IN_ANALYZE` (off — it is a tuning oracle), `CLUSTER_ESCALATION` (off — measured to flag more legitimate users than bots), `POW_DIFFICULTY_BITS` |
| per-customer profile (all off by default, **no development fallback**) | `DEEPCHECK_MERCHANT_KEYS`, `DEEPCHECK_PROFILE_KEY`, `DEEPCHECK_PROFILE_KEY_VERSION`, `PROFILE_LAYER`, `PROFILE_ESCALATION`, `PROFILE_REVIEW_KEYS` |
| training data | `REAL_TELEMETRY_PATH` |

With `DEBUG=1` the backend falls back to fixed development values and warns on
every boot; with `DEBUG=0` it refuses to start without both secrets, because a
missing secret must never quietly mean "authentication off". The profile
variables have **no** fallback in either mode: unset means the layer is off.
A malformed merchant or review credential stops the boot in every mode.

The frontend image builds the bundle and serves it with nginx. `vite dev` is
available as an override for development:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

CI (`.github/workflows/ci.yml`) trains a reduced model, runs the test suite
without a database, builds the frontend, and fails if a dashboard key ever
appears in the built bundle.

---

## 15. Known limitations and planned work

These are real and the team knows them. Details and acceptance criteria
are in `ESSENTIAL_CHANGES.md`.

1. **Evaluation is still synthetic.** Accuracy is measured on data from the
   same simulator that produced the training set, so it describes fit to the
   simulator and not performance against people. The measurement pipeline
   exists — `record_session.py`, `evaluate.py`, `lab/bot_lab.py` — and
   what is missing is the recordings. See `docs/evaluation.md`. This is the
   most important open item.
2. **A bot that reproduces human timing distributions can evade the
   current features.** The sequence model shows this directly: it separates
   naive automation cleanly (p ≈ 0.94) but scores the human-mimicking
   persona at only p ≈ 0.25. Planned mitigations: kinematic plausibility
   checks, `event.isTrusted`, pointer provenance, and the real-data
   evaluation above to measure them.
3. **Rate limits are per worker.** Counters live in each uvicorn worker's
   memory, so the workers give up to `UVICORN_WORKERS` x the configured ceiling, and a
   restart forgets them. Correct for abuse control, not for quota; a shared
   Redis backend is the fix once there is more than one host.
4. **No schema migration tool.** `create_all()` creates tables but does not
   alter existing ones. As a stop-gap, `init_db` applies a short list of
   additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements at boot so
   existing volumes keep working; Alembic is the proper fix.
5. **No key rotation.** Changing `DEEPCHECK_SECRET` invalidates every live
   session token at once.
6. **The dashboard key is a shared secret, not a login.** It is no longer
   shipped in the bundle, but there are still no per-analyst identities and
   no audit trail of who looked at which session.
7. **The retention sweep has no automated test.** It was exercised by hand
   against the live database (9 rows blanked, features and scores intact),
   but nothing in CI covers it, because it needs a real Postgres.
8. **A clock that steps backwards blinds the SDK.** The replay guard requires
   each flush's newest event not to precede the previous flush's. A client
   whose clock is corrected backwards has its flushes rejected until real
   time catches up. It fails closed (the host page sees an error and routes
   to step-up), but the session is unscored meanwhile.
9. **The demo machine is memory-bound before it is CPU-bound.** Two workers
   fit Docker Desktop's default ~2 GB; four swapped badly enough to make
   `/api/health` take 25 seconds.
10. **No threshold in the per-customer profile layer has been validated on a
    real person.** Everything in `docs/profile-evaluation.md` was measured on
    synthetic identities from `train_model.simulate_identity_sessions()`, and
    every false-challenge rate there is a **lower bound**: a synthetic person
    is a generator with fixed parameters, so their sessions are more
    self-consistent than any real person's. The protocol that would settle it
    is written out in `docs/profile-evaluation.md` §12 — one consenting person,
    20 or more sittings, three device classes, recorded with
    `record_session.py --person`. It is the
    precondition for `PROFILE_ESCALATION=1` outside a demo. Related: every
    number depends on `WITHIN_IDENTITY_SD_RATIO = 0.6`, which nobody has
    measured; at 0.3 / 0.6 / 1.0 the different-person escalation rate on mouse
    moves 70.0% / 47.7% / 27.6%.
11. **Touch is not measured at all.** The code recognises a `touch` input type
    and profiles it separately, but the simulator has no model of a finger, so
    `simulate_identity_sessions` refuses touch and no rate on phones exists.
    There is also no native mobile SDK; the browser SDK works in a WebView.
12. **The layer is only as strong as the merchant's step-up channel, and a
    merchant has no way to report a pass.** A fresh step-up turns the
    challenge straight into an approval, so an attacker who controls that
    channel walks through. DeepCheck only learns of a passed challenge where
    it runs the step-up itself (`/api/demo/verify`); there is no endpoint for
    a merchant's own OTP, so outside the demo neither the per-customer
    challenge budget nor self-healing is ever reached.
13. **A rescued deviation costs the customer their next session.** A probation
    vector is stored but is deliberately **not** a reference until it is
    promoted (by a merchant's `settled` outcome, or by three passed challenges
    in a row). That closes the poisoning hole — one attacker session learned as
    a reference used to halve his own later escalation — at a measured price:
    a different person who passed step-up and comes back in the same pattern
    is challenged again 71.7% of the time on mouse and 62.4% on keyboard. The
    grandchild is asked for the code twice.
14. **An exact replay of a stored reference can no longer be flagged.** Under
    full conformal the replayed session ties with the reference it copied, so
    both p-values stay at or above 2/21 and `p_value_low` never fires (under
    the old leave-one-out rank it was escalated 1.5% of the time). `p_value_low`
    is recorded, not enforced, so behaviour is unchanged — but the low-tail
    replay idea does not work for exact replays and should not be claimed.
15. **A settlement is trusted.** A merchant reporting `settled` at batch
    settlement instead of after the dispute window promotes an undetected
    fraud into the reference set. A later `disputed` deletes it exactly, but
    nothing enforces the ordering.
16. **The profile layer's limits are per worker and in memory**, like every
    other limiter here: the per-customer decision bucket and the deployment-wide
    breaker's cache multiply with `UVICORN_WORKERS` and reset on restart.
17. **The profile layer does nothing against card-testing bots.** They arrive
    at guest checkout with no customer reference and never reach that code. It
    is an account-takeover control, and it is the only one here.
18. **The escalation is itself an oracle**, even with the reason collapsed to
    `step_up`: an attacker can tell `allow` from `verify`. What bounds it is
    that a *match buys nothing* — the layer never approves and never lowers a
    score — so the most the oracle teaches is how to avoid one extra challenge,
    not how to be approved.
19. **Input modality is self-reported**, so an attacker who claims a fresh
    device class turns the profile layer off for himself. Inherent to any
    escalation-only control keyed on attacker-controlled attributes.
20. **Transaction context is recorded, not enforced.** `amount_band` and
    `new_beneficiary` are stored on the audit row and gate nothing, because
    gating on a band nobody has measured is inventing a threshold. This is the
    **first** thing to measure when real traffic exists and the single largest
    lever on challenges-versus-fraud: challenging a 50 TL top-up on a
    behavioural wobble is pure cost, challenging a 15,000 TL transfer to a
    first-time payee is the product. Within-session dispersion and long
    dormancy are recorded and unused in the same way.
21. **Most customers will never mature** at alpha 0.05 with per-modality
    profiles: 19 sessions on the same input type, at most 3 learned a day. That
    is the price of a calibrated challenge rate, not a defect — but it means a
    pilot's profile coverage will be small, and nothing here measures how often
    a real customer checks out.
22. **Conformal validity assumes exchangeability, which drift violates.** The
    alpha bound is exact for sessions that are exchangeable; a customer's
    sessions months apart are not (new device, new posture, age, hurry). Alpha
    is a target on real traffic, not a guarantee. The self-healing rebuild and
    the population breaker are the practical answers.
23. **Single-tenant in practice.** Merchant namespacing is implemented and
    tested, but the deployment has one database, one dashboard key and one
    profile key. There is no mTLS: a shared per-merchant key is the credential.
24. **The conformal guard is inert as served.** Both bundles hold 36 calibration
    values from the lab's scripted Playwright personas — `data/real/` is empty —
    and the highest is 27.71, so every score of 80 or more gets
    p = 1/37 = 0.027 < 0.05 and **no block is ever softened**. The mechanism is
    correct and the calibration sample is not real. `ModelBundle` logs which of
    the four states it is in at load, at WARNING unless the guard actually
    works.
25. **Runtime attestation does not prove a browser.** It shows that some client
    did the proof of work for this session inside the challenge window and
    reported two runtime values inside browser-plausible bounds. Both are
    self-reported, and a plain Python client with hard-coded values obtained a
    valid token 50 times out of 50. It bounds how fast sessions can be minted;
    it is not what stands between an attacker and the API.
26. **Four of the twelve features saturate on real browser telemetry**, one of
    them completely — see §20, which also has the re-capture plan. No figure in
    §7 was produced with all twelve features working.

Resolved since the first draft of this guide: client-side enforcement
(now `POST /api/decision`), unauthenticated endpoints (now signed session
tokens and a dashboard key), the LSTM's tiled input (now the session's
real flush history, trained on real sequences), telemetry replay (clock
skew, forward-time and fingerprint checks), one-flush verdicts (now three
flushes and 30 s freshness), and the browser-side charge and step-up (now
`/api/demo/charge` and `/api/demo/verify`).

---

## 16. Questions a jury may ask, with answers

**Why behaviour instead of device fingerprinting or CAPTCHA?**
Fingerprints are spoofable and CAPTCHAs cost conversions. Behaviour is
collected passively, needs no user action, and is hard to fake
convincingly across twelve independent signals at once. It complements, not
replaces, the other layers.

**Why these twelve features?**
The first six are marginal statistics — variances, entropies, means — and each
captures a different physical or cognitive property: scroll variance and
acceleration variance are motor-control signals, hesitation and entropy are
timing signals, click density is intent, focus change is attention. The
problem with marginal statistics is that an attacker reproduces them with
independent per-step noise, and noise is free: a straight-line bot with two
pixels of jitter halved its risk score and was approved. The other six measure
**structure** that independent noise does not have — speed autocorrelation,
direction consistency, timer quantisation, the heavy tail of human pauses, and
two cross-channel relations (the cursor arrives before the click; a hand takes
real time to move between keyboard and mouse). Reproducing those requires
modelling human motor control rather than adding noise. All twelve are cheap to
compute and each is explainable to a fraud analyst.

Two honest caveats. The structural features are an **unverified mitigation**:
they were designed against this project's own adversary and the lab's "human"
rows are scripted, so nothing has shown they survive a real one (§15.2). And
four of them saturate on real browser telemetry, one of them completely — §20.

**Why one model instead of an ensemble?**
Because the ensemble members were measured and each one made the served
decision worse. The Isolation Forest was inverted (§10). The LSTM lowered
bot detection on browser traffic and caught handovers later than the forest
did (§7). What a temporal model was meant to add, noticing that a session's
past and present do not match, is done explicitly in `smooth_session_score`,
where it can be read, tested and explained to an analyst.

**Why not gradient boosting or a deep model?**
They were measured (`backend/model_selection.py`). With an attack scenario
held out of training, every tree model caught 0-20% of A2, A3 and A4. The
linear model and the MLP each caught one unseen family well (A3 and A2, both
78% at 60), but they challenged 48-68% of an unseen human scenario to do it.
No family generalises to new attacks without also turning on new humans, so
the model family is not the bottleneck; the signals and the data are. Among
models that rank equally well, the one that extrapolates least confidently to
unseen humans is the right one for a payment gate.

**How do you know it works on real people?**
We do not, and one person is not a rate. **Exactly one** real human has ever
been scored by this system (2026-09-25, person p01, 52 recorded flushes). That
session was scored **"Yüksek Risk"** — the system was wrong about the first real
human it ever saw. The cause was found, written down (§7.0), and fixed in the
training distribution, and the same recording now scores **1.2 of 100**. What
that buys is one corrected failure, not a false-positive rate: n = 1, one
machine, one 60 Hz screen, one browser. Everything else we know is twelve
regression scenarios and the synthetic distribution. We would rather show a
smaller real number than a perfect synthetic one.

**What is the false positive rate? What happens to a real customer who is
flagged?**
Not measured on real data, and it cannot be: a rate needs a denominator, and
the denominator here is one person. Every false-positive figure in this project
is measured on **synthetic** humans, which makes each one a **lower bound** — a
simulated person is more self-consistent than a real one. By design a flagged
customer is never silently rejected: 60 – 80 triggers a verification step, only
80 and above blocks, and median smoothing means a single odd reading cannot
trigger either — though §7.0 records one way a single odd reading *did* get
through the smoothing's level-shift bypass, and how that was closed.

**What if the customer's browser blocks the script?**
The page receives no score. An unknown score is treated as "verify", never
as "allow". Once server-side enforcement lands, a missing session token
is rejected at the API.

**Can a bot just call your API with fake human data?**
It can call the API, but it needs a server-minted token, so it can only
post under a session it opened itself. It cannot replay a recording of a
real person: telemetry far from the server clock, time running backwards,
or a fingerprint already seen in any session is rejected. It cannot cash in
one lucky window: a verdict needs three flushes of current behaviour. What
remains is *synthesising* human-shaped telemetry live, which is a model
problem rather than a transport one, and is what the forge-resistant
features and the real-session evaluation are for.

**Can a bot imitate a human?**
A sophisticated bot that copies human timing distributions can lower its
score with the current feature set. This is the hardest open problem in
the field. Our mitigations are per-channel entropy (defeats the merged-
stream trick), acceleration rather than speed, and next, kinematic
plausibility and pointer provenance.

**Why synthetic training data?**
There is no public labelled dataset of payment-form behaviour, and
collecting real fraud traffic requires a bank partner. Synthetic personas
let us build and test the full pipeline; real recorded sessions are the
next step and the pipeline does not change to accept them.

**Isn't 50 ms slow for a payment page?**
The call is asynchronous and off the critical path; the page never waits
for it. Scoring one flush measured 17.7 ms, SHAP included (the older 42 ms
was the three-model ensemble). The checkout decision is measured separately,
end to end in the application, in `docs/profile-evaluation.md` §11. The score
is ready long before a human can finish typing a card number.

**What data do you collect? Is it GDPR / KVKK safe?**
Coordinates, timestamps, scroll offsets, keydown times (never which key)
and focus-loss timestamps. No key values, no field contents, no DOM. The bot
score is keyed to a random session UUID only; no IP address is written to the
database, and the uvicorn and nginx access logs are off. The per-customer
profile layer is the exception: when a merchant enables it with the
customer's explicit consent it keeps a pseudonymous behavioural profile,
which counts as biometric (special-category) data. `docs/kvkk-aydinlatma.md`
lists what is kept and for how long.

**What does the per-customer profile add, when the bot recall is already 0.90
on the simulator?**
Nothing at all against bots — that is the point. Bot recall answers "is a
script driving this page". The profile answers a question the bot score cannot
even ask: *the behaviour is human, but is it **this customer's** human?* That
is account takeover, where a real person with the real credentials, often the
real OTP, operates the account. Measured on synthetic identities, a mature
mouse profile asks for extra verification on **47.5 %** of sessions by a
different person (26.4 % on keyboard, §19.8). The cost is the same currency:
**4.9 %** of the customer's own sessions on mouse and 3.8 % on keyboard get an
extra challenge, and both figures are lower bounds because a synthetic person
is more self-consistent than a real one. A merchant should read that as
"roughly one challenge in twenty legitimate checkouts, for roughly half of
account-takeover attempts on a mature profile", decide whether that trade is
worth it for their fraud rate, and switch the layer on per customer with
consent. That is why it ships off.

**How does a bank integrate this?**
One script tag, one `DeepCheck.init` call, and a server-side check of the
session's decision before authorising the transaction. The backend is a
container they run inside their own network; no data leaves their
perimeter. If the bank wants the per-customer layer as well, its backend adds
a merchant credential and a `customer_ref` to the decision call, and calls the
consent endpoint first — see §18.

**Does this work in an e-wallet, not just a web checkout?**
In anything that renders a web view, yes: the SDK is a plain script with no
dependencies and the API is ordinary REST, so a WebView-based wallet integrates
exactly like a web checkout. Two honest limits. There is **no native mobile
SDK** — a native app would have to produce the same payload itself, which the
API makes possible but which nobody has written. And the model has never been
trained on touch trajectories: the code recognises a `touch` input type and
profiles it separately, but `simulate_identity_sessions` refuses touch, so
**no rate on phones exists** (§15.11). A phone session is scored, and what that
score means has not been measured.

**How does it scale? Can a burst of traffic take it down?**
No load test has been run on the current code, so there is no measured
sessions-per-worker figure; an older "about 20 sessions per worker" came from
the three-model ensemble. §17 works the question through on paper instead,
from per-request measurements that do exist: about 87 concurrent checkout
sessions per vCPU at saturation, roughly 0.71 CPU-seconds per checkout, and
the arithmetic checked against the one real concurrency run in `AUDIT.md` C-3
to within 2.4 %. Past that ceiling requests queue and slow; they are not dropped.
The one failure actually reproduced was memory -- four workers swapping on a
2 GB VM, `/api/health` taking 25 seconds -- which is why the default is 2
workers. §17.6 lists what is still unbounded (no concurrency cap, no database
pool timeout, per-worker in-memory rate limiters), and §17.7 is the design for
load shedding that does not exist yet. The reason overload is survivable is
that every failure path here resolves to `verify`: a saturated DeepCheck asks
everyone for a second factor, which costs conversions and does not let fraud
through (§17.5).

**Does all this data load the server or cost a lot to store?**
Storage is bounded by retention, not by traffic history. Raw telemetry is
blanked after an hour and the rows deleted after a day, so the working set is a
day of traffic: measured row sizes give about **183 kB per checkout** in the
first hour and **19 kB** for the rest of the day, then nothing. A customer
profile is at most 24 vectors of twelve numbers per input type — **32.7 kB**,
and no growth term anywhere, because the buffer evicts. The full arithmetic,
including what 1M customers would cost and the 80 % of stored raw bytes that is
a duplicate of the neighbouring flush, is §17.4.

**Why FastAPI, PyTorch, scikit-learn?**
FastAPI gives async I/O with typed validation for free. scikit-learn's
forests are fast, robust on tabular data, and SHAP supports them natively.
PyTorch is the natural home for the sequence model. All are standard,
auditable and free.

**Why Docker Compose and not the cloud?**
The jury and any bank evaluator can run the whole system on one machine
with one command, offline. The same images move unchanged to Kubernetes
or any cloud later.

**What was the hardest bug?**
Hesitation was measured over a 2 s window in production but about 10 s in
training, so the feature sat at its neutral fallback for most real
flushes. The fix was to timestamp hesitation gaps and prune them on the
same rolling window as every other buffer. Finding it required measuring
feature distributions from live traffic against the training set.

**What would you do with more time?**
In order: server-side enforcement, real LSTM sequences, a real-session
evaluation set, forge-resistant features, then a pilot with a payment
provider to collect labelled traffic.

---

## 17. Capacity, cost and overload — on paper

**No load test has been run against this code.** Nothing in this section is a
measured throughput. It is arithmetic, built from per-request and per-row
measurements that do exist, and every input is named so the reader can redo it.
Any figure taken from here into a report must be labelled **derived from
measurements, not a load test**.

Where a row says *measured*, it was measured; where it says *arithmetic*, it is
a calculation shown in full. Both are on this page on purpose, so nobody has to
guess which is which.

### 17.1 What the load actually is

Load scales with **concurrent visitors, not with transactions**. The SDK flushes
every `DEFAULT_INTERVAL_MS = 2000` ms while a page is open
(`sdk/deepcheck.js`), so one active session is a steady **0.5
`POST /api/analyze` per second** for as long as the checkout page is open, plus
**one `POST /api/decision`** when the customer presses pay. A 60-second checkout
is therefore about 30 analyze calls and 1 decision.

### 17.2 Cost of one request

| path | figure | kind | source |
|---|---|---|---|
| `compute_risk` (features + Random Forest + SHAP) | 17.7 ms | measured | `backend/scorer.py`, §7 (31.7 ms before the Isolation Forest left the score) |
| `/api/decision`, profile layer off, p50 / p95 | 5.0 / 7.4 ms | measured | `docs/profile-evaluation.md` §11 (container, n = 300) |
| `/api/decision`, profile layer on (shadow), p50 / p95 | 24.0 / 34.8 ms | measured | same |
| `/api/decision`, profile layer on, escalating, p50 / p95 | 16.0 / 21.8 ms | measured | same |
| `/api/analyze` end to end | **~23 ms** | **arithmetic** | 17.7 ms of scoring + the 5.0 ms p50 the layer-off decision path measures for validation, session upsert, row insert and commit |

That last line is a construction, not a measurement, and it is the single
biggest source of error in this section. `/api/analyze` has never been timed on
its own, and it writes a larger row than a decision reads.

### 17.3 The ceiling, and the one measurement that validates the method

Scoring is CPU-bound and runs in the threadpool (`run_in_threadpool`, §5), so
the ceiling is CPU-seconds available per second — that is, allocated vCPUs —
**not** the number of uvicorn workers. Per vCPU:

```
  1000 ms / 23 ms   = 43 analyze per second per vCPU        (arithmetic)
  43 / 0.5 per user = 87 concurrent active sessions per vCPU (arithmetic)
```

The same arithmetic was once checked against a real concurrency run. `AUDIT.md`
C-3 (2026-07-31, the old three-model ensemble, ~74 ms per request, one worker)
measured **13.2 req/s** with 20 concurrent calls and derived ~26 sustainable
sessions. The model predicts `1000 / 74 = 13.5` req/s — **2.4 % from the
measurement**. That is one point on old code, not a validation of today's
number, but it is the reason the method is quoted at all.

| container | at 100 % CPU | at 60 % CPU (burst headroom) |
|---|---|---|
| 1 vCPU | ~87 concurrent sessions | ~52 |
| 2 vCPU (the `docker-compose` default shape) | ~174 | ~104 |
| 4 vCPU | ~348 | ~209 |

**Per-transaction cost.** 30 analyze at 23 ms plus one decision at 24 ms (the
profile layer on) is **0.71 CPU-seconds per checkout**, so one vCPU-hour
carries roughly **5,000 checkouts** (3600 / 0.71). At list prices for a
general-purpose cloud vCPU that is small compared with the payment itself; the
real costs of running this are Postgres and operations, not inference.

**What the profile layer costs.** It is inside that 0.71, not on top of it.
The layer adds 19 ms p50 (24.0 against 5.0) to **one request in about
thirty-one**, so the checkout goes from 0.695 to 0.714 CPU-seconds: **+2.7 %**.
It is not a capacity question; it is a latency question, and §19.8 is where the
latency lives.

### 17.4 Storage

Row sizes below were **measured on Postgres 16** (`pg_total_relation_size` over
100,000 generated rows of each table, created with
`CREATE TABLE … (LIKE … INCLUDING ALL)` so the real indexes are included, then
dropped; `behavior_data` at 10,000 rows). They include heap, TOAST and indexes.

| row | on disk | note |
|---|---|---|
| `behavior_data`, raw telemetry present | **6.1 kB** | a typical 10 s mouse window: 300 pointer samples, 2 clicks, 8 scrolls, 3 hesitations, 25 keys ≈ 11.6 kB of JSON, which Postgres TOASTs and compresses to ~4 kB |
| `behavior_data`, after the 1 h blanking | **644 B** | 400 B heap + 234 B index; the twelve features and the score survive (the live demo database gives `pg_column_size` = 404 B for the tuple alone) |
| `customer_profile_vectors` | **1,363 B** | 1,028 B heap + 335 B index; `vec` and `disp` are 408 B of JSONB each |
| `decision_audit`, profile opinion + compared vector | **1,051 B** | |
| `decision_audit`, no profile opinion | **268 B** | |
| `customer_profiles` | ~185 B | composite-row estimate, not a table measurement |

The payload caps bound the worst case: at `max_length` (2000 mouse points, 500
clicks, 1000 scrolls, 500 hesitations, 200 focus, 1000 keys) one flush is
**160 kB of JSON**, about 14× the typical window. That is the ceiling a single
caller can force, and it is why the caps exist.

**Per checkout** (60 s, 30 flushes, arithmetic from the rows above):

```
  first hour           30 x 6.1 kB  = 183 kB   (raw telemetry present)
  hour 1 to hour 24    30 x 644 B   =  19 kB   (raw blanked, features kept)
  after 24 h                          0 B      (rows deleted)
  kept for 90 days     0 or 1 decision_audit row, 268 B or 1,051 B
```

A decision-audit row is written only when the action is not `allow`, or when
the profile layer had any opinion at all — so with the layer off, a clean
checkout leaves **nothing** after 24 hours.

**Working set.** Because raw telemetry lives one hour, the un-blanked set is one
hour of traffic, not a history:

```
  1,000 checkouts/hour = 30,000 flush rows/hour
  raw, last 1 h        = 30,000 x 6.1 kB      = 183 MB
  blanked, hours 1-24  = 23 x 30,000 x 644 B  = 444 MB
                                          total ~630 MB, and it stops there
```

**Per customer profile**, at the caps (20 references + 4 probation vectors per
modality):

```
  one modality    24 x 1,363 B  =  32.7 kB
  two modalities                =  65.4 kB
  1M customers, one modality    =  32.7 GB
  1M customers, two modalities  =  65.4 GB
```

That is a ceiling, not an expectation: a customer with five sessions stores five
vectors (6.8 kB), and §15.21 expects **most customers never to mature**. The
profile table is bounded by construction — there is no growth term anywhere,
because the buffer evicts.

**The 80 % that is a duplicate.** The SDK keeps a 10 s rolling window and
flushes every 2 s, so consecutive flushes overlap by 8 of 10 seconds and each
event is uploaded and stored in up to **five** consecutive rows. Of the raw
bytes in the first hour, about **4/5 are a copy of a neighbouring row's**;
storing each event once would cut the 183 kB per checkout to roughly 37 kB.

It is stored that way on purpose, and the reasons are worth stating before
someone calls it waste:

- the feature vector must be computed over the same 10 s window the model was
  trained on, so the window is the unit of evidence, not the event;
- the row *is* the request as received, which is what makes
  `payload_hash` replay detection and any later dispute meaningful;
- a store-once design would have to reassemble a window at read time, which
  moves cost from disk (cheap, and deleted in an hour) to the request path
  (expensive, and on the customer's clock).

If storage ever becomes the constraint, the cheapest change is not
de-duplication but lowering `RAW_RETENTION_HOURS` — the raw channels are read by
nothing after scoring except `record_session.py`, which runs promptly.

### 17.5 Can many simultaneous requests crash it?

Not by CPU. Past the ceiling in §17.3, requests **queue** and latency rises;
they are not dropped and the process does not die. Four things are bounded on
purpose:

- **Request size.** Every list in the analyze payload has a `max_length`, so one
  caller cannot make one request arbitrarily expensive (§17.4).
- **The event loop.** `compute_risk` runs in the threadpool. Before that fix it
  ran inline and blocked everything, including `/api/health`: `AUDIT.md` C-3
  measured event-loop lag of 762 ms at p95 and 1510 ms at maximum with 20
  concurrent flushes. That is the failure that matters operationally — a health
  check that times out gets a *healthy* container restarted by the
  orchestrator, which is how "slow" turns into "down".
- **Per-caller rate.** `RATE_LIMITS` bounds session minting per IP (10/min),
  flushes per session (60/min), checkouts per session (20/min), profiled
  decisions per **customer** (60/h) and profile admin calls per merchant
  (600/min). All per worker and in memory, so the effective ceiling multiplies
  with `UVICORN_WORKERS` and resets on restart (§15.3).
- **Fail-closed, everywhere.** This is the property that makes overload safe
  rather than merely survivable:

| failure | result |
|---|---|
| no session row, too few flushes, stale telemetry | `verify`, never `allow` |
| model not loaded | 503 on `/api/analyze`; `/api/health` says so |
| score missing or non-finite | treated as `verify` (`get_action`) |
| profile read fails (database) | 503 — the merchant must treat it as `verify` |
| profile breaker cannot be counted | behaves as if the ceiling were reached, i.e. the layer says nothing |
| audit write fails | logged, rolled back; the decision still stands |
| decision call fails entirely (network, 5xx) | the merchant's integration treats it as `verify` — `README` "Entegrasyon" states this, and it is the integrator's obligation, not something DeepCheck can enforce from the outside |

A saturated DeepCheck therefore degrades into "ask everyone for a second
factor", which costs conversions and does not let fraud through. That is the
correct direction for a payment gate, and it is why the absence of a load test
is a gap in *sizing*, not in safety.

**The one way it has actually been made to fall over is memory, not load.**
Four workers hold four copies of the forests, the SHAP explainer and the torch
runtime, ~300–400 MB each. On a Docker Desktop VM with the default ~2 GB, with
Postgres and nginx alongside, that machine swaps: `/api/health` took **25
seconds** to answer while the CPU sat at 1 % (`backend/entrypoint.sh`). This is
why `UVICORN_WORKERS` defaults to 2. Sizing a host by cores while ignoring the
~400 MB per worker reproduces it.

### 17.6 What is not bounded

Stated so the table above is not read as a guarantee:

- There is **no cap on concurrent in-flight requests**, and the database pool
  is left at SQLAlchemy's defaults: 5 connections plus 10 overflow per worker,
  and a 30-second checkout timeout. Beyond 15 in-flight queries per worker,
  requests wait; a 30-second wait at a payment gate is indistinguishable from
  hanging. Under a burst this converts a CPU queue into a connection queue, and
  the request that eventually gives up is the customer's.
- The rate limiters and the profile breaker's cache are **per worker and
  in memory**.
- Postgres has not been sized against this write rate, and the retention sweep's
  `DELETE` is unbatched (`AUDIT.md`).
- **No load test has been run on this code.**

### 17.7 Design for load: not implemented

Written as a design so it is not mistaken for a feature. None of this exists in
the tree today.

1. **Shed load instead of queueing it.** A semaphore around the scoring
   threadpool with a fixed depth, and a fast `503` with `Retry-After` once it is
   full. Shedding at the door is the difference between a slow site and an
   unreachable one, and `/api/analyze` is the right thing to shed: a dropped
   flush costs one 2-second window of evidence, and the SDK's rolling buffer
   re-sends it on the next flush anyway. `/api/decision` must **not** be shed —
   it is one request per checkout, and its failure mode is already `verify`.
2. **SDK back-off.** The SDK today re-registers once on a 401 and otherwise
   raises on any non-2xx (`onError`, `deepcheck:error`); it does **not** back
   off on 429 or 503. The design is: on 503 or 429, honour `Retry-After` if
   present, otherwise double the interval up to a ceiling (say 16 s) and return
   to 2 s after a success, with full jitter. Without jitter a deployment that
   restarts hands every open page the same retry instant.
3. **Horizontal scaling.** Containers hold no session state, so this is more
   containers behind a load balancer, with Postgres the only shared state. The
   two things that are not stateless are the in-memory rate limiters and the
   profile breaker's cache; both get weaker (not wrong) as instances multiply.
4. **A shared limit store.** Redis, keyed exactly as `RATE_LIMITS` is today, so
   a limit means the same thing regardless of worker count. This is also what
   makes the profile breaker a deployment-wide ceiling in fact and not only in
   name.
5. **A pool checkout timeout worth the name and a concurrency cap**, so the
   system fails fast instead of accumulating queued requests. A checkout
   timeout of about a second, an explicit `pool_size`, and a ceiling on
   in-flight requests are all one-line changes to `get_engine()` and the app
   factory; what is missing is the load test that would set the numbers.

The honest order of work is 5, then 1 and 2, then a load test — and only then
does this section get replaced with measurements.

---

## 18. How customer data is collected, and how a customer is recognised

Two different kinds of data are collected, and the whole privacy argument
depends on keeping them apart.

| | bot score (§4 – §7) | per-customer profile (§19) |
|---|---|---|
| who it is collected from | every visitor | only customers a merchant names, and only with recorded consent |
| what identifies the record | a random session UUID | `profile_id`, an HMAC of (merchant, customer reference) |
| what is stored | raw telemetry, 12 features, score | 12 normalised numbers per session, never raw telemetry |
| how long | 1 h raw / 24 h rows | 180 days idle, or until erasure |
| default | always on | **off** unless three variables are set |

### 18.1 Behaviour: what leaves the browser

`sdk/deepcheck.js` records pointer coordinates, click coordinates, scroll
offsets, keydown **times** (never `e.key`, never `e.code`, never a field
value), tab-hidden timestamps, and three provenance counters (§4.1). Nothing
else. There is no cookie, no `localStorage` write, no device fingerprint, no
IP address in the database, and no DOM read.

The session id is minted by the server (`POST /api/session`), so the browser
cannot choose what to post under. That id is a random UUID with no link to a
person, and its rows are deleted 24 hours later.

### 18.2 Identity: where the customer's name comes from

It does not come from the browser. The merchant's backend already knows who is
logged in, and it is the party that names them:

```
browser  --- session_id + token ------------> merchant backend (it already knows the customer)
merchant backend --- POST /api/decision ----> DeepCheck
                     { session_id, customer_ref: "musteri-48213" }
                     X-Merchant-Id: acme
                     X-Merchant-Key: <32+ chars>
```

Consequences, each enforced in code rather than promised:

1. **A `customer_ref` without a valid merchant credential is refused** — 400
   when the headers are missing, 401 when they are wrong (`_profile_request`).
   The reference is attacker-supplied otherwise, which was the first blocker
   the design review raised.
2. **The raw reference is never stored.** It is turned into
   `profile_id = HMAC-SHA256(DEEPCHECK_PROFILE_KEY, len||merchant_id ‖ len||customer_ref)`
   inside one function and the plaintext is discarded. It is never written to a
   log, never returned in a response, never placed in a URL path or query
   string, and the global 422 handler strips FastAPI's `input`/`ctx` echo so a
   malformed reference cannot come back out in an error body.
3. **Length-prefixed, not concatenated.** Merchant `a|b` with reference `c` must
   not derive the same id as merchant `a` with reference `b|c`; with a separator
   encoding that collision is reachable by a merchant simply choosing an id
   containing the separator, and it would link one person across two merchants.
4. **Per-merchant pseudonyms.** The same person at two merchants is two
   unrelated 64-hex strings. Nothing in the database links them.
5. **No profile without consent.** `POST /api/profile/consent` is the only code
   path that creates a profile row, and it records a lawful basis.
   `/api/decision` can never create one. No consent → no row → no personal data
   → no escalation, and the refusal costs the customer nothing: they are never
   challenged for refusing.
   The one exception is the reserved `demo` namespace used by the demo page,
   which no merchant may claim (`demo` is rejected at boot) and which is
   excluded from every reported measurement.

`profile_id` is never logged either — not even truncated. The 12-character
prefix pattern used for `payload_hash` is fine for a content hash and wrong for
a stable cross-session identifier.

### 18.3 Recognition: what "the same customer" means here

One session becomes **one vector**: for each of the twelve features, the median
over that session's non-provisional flushes, and only for features the session
genuinely *measured* — `behavior_data.measured_mask` is what distinguishes a
measured 0.3 from a `NEUTRAL_DEFAULTS` 0.3. Without that mask a profile would
be measuring "how much telemetry did this session produce", not "is this the
same person".

That vector is compared against **this customer's own stored vectors**, for the
same input modality and the same `FEATURE_SCHEMA_VERSION`, and against nothing
else. There is no population model, no cross-customer comparison and no pooled
fallback. §19 is the statistic.

### 18.4 How long recognition takes to become possible

| step | requirement | source |
|---|---|---|
| a session teaches at all | ≥3 analysed flushes and ≥4 measured features | `profiles.PROFILE_MIN_FLUSHES`, `PROFILE_MIN_FEATURES` |
| per day | at most 3 learned sessions per Europe/Istanbul day | `PROFILE_LEARN_PER_DAY` |
| maturity | 19 reference sessions **per input modality** | `ceil(1/0.05) − 1`, §19.2 |

So the floor is **seven Istanbul days** of allowed checkouts on one input type,
and only if the customer uses the same input type every time. Measured on
synthetic identities, the median number of sessions to maturity was 19 on mouse
and 19 on keyboard (`docs/profile-evaluation.md` §5) — that is the arithmetic
floor and says nothing about how often a real customer checks out, which
nothing in this project measures. §15.21 says plainly that **most customers
will never mature**, and that is the price of a calibrated challenge rate, not
a defect.

### 18.5 The customer's controls

| route | effect |
|---|---|
| `POST /api/profile/consent` | creates the profile, records the lawful basis |
| `POST /api/profile/erase` mode `erase` | deletes the profile row and every vector; clears the profile link on sessions, on decision-audit rows (with their stored vector) and on access-audit rows |
| `POST /api/profile/erase` mode `object` | the same deletions, plus a tombstone that survives and makes a later re-consent answer 409 |
| `POST /api/outcome` | the merchant marks a session `settled` (promotes its vector) or `disputed` (deletes it) |
| `GET /api/profile/review/{session_id}` | human review of a contested decision, behind a **per-operator** credential, writing an access-audit row |

Both erase modes answer 204 with the same body whether or not a profile
existed, so the endpoint is not an existence oracle.

### 18.6 The honest part

**No real customer has ever been collected by this system.** `data/real/`
contains only a README. The jury prototype runs on **synthetic demo customers**
(`backend/demo_seed.py`): simulator identities seeded with 20 mouse and 20
keyboard sessions each, flagged `is_synthetic` in the database, labelled
"sentetik" in the customer reference, badged "Sentetik demo verisi" in the SOC
panel and excluded from every measurement. They demonstrate the **mechanism**,
not accuracy on real people.

---

## 19. The per-customer behavioural profile

Implemented in `backend/profiles.py` (the statistic) and `backend/main.py` (the
wiring); measured in `docs/profile-evaluation.md`; ships **off**.

### 19.1 The contract, in one sentence

> A customer's own behavioural history may ask for **extra verification** and
> nothing else. It never blocks, never approves, never lowers a risk score,
> never raises a risk score, and a session that *matches* the profile gets no
> benefit whatsoever.

The only state transition it can cause is `allow | warn → verify`. That is
asserted directly by a test that sweeps 1,320 combinations (10 evidence
outcomes × 11 profile states × 3 step-up states × shadow/enforcing × two
endpoints) over HTTP and checks that the score, the label and the ladder never
move.

Why *only* verification, stated three ways so it does not read as timidity:

- **Product.** A deviation is not evidence of fraud. An elderly customer may
  hand the phone to a grandchild to pay this once; a new laptop, a broken
  wrist, a train journey and a switch to assistive input all look identical to
  a statistic. Extra verification is the proportionate answer to all of them.
- **Law.** The profile is behavioural biometric data (GDPR Art. 4(14), KVKK
  Art. 6). Art. 22(4) bars a solely-automated *decision* on Art. 9 data, so a
  layer that may only ask for a second factor is the only shape available.
- **Security.** A bot replaying the victim's recorded behaviour matches the
  victim's profile perfectly. Trust-on-match would hand the attacker the win,
  so a match must buy exactly nothing.

**What it is for, and what it is not for.** It is the only control in DeepCheck
aimed at human **account takeover**. It contributes **nothing** against
card-testing bots — those arrive at guest checkout with no customer reference
and never reach this code — and its fraud-prevention value is bounded by the
strength of the merchant's step-up channel: if the attacker has already phished
the OTP, this layer asks a question he can answer.

### 19.2 The statistic

`backend/profiles.py` is pure: no database, no FastAPI, no model bundle.

**Modality.** A profile is compared only within one input modality —
`touch`, `mouse` or `keyboard` — derived from the pointer-type mix the SDK
already records, with **no pooled fallback anywhere**. Touch produces no
`mousemove` at all, so `hiz_otokorelasyonu`, `yon_tutarliligi` and
`tiklama_oncesi_hareket` come from a different distribution on a phone than on
a mouse; pooling gives a profile that is either blind (within-person variance
swamped by the mixture) or permanently hostile to whichever device the customer
uses less. The modality is **self-reported**, and therefore evadable — an
attacker who claims a fresh device class turns the layer off for himself. That
is inherent to any escalation-only control keyed on attacker-controlled
attributes, and it is written down rather than pooled away.

**Session vector.** Per-feature median over the session's non-provisional
flushes; a feature participates only if it was measured in at least
`PROFILE_MIN_FLUSH_OBS = 2` of them. Median, not mean, because one odd flush
must not set the customer's centre — and a customer's own sessions are exactly
where one odd flush is most likely to be innocent.

**Reference statistics.** Centre and scale are the **median and MAD** of the
reference vectors (MAD scaled by 1.4826), recomputed on every read. There is no
Welford state and no stored summary anywhere: the ≤20 stored vectors *are* the
statistic. That is what makes erasure exact (delete rows), forgetting free
(evict the oldest) and lost updates impossible (one `ON CONFLICT DO NOTHING`
insert).

**Degenerate features are excluded, not floored.** A feature whose spread is
below `PROFILE_SCALE_FLOOR` carries no information about this customer and is
dropped. `odak_degisimi` is an integer count that is 0 for most people in most
sessions; floor its scale and a customer who alt-tabs once to read the SMS code
*this very system just sent them* produces an enormous z, lands in the top 3
every time, and is challenged for doing exactly what the previous challenge
asked. "Never varied in 19 sessions" is not evidence that varying once is
fraud.

**Deviation.** `z_f = |x_f − centre_f| / scale_f`, and the score is the mean of
the largest `PROFILE_TOP_K = 3`. Top-K is a poor *threshold* (correlated
features triple-count one departure; the null distribution of a maximum moves
with the feature count) and a good *score* and a good *explanation* — "these
three things were unusual" is reviewable by a human, an opaque distance is not.
It is used only as the score.

**The threshold is a rank, and the rank is full conformal.** The candidate and
every reference are scored by **one function**,
`point_deviation(point, the other n points)`: the candidate against the n
references, and each reference against the other n − 1 **plus the candidate**.
With n + 1 exchangeable points each scored against the other n, the candidate's
rank is uniform and `P(p ≤ alpha) ≤ alpha` holds **in finite samples** — not
asymptotically, not approximately. Ties count against escalation, which only
makes it more conservative.

This replaced a leave-one-out rank that scored the candidate against all n
references but each reference against only the other n − 1, on the subset of
the *candidate's* features that reference could be scored on: two different
statistics, so alpha was not the rate even on synthetic people. Measured on the
same synthetic sessions, 2026-09-16: mouse 6.0 % same-person challenges under
leave-one-out against 4.9 % under full conformal (paired difference +1.2 pp,
95 % +0.2 … +2.4); keyboard 2.9 % against 3.8 % (−0.9 pp, 95 % −1.4 … −0.4).
Wrong in **both** directions, which is exactly what "not the same function"
predicts. The shipped rank now agrees with an independently written
implementation on 0 disagreements in 8,000 + 8,000 verdicts
(`docs/profile-evaluation.md` §5).

**Maturity is arithmetic, not a tuned number.** With n references the smallest
attainable p-value is 1/(n+1), so at alpha 0.05 the layer *cannot* fire below
**n = 19**. A "minimum 5 sessions" would be judgement dressed as statistics: the
threshold could never be reached and the code around it would look calibrated
while doing nothing. Below 19 the layer does not compare at all — an immature
profile is the absence of an opinion, not a weak one.

**Every other outcome is a named abstention** (`thin_session`,
`too_few_features`, `immature`, `suppressed`, `budget_exhausted`, `breaker`,
`rate_limited`, `no_profile`, `disabled`), so the audit row and the SOC panel
can always say *why* the layer had no opinion.

### 19.3 A rescued session is stored but is not a reference

When a profile deviation is challenged and the customer **passes** the step-up,
that session is learned with `probation = true`. A probation vector is stored —
it is evidence for the SOC panel and for a human reviewer — but it is **not a
reference**: the deviation, the conformal rank and the maturity count never see
it until it is promoted.

The measurement that forced this: with the rescued session counted as a
reference, one passed step-up shielded the attacker's later sessions. Escalation
of an attacker on a victim's mature mouse profile fell from 48.5 % with none of
his sessions learned to 25.0 % with one and 12.5 % with four. With probation
vectors excluded the curve is **flat — 47.5 % at 0, 1, 2, 4 and 6 attacker
sessions stored**, and all 200 victims' reference sets came back identical to
the one with none (`docs/profile-evaluation.md` §7; the storage cap of 4 is
exercised by the 6 case).

Two routes promote a probation vector:

- the merchant reports `settled` for that session (`POST /api/outcome`), or
- **self-healing**: `PROFILE_HEAL_AFTER = 3` challenges in a row, each passed,
  *and* a run of at least that many probation vectors newer than the newest
  reference. A single pass is never promoted, whatever the counter says.

The accepted price is written down rather than hidden: a different person who
passed step-up and comes back in the same pattern is challenged **again**
71.7 % of the time on mouse and 62.4 % on keyboard. The grandchild is asked for
the code twice.

### 19.4 Learning, forgetting, and the bounds on both

| mechanism | value | what it bounds |
|---|---|---|
| ring buffer | `PROFILE_BUFFER_MAX = 20` references per (profile, modality) | one absorbed vector's influence is 1/20; the profile forgets the customer's older self |
| probation slots | `PROFILE_PROBATION_MAX = 4`, separate from the 20 | a probation vector can never evict a reference; at most 24 rows per (profile, modality) |
| learn rate | `PROFILE_LEARN_PER_DAY = 3` per Europe/Istanbul day | an attacker with a valid session cannot rebuild the envelope in an afternoon |
| learn-once | unique index on (profile_id, session_id) | a double learn is physically impossible, not merely checked for |
| self-healing | `PROFILE_HEAL_AFTER = 3` passes → keep newest `PROFILE_HEAL_KEEP = 10` | a stale profile rebuilds and drops **below** maturity, so the layer abstains until it knows who the customer is now |
| challenge budget | `PROFILE_MAX_ESCALATIONS = 3` **passed** challenges per 30 days | a customer who keeps proving it is them is challenged at most three times a month — a hard ceiling on the discrimination risk (inferred disability, tremor, assistive input) that a statistic cannot give |
| population breaker | `PROFILE_BREAKER_MAX = 50` distinct customers challenged per hour, deployment-wide | a mis-calibration is discovered by a ceiling rather than by a merchant complaining about checkout abandonment |

The budget is counted in **passed** challenges, not issued ones. A budget spent
by *issuing* a challenge is an off switch for the very attacker this layer
exists for: without the OTP he cannot pass one, but he can press pay again, and
the fourth unanswered challenge used to become an approval. The honest cost of
counting passes is that DeepCheck only hears of a pass where it runs the step-up
itself (`/api/demo/verify`), so outside the demo neither the budget nor
self-healing is ever reached (§15.12).

### 19.5 What the client is told

Six internal reasons collapse to one public `step_up` (`PUBLIC_REASONS`):
`cluster`, `conformal`, `ambiguous`, `sequential`, `profile_deviation` and
`profile_rate_limited`. Each of them names the evidence that convicted the
caller, and that is a tuning signal: submit, read which check fired, adjust,
repeat. A dedicated `profile_deviation` reason would additionally confirm that
this customer reference exists and is mature, and would let an attacker
binary-search the victim's behavioural envelope. The reasons that describe the
**state of the telemetry** rather than the verdict (`unknown_session`,
`insufficient_evidence`, `stale`, `verified`, `score`) are left alone: the host
page needs them to say "keep going, we need a few more seconds" instead of
showing an OTP box, and they say nothing about which check convicted anyone.

The real reason is kept in `decision_audit` and shown in the SOC panel, and the
data subject's transparency right is met by the aydınlatma metni
(`docs/kvkk-aydinlatma.md`) and by the human-review endpoint — not by the API
response to the party being scored.

### 19.6 Privacy posture

- **Classification.** Behavioural biometric data. Pseudonymisation does not
  change that (Recital 26), so "we hash the reference" is not a lawful basis.
  Legitimate interest is not available; `consent_basis` is a required recorded
  field.
- **What is stored, for how long** (also in `docs/kvkk-aydinlatma.md`):

| stored | what it is | retention |
|---|---|---|
| `profile_id` | HMAC of (merchant, customer reference); the raw reference is never stored | until erasure, or 180 days idle |
| ≤20 references + ≤4 probation vectors per modality | 12 normalised numbers per session, **never** raw telemetry | with the profile |
| consent basis + timestamp | the lawful-basis record | with the profile |
| decision audit | action, reason, deviation, p-values, top features, and the compared vector when a deviation was found | 90 days |
| access audit | which operator read a profile and when | 365 days |

- **Erasure is exact** because there is no derived summary to unwind: delete the
  rows. An objection leaves a tombstone that survives erasure so a later
  decision cannot silently recreate what the customer refused.
- **Never logged:** `customer_ref` and `profile_id`, in any form, including
  inside an exception message. Database failures on these endpoints log the
  exception **class** only, because a driver error renders the statement's bound
  parameters — which here are profile ids.
- **Key rotation is a silent mass reset.** A pseudonym cannot be re-keyed
  without the raw references, which this system deliberately never keeps, so a
  new `DEEPCHECK_PROFILE_KEY` orphans every profile and no erasure request can
  match them again. `key_version` exists so a rotation can dual-write for a
  window. Documented next to the variable in `.env.example`.
- **Encryption at rest is volume-level only**, as a recorded decision rather
  than silence: the statistic is recomputed on every decision, so a
  column-encryption key would have to live in the same process as the data, and
  the gain would be confined to the offline disk-theft scenario volume
  encryption already covers.
- **`/api/score/{id}`** returns a bounded, uniformly shaped profile block and
  never `profile_id` or raw vectors. Raw vectors live behind
  `GET /api/profile/review/{session_id}`, which needs a **per-operator**
  credential (`PROFILE_REVIEW_KEYS`) rather than the shared dashboard key, and
  writes a `profile_access_audit` row — a shared password cannot attribute a
  read to anyone.

### 19.7 What this layer deliberately does **not** do

1. No Welford or incremental statistics; a bounded buffer instead.
2. No foreign key on `sessions.profile_id` or `decision_audit.profile_id` —
   sessions live 24 h and profiles 180 days, and a FK would let a profile delete
   abort the telemetry sweep and silently stop raw-telemetry blanking.
3. **No trust on match, ever.**
4. No block, no score change, no label change.
5. No amount / beneficiary gate. `risk_context` is **recorded, not enforced**:
   gating on a band nobody has measured is inventing a threshold.
6. No low-tail (replay) enforcement. `p_value_low` is computed and stored only.
7. No dispersion term in the statistic; the mid-session handover it would catch
   is already caught per-flush by the measured level-shift rule (185/185).
8. No pooled cross-modality comparison and no pooled fallback.
9. No profile creation from `/api/decision`.
10. No `profile_id` or `customer_ref` in any application log.
11. No mTLS — a shared per-merchant key is the credential.
12. No separate "profile challenge class" that a prior step-up cannot satisfy.
13. No column-level encryption.
14. Nothing profile-related on the Demo page's risk badge or in `/api/analyze`.
15. No change to the SDK, the model files, `compute_risk`'s score or
    `smooth_session_score`.

### 19.8 What it measured, and on what

Everything below was measured by `backend/profile_lab.py` on **synthetic
identities** from `train_model.simulate_identity_sessions()` (200 identities,
20 fresh sessions each per modality, 2026-09-19 run), reading the served bundle
and never writing it. **No real customer has been measured**, and every
false-challenge rate is a **lower bound**: a synthetic person is a generator
with fixed parameters, so their sessions are more self-consistent than any real
person's, and the bias runs in the direction that flatters the product.

| | mouse | keyboard |
|---|---|---|
| same person challenged (**false challenge, lower bound**) | 4.9 % (4.0–5.9) | 3.8 % (3.0–4.7) |
| a different person escalated | 47.5 % (44.0–50.8) | 26.4 % (23.3–29.6) |
| abstained | 0.0 % | 19.6 % |
| AUC of the p-value, where evaluated | 0.841 | 0.735 |

Read the first row against the theory: with 20 references the smallest p-value
is 1/21, so a legitimate session is challenged exactly when it is further from
the customer's own centre than all 20 of their own past sessions. For
exchangeable sessions that is at most 1 in 21 (4.8 %) — the measured 4.9 % is
that floor, not a tuning result.

Read the second row honestly too: **roughly half** of account-takeover attempts
on a mature mouse profile get an extra challenge, and half do not. On keyboard
it is a quarter. This is a control that raises the cost of taking over an
account; it is not a detector.

Three constants were selected by rules fixed before the data was looked at, and
came out the same in both runs: `PROFILE_MIN_FEATURE_OBS = 8` (the knee of the
scale-error curve), `PROFILE_SCALE_FLOOR = 0.0062` (5th percentile of non-zero
spreads), `PROFILE_TOP_K = 3`.

Every number depends on `WITHIN_IDENTITY_SD_RATIO = 0.6` — the assumption that
a person varies 0.6 as much between their own sessions as people vary between
each other. **Nobody has measured it.** At 0.3 / 0.6 / 1.0 the different-person
escalation rate on mouse moves 70.0 % / 47.7 % / 27.6 %.

**Latency** (`docs/profile-evaluation.md` §11; a throwaway Postgres 16, the
ASGI app called directly, n = 300 per configuration, budget 50 ms):

| topology | layer off p50 / p95 | shadow p50 / p95 | enforcing, escalates | enforcing, learns |
|---|---|---|---|---|
| container, docker-compose topology | 5.0 / 7.4 ms | 24.0 / 34.8 ms | 16.0 / 21.8 ms | 24.0 / 31.7 ms |
| the same, repeat run | 4.8 / 7.4 ms | 23.8 / 37.9 ms | 15.9 / 22.3 ms | 23.9 / 33.6 ms |
| Windows host via Docker Desktop port mapping | 12.0 / 15.1 ms | 42.3 / 53.8 ms | 28.6 / 37.3 ms | 42.2 / 53.2 ms |
| the same, repeat run | 12.0 / 14.8 ms | 42.4 / 52.5 ms | 28.7 / 35.0 ms | 42.2 / 51.3 ms |

Inside the container topology every configuration is within the 50 ms budget at
p95. Through Docker Desktop's port mapping on the Windows development host,
**four** configurations are not (shadow and enforcing-learns in both runs), and
that is a property of the development host's networking, not of the layer. The
layer costs about **19 ms p50** on the decision path: three reads (profile row,
this session's flushes, the reference vectors), the statistic, and the audit
row — plus the learning insert on the path that learns. The specification names the only
change allowed if a real deployment does not fit: move the learning insert off
the response path, because it is not needed to answer the current decision. It
has not been moved.

The rank change from leave-one-out to full conformal could not be told apart
from run-to-run variation end to end; `evaluate_profile` alone went from 1.61 to
1.70 ms p50.

### 19.9 Configuration

The layer is on only when `PROFILE_LAYER=1` **and** `DEEPCHECK_PROFILE_KEY` is
set **and** at least one merchant is configured. There is **no development
fallback for any of them**, `DEBUG=1` included: an unset key leaves the layer
off in every mode. `PROFILE_ESCALATION=1` is a second switch that lets a
computed deviation actually ask for verification; with it off the layer is in
shadow mode — it computes, stores and audits, and no customer is asked for
anything. That is how it is meant to be measured before it is allowed to act.

A malformed `DEEPCHECK_MERCHANT_KEYS` entry stops the boot, in every mode, and
the error names the entry's position rather than its text.

---

## 20. Saturation of real-browser features, and the re-capture plan

This is a measured defect in the evaluation data, written down because it
limits what §7's numbers can mean.

### 20.1 What was measured

`lab/real_telemetry.json` holds 234 flushes captured through a real Chromium by
`lab/capture.py` (2026-09-06, six scenarios, 47 runs, one machine). It stores
**normalised features**, under the scaling in force when it was captured. Every
count below is from that file:

| feature | at the ceiling (= 1.0) | at its neutral fallback | reading |
|---|---|---|---|
| `scroll_hizi_varyansi` | 0 | **234 / 234** | never measured once: no scenario scrolls |
| `ivme_degisimi` | **90 / 234** | 87 / 234 | at the ceiling in every `H1_human` and every `A3_human_mimic` row; at the fallback in `A1_naive`, `A2_randomized` and `H2_keyboard_only` |
| `duraklama_dagilimi` | **109 / 234** | 0 | 37 of 50 `H1_human`, 32 of 40 `A3_human_mimic`, 40 of 57 `A4_evasive` |
| `tiklama_oncesi_hareket` | 66 / 234 | 168 / 234 | measured in 66 rows and at the ceiling in **all 66** |
| `yon_tutarliligi` | 40 / 234 | 87 / 234 | 40 of the 147 rows where it was measured |
| `odak_degisimi` | 0 | — | 0.0 in all 234: the scripted runs never blur the tab (a real 0, not a fallback) |

The damaging one is `ivme_degisimi`: it reads exactly **1.0 in all 50
`H1_human` rows and in all 40 `A3_human_mimic` rows**. Between the lab's model
of a person and the lab's best mimic, that feature carries **one bit, and it is
the same bit**. `duraklama_dagilimi` saturates on 37 of 50 `H1_human`, 32 of 40
`A3_human_mimic` and 40 of 57 `A4_evasive` rows.

The cause is visible in the served bundle: the log-percentile endpoints for
`ivme_degisimi` are 10^−7.021 … 10^−5.532, i.e. up to 2.94 × 10⁻⁶, and they were
fitted on the **simulator's** distribution. Real Chromium pointer motion sits
above the top of that range.

### 20.2 What this costs

A feature pinned at a boundary contributes nothing to a split. So §7's held-out
table (0 % false flags, 76.1 % bot recall on browser runs) was achieved with
**one of the twelve features never measured at all** and **three more sitting
at the ceiling in a large share of the rows where they were measured**: 61 % of
`ivme_degisimi`'s 147 measured rows, 47 % of `duraklama_dagilimi`'s 234, and
100 % of `tiklama_oncesi_hareket`'s 66. The same saturation is why the profile
layer's browser-lab pass (§10 of `docs/profile-evaluation.md`) has to infer a
`measured_mask` rather than read one.

### 20.3 The pipeline that already exists

Two of the three pieces are in the code; the third is what has not been done:

1. `lab/capture.py` now records the **raw** channels the SDK posted, beside the
   features — added precisely so a rescaling can be replayed rather than
   re-captured.
2. `train_model.compute_feature_scaling(real_raw=…)` blends real raw values
   into the percentile pool, weighted so the real values of a feature carry the
   same total mass as its synthetic values. The blend can only **widen** the
   range: narrowing it would clip simulator rows that fit today, and a wider
   range costs a tree nothing, because splits depend on the ordering of values,
   which rescaling preserves and only clipping destroys.
3. The missing piece: **the existing 234 samples carry no `raw`.** They predate
   the change. `load_real_raw_for_scaling()` therefore returns an empty list
   today, so retraining on the current file changes nothing at all.

### 20.4 The plan, and its status

**Not done, and not done in this prototype.** The order is:

1. Re-run `lab/capture.py` so the file carries `raw` (a few minutes of browser
   time; the scenarios are unchanged), and add at least one scenario that
   scrolls, so `scroll_hizi_varyansi` is measured rather than defaulted.
2. Retrain. The percentile pool then includes real values, the endpoints widen,
   and the clipping goes.
3. Re-measure the held-out table in `docs/evaluation.md` and the browser-lab
   pass in `docs/profile-evaluation.md` §10 — the second one can then read a
   real `measured_mask` instead of inferring it.
4. Only then compare: a feature that stops saturating may move the numbers in
   either direction, and the point is to find out, not to improve them.

Until that is done, no figure in this guide should be read as "what these twelve
features can do". It is what eleven of them did, three of those degraded, on
one machine, against scripted behaviour.

### 20.5 Update, 2026-09-25: three of the four are repaired, by an unexpected route

The status above was written before the first real person was ever scored. That
recording (`data/real/human/`, 52 flushes, person p01) both caused a retrain and
gave the first data that is in the **shipped** coordinate system, because the
archive keeps raw events and re-extracts them. Counted on those 52 flushes:

| feature | at the ceiling, old bundle | at the ceiling, shipped bundle |
|---|---|---|
| `ivme_degisimi` | 48 / 52 | **3 / 52** |
| `scroll_hizi_varyansi` | 27 / 52 | **0 / 52** |
| `tereddut_skoru` | 9 / 52 | **4 / 52** |
| `duraklama_dagilimi` | 44 / 52 | **44 / 52** — unchanged |

**What moved the endpoints was step 2 of §20.3 doing nothing, and the simulator
doing everything.** The log-percentile endpoints are fitted to the training
distribution; giving the simulator a renderer frame clock (§7, and
`docs/evaluation.md` 2026-09-25) changed that distribution, and the endpoints
moved with it. The intended repair — blending real raw values into the
percentile pool — did **not** fire: 49 real raw flushes now reach the pool but
yield 46, 47 and 48 values per log-scaled feature against the
`MIN_SCALING_VALUES = 50` floor. The floor was **not** lowered by two to clear
it; it exists so one odd run cannot set the scale. A second recording crosses it.

`duraklama_dagilimi` is untouched because it is not log-percentile scaled at
all: it is a coefficient of variation over a hand-picked `DISPERSION_DIVISOR =
1.5`, out of reach of this mechanism. It is now the dominant residual ceiling,
and moving it into the fitted set changes how a feature is computed, so it
requires a `FEATURE_SCHEMA_VERSION` bump (§8).

Two gaps are unchanged and still open: on real traffic `kanal_gecis_gecikmesi`
was measurable in **2 of 52** flushes and `tiklama_oncesi_hareket` in 19 of 52,
so both are mostly the neutral fallback.

**The 234 lab rows of §20.1 can no longer be re-counted in the shipped
coordinate system at all.** They store normalised vectors and no raw, so a
retrain leaves them frozen: the table in §20.1 describes a bundle that is no
longer served. Re-capturing with `lab/capture.py`, with at least one scenario
that scrolls, is therefore a **prerequisite for quoting any lab number**, not an
improvement to one — step 1 of §20.4 stands, and steps 2–4 now have to be run
against a file that does not yet exist.

---

### 20.6 Update, 2026-09-25 (later): the lab WAS re-captured. One repaired, one unchanged, one still not measured

Step 1 of §20.4 was finally run: `lab/capture.py` drove the same six scenarios
again and wrote **252 flushes across 46 runs, every one carrying `raw`**. The
file no longer stores frozen vectors, so from here every lab row is re-extracted
under the scale in force and §20.1's "can no longer be re-counted" problem is
gone for good. Steps 2 and 3 followed: both served bundles were retrained and
`docs/evaluation.md`'s held-out table was re-measured.

Counted on the new file, under the shipped bundle:

| feature | at the ceiling | at its fallback | measured | vs §20.1 |
|---|---|---|---|---|
| `ivme_degisimi` | **0 / 252** | 89 / 252 | 163 / 252 | was 90 / 234 at the ceiling — **repaired** |
| `duraklama_dagilimi` | **119 / 252** | 0 | 252 / 252 | was 109 / 234 — **unchanged, as predicted** |
| `scroll_hizi_varyansi` | 0 | **252 / 252** | **0 / 252** | was 234 / 234 at fallback — **still never measured** |
| `tiklama_oncesi_hareket` | 74 / 252 | 178 / 252 | 74 / 252 | measured in 74 rows, at the ceiling in **all 74** — unchanged pattern |
| `yon_tutarliligi` | 44 / 252 | 89 / 252 | 163 / 252 | 44 of the 163 measured |
| `kanal_gecis_gecikmesi` | 1 / 252 | 171 / 252 | 81 / 252 | measurable in under a third of flushes |

What this run did and did not do:

* **`ivme_degisimi` is fixed.** Not by one mechanism but by two, and this time
  both fired: the frame-clocked simulator moved the endpoints (§20.5), and the
  real raw values finally entered the percentile pool. That pool now holds
  **209 values for `tereddut_skoru` and 156 for `ivme_degisimi`** against the
  `MIN_SCALING_VALUES = 50` floor — where the previous step had 46 to 48 and
  correctly refused to lower the floor by two. Both endpoints moved, which is
  what took `FEATURE_SCHEMA_VERSION` to 3 (§8).
* **`scroll_hizi_varyansi` is not fixed, and the reason is that half of step 1
  was skipped.** §20.4 asked for a re-capture *and* at least one scenario that
  scrolls. Only the re-capture was done, so no lab flush scrolls, the feature is
  measured 0 times in 252, and its scaling pool still holds 46 real values —
  below the floor. Its endpoints are still the simulator's alone. Adding a
  scrolling scenario to `lab/bot_lab.py` remains open and is now the cheapest
  outstanding item in this section.
* **`duraklama_dagilimi` is unchanged at 119 / 252**, exactly as §20.5 predicted:
  it is a coefficient of variation over a hand-picked `DISPERSION_DIVISOR = 1.5`
  and is out of reach of the percentile mechanism. It is now the only heavy
  residual ceiling, and moving it into the fitted set is a change to how a
  feature is *computed*, so it costs another schema bump.

What the repair bought, measured on **held-out lab runs** — runs the model never
trained on, session-level score, which is what `/api/decision` reads:

| | before (frozen vectors) | after (re-captured) |
|---|---|---|
| bot runs reaching block (≥80) | 11.1 % | **77.8 %** |
| bot runs reaching step-up (≥60) | 55.6 % | **100 %** |
| human runs flagged at either line | 0 % | 0 % |

n = 9 held-out bot runs, 4 human runs. The direction is the claim, not the
decimals. No threshold, weight or constant was changed to get it: 50 % of the
forest's fitted mass had been sitting in a coordinate system that no longer
existed, and putting it back is the whole intervention. Full write-up, including
what got worse, in `docs/evaluation.md`.

---

## 21. Glossary

- **Flush** — one 2 s SDK upload of the rolling 10 s window.
- **Feature** — one of the twelve numbers derived from a flush.
- **SHAP** — SHapley Additive exPlanations; per-feature contribution to a
  prediction.
- **Isolation Forest** — unsupervised model that scores how easily a point
  is isolated; easy isolation means anomalous. Trained and stored here but
  no longer part of the score (§7).
- **Median smoothing** — the session's official score is the median of the
  last five raw flush scores.
- **Neutral default** — the midpoint between human and bot training means,
  used when a feature cannot be computed.
- **`measured_mask`** — bitmask on each flush saying which features were
  genuinely measured rather than filled with a neutral default.
- **Contamination persona** — the 10 % of each class simulated to resemble
  the other class, so the model cannot cheat on easy separation.
- **Step-up** — asking for a second factor (SMS, 3-D Secure). The harshest
  thing the profile layer can cause, and the softest thing the ladder can.
- **Sequential rule (SPRT-shaped)** — evidence is accumulated across flushes
  and the decision stops when the sum crosses a bound, instead of counting
  flushes. The bounds are an operating point measured on synthetic data, not
  Wald's error rates, because the per-flush scores are uncalibrated vote
  shares from overlapping windows. Only flushes that **observed a generator**
  (at least one structural feature measured) are summed; with fewer than three
  of them the answer is `verify`.
- **Observed flush** — a flush in which at least one of the six structural
  features (`scorer.BUCKET_FEATURES`) was genuinely measured, read from
  `measured_mask`. The same test gates the smoothing's level-shift bypass.
- **Conformal guard** — refuses to *block* on a score that is unremarkable
  among held-out human sessions. One-directional. Inert as served (§15.24).
- **Profile / reference vector** — one session reduced to twelve normalised
  numbers and stored as part of a customer's own history (§19).
- **Probation vector** — a session learned only because a step-up rescued a
  profile challenge. Stored, but **not** a reference until promoted (§19.3).
- **Full conformal rank** — the p-value that decides a profile challenge:
  candidate and references scored by one function, each against the other n
  points, so the false-challenge rate is bounded by alpha in finite samples.
- **Modality** — `touch`, `mouse` or `keyboard`. A profile is compared only
  within one, with no pooled fallback.
- **Shadow mode** — `PROFILE_LAYER=1` with `PROFILE_ESCALATION=0`: the layer
  computes, stores and audits, and no customer is asked for anything.
