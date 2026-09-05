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

DeepCheck turns those differences into six numbers, feeds them to a small
ensemble of ML models, and returns a **risk score from 0 to 100** every two
seconds while the user is on the page. The score comes with a Turkish label
and a SHAP explanation of which behaviours drove it.

**Risk score = 100 × P(fraud | behaviour)**

---

## 2. System overview

```
 Browser (customer)                     Backend (FastAPI, Python 3.11)          SOC Dashboard (React)
 +----------------------+   POST every 2 s   +--------------------------+   GET every 3 s   +------------------+
 | payment page         | -----------------> | /api/analyze             | <---------------- | /api/sessions    |
 | + sdk/deepcheck.js   |  raw telemetry     |  validate -> features -> |                   | /api/score/{id}  |
 |   (mouse, click,     | <----------------- |  RF + IsoForest + LSTM ->|                   |  table, D3 chart,|
 |    scroll, keydown,  |  score + label +   |  SHAP -> smooth -> store |                   |  SHAP bars       |
 |    focus timestamps) |  SHAP              +------------+-------------+                   +------------------+
 +----------------------+                                 |
                                                          v
                                              PostgreSQL 16 (sessions, behavior_data)
```

Everything runs from one `docker-compose up --build`: three containers,
database, backend, frontend. The backend trains the model on first start if
no model file exists.

---

## 3. Repository map

| Path | Role |
|---|---|
| `sdk/deepcheck.js` | Browser SDK. Collects behaviour, posts it, emits results. ~270 lines, no dependencies. |
| `backend/main.py` | FastAPI app: request validation, four endpoints, smoothing, persistence. |
| `backend/scorer.py` | Feature extraction, model loading, inference, SHAP, labelling. |
| `backend/lstm_model.py` | PyTorch LSTM definition and the canonical `FEATURE_NAMES` list. |
| `backend/train_model.py` | Synthetic data generator (four personas) and training of all three models. |
| `backend/models.py` | SQLAlchemy tables `sessions` and `behavior_data`. |
| `backend/database.py` | Async engine, session factory, `init_db`. |
| `backend/test_scorer.py` | Seven end-to-end scenario tests. |
| `backend/entrypoint.sh` | Trains if needed, then starts uvicorn with 4 workers. |
| `frontend/src/pages/Demo.jsx` | Turkish payment form with the SDK embedded and a live risk badge. |
| `frontend/src/pages/Dashboard.jsx` | SOC view: session table, D3 risk history, SHAP bars. |
| `frontend/src/components/*` | RiskBadge, SessionTable, RiskChart (D3), VerificationModal, MetricCard. |
| `docker-compose.yml` | Postgres + backend + frontend. |
| `docs/index.html` | Landing page served by GitHub Pages. |

---

## 4. The browser SDK in detail

`sdk/deepcheck.js` is an IIFE that exposes `window.DeepCheck = { init, stop,
getSessionId }`.

### 4.1 What it listens to

| Event | Stored as | Why |
|---|---|---|
| `mousemove` | `{x, y, t}` | Trajectory shape and acceleration |
| `click` | `{x, y, t}` | Click density and timing regularity |
| `scroll` | `{scrollY, t}` | Scroll speed variance |
| `keydown` | `{t}` only | Typing rhythm. **The key itself is never read.** |
| `visibilitychange` (hidden) | `t` | Focus loss count |

All listeners are `passive`, so they never delay the page.

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
6. On success, `onUpdate(result)` and a `deepcheck:update` DOM event.

### 4.4 Session identity

`init()` calls `POST /api/session`, and the server returns the id together
with an HMAC-SHA256 token over it. The id is **not** generated in the
browser: a client that could name its own id could post telemetry under
another customer's session, and a bot could present an id at checkout that
it had never sent behavior for. Listeners attach before the request goes
out, so the first two seconds of behavior are buffered rather than lost;
flushes are dropped until the token arrives, and the rolling buffers mean
the next flush re-sends that window.

`DeepCheck.getSessionId()`, `DeepCheck.getToken()` and `DeepCheck.ready()`
expose what a host page needs to call `/api/decision` at checkout.

---

## 5. The API

All six endpoints are in `backend/main.py`.

### `POST /api/session`

Mints `{session_id, token}` where `token = HMAC-SHA256(DEEPCHECK_SECRET,
session_id)`. Takes no input, deliberately: the id is never accepted from
the caller. It writes no database row either, so a page that is opened and
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
   a `behavior_data` row inserted with the raw telemetry, the six features
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

Errors: 503 if the model is not loaded, 500 with a generic Turkish message
on any other failure (tracebacks are logged, never returned).

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
problem, which the LSTM history and the real-session evaluation address.

### `POST /api/decision`

The enforcement point, and the only place the 40 / 60 / 80 ladder is
applied. Takes `{session_id}` plus the token, returns
`{action, risk_score, label, message, reason}` where action is `allow`,
`warn`, `verify` or `block`.

It fails closed, and it needs evidence. In order:

| Condition | Result | `reason` |
|---|---|---|
| No session row | `verify` | `unknown_session` |
| Fewer than 3 analysed flushes (6 s of behaviour) | `verify` | `insufficient_evidence` |
| Last flush older than 30 s | `verify` | `stale` |
| Score puts it in the `verify` band but a step-up was recorded within 5 min | `allow` | `verified` |
| Otherwise, the ladder | as scored | `score` |

One plausible two-second window is cheap to fabricate; six seconds of
sustained behaviour is not. The freshness rule stops a token lifted from a
shared machine being cashed in later on the real customer's earlier
browsing. A recorded verification only ever upgrades `verify`; a `block`
cannot be verified past.

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

### `GET /api/score/{session_id}`

Session summary plus the last 200 `behavior_data` rows in chronological
order, each with timestamp, raw score and the six features. Feeds the
dashboard chart. Requires `X-Dashboard-Key`.

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

`scorer.extract_features(raw)` turns the payload into six floats, each
normalised to roughly 0 – 1. Canonical names and order live in
`lstm_model.FEATURE_NAMES`, shared by scorer, trainer and SHAP labels so
they can never drift apart.

| # | Feature | Computation | Human tends to | Bot tends to |
|---|---|---|---|---|
| 1 | `scroll_hizi_varyansi` | variance of scroll speed over consecutive scroll events, / 5 | high | 0 or tiny |
| 2 | `tereddut_skoru` | mean hesitation gap / 1500 ms | 0.3 – 0.8 | about 0 |
| 3 | `etkilesim_entropisi` | Shannon entropy of inter-event gaps, **per channel**, then weighted average | 0.7 – 1.0 | about 0 |
| 4 | `ivme_degisimi` | variance of mouse *acceleration* (change of speed over time), / 2.2e-6 | about 0.45 | about 0 |
| 5 | `tiklama_yogunlugu` | clicks in the last 5 s / 10 | low | high |
| 6 | `odak_degisimi` | focus-loss count / 5 | sometimes > 0 | 0 |

Design decisions worth knowing:

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

Three models are trained by `train_model.py` and loaded once per worker by
`scorer.ModelBundle`.

| Model | Library | Config | Role | Weight |
|---|---|---|---|---|
| Random Forest | scikit-learn | 200 trees, depth 12, min leaf 5 | Supervised classifier, main signal | 0.5 |
| Isolation Forest | scikit-learn | 200 trees, contamination 0.05 | Unsupervised anomaly score, catches behaviour unlike *anything* seen | 0.2 |
| LSTM | PyTorch | 2 layers, hidden 32, dropout 0.2, Adam 1e-3, 8 epochs | Sequence model over 10 timesteps x 6 features | 0.3 |

Inference:

```
scaled     = StandardScaler(features)
rf_p       = RF.predict_proba(scaled)[fraud]
iso_a      = clip(0.5 - IsoForest.decision_function(scaled), 0, 1)   # higher = more anomalous
lstm_p     = LSTM(sequence)
P(fraud)   = clip(0.5*rf_p + 0.2*iso_a + 0.3*lstm_p, 0, 1)
score      = round(100 * P(fraud), 1)
confidence = max(P, 1 - P)
```

**Explanation.** `shap.TreeExplainer(rf)` gives a per-feature contribution
for the fraud class. The three largest absolute contributions are returned
as `impact` (x100). Only the Random Forest is explained; that is the model
the jury can inspect, and SHAP on trees is exact and fast.

Performance settings baked into loading: `n_jobs=1` on both forests
(per-call thread-pool setup cost about 3x the actual work for a single row)
and `torch.set_num_threads(1)`. Measured `compute_risk` about 42 ms mean on
a laptop; the `response_time_ms` field reports it on every call.

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
`model.pkl` is absent; seed 42, fully reproducible).

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
4. Train RF and IsoForest on the aggregate rows; train the LSTM on
   sequences built by repeating each row 10 times with N(0, 0.02) jitter.
5. Save `model.pkl` (scaler, rf, iso_forest, feature_names) and
   `lstm_model.pt`. Both are git-ignored and regenerated on demand.

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
4. FastAPI validates the payload, extracts six features (scroll and click
   density fall back to neutral / zero because none happened yet), scores
   about 18, SHAP says entropy and acceleration drove it, and stores two
   rows.
5. The badge on the page turns green: "Gerçek Kullanıcı 18".
6. Every 2 s the window slides forward; the median of the last five raw
   scores is the badge value.
7. Meanwhile the dashboard polls `/api/sessions` every 3 s and lists the
   session in green. Clicking it polls `/api/score/{id}` and draws the raw
   per-flush history with D3 and the top-3 SHAP bars.
8. If a Playwright script drives the same page, mouse points arrive at a
   fixed 80 ms cadence in a straight line, hesitation is empty, entropy
   near 0, acceleration variance near 0. Score climbs past 80 within three
   flushes, the badge turns red, and the submit button is disabled.

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
shap_explanation JSON                 six feature columns         FLOAT
response_time_ms FLOAT                risk_score     FLOAT 0..100 (check, raw per-flush)
                                      idx (session_id, created_at)
```

`sessions.risk_score` is the *smoothed* verdict; `behavior_data.risk_score`
is the raw per-flush signal kept for analysis and charting.

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
- **Dashboard.jsx** — dark SOC theme. Session table coloured by label,
  D3 line chart of the selected session's raw history, horizontal SHAP bars
  with Turkish feature names, metric cards, 3 s refresh.
- `VITE_API_URL` selects the backend; defaults to `http://localhost:8000`.
  `VITE_DASHBOARD_KEY` must match the backend's `DASHBOARD_KEY` or the SOC
  dashboard shows "Yetkisiz erişim".

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
- **Privacy:** the SDK never reads key values, field contents, or the DOM.
  Only coordinates and timestamps leave the browser. No PII is stored.
- Model artefacts are reproducible from a fixed seed and kept out of git.

---

## 13. Tests

`backend/test_scorer.py` runs nineteen tests. Seven scoring scenarios go
through the real `compute_risk`:

- natural human scores low
- sparse typing-only human scores low
- headless bot scores high
- scripted-motion bot scores high
- bot with one incidental pause still scores high
- human with a fast burst still scores low
- fast keyboard-only with no mouse scores high

One checks the sequence model actually reads history: the same current flush
must score differently after a robotic history than after a calm one.

Eleven cover the API's authorization and enforcement:

- `/api/analyze` rejects a missing, wrong, or other-session token
- `/api/analyze` rejects telemetry far from the server clock, in either direction
- `/api/analyze` rejects a flush whose time runs backwards within its session
- `/api/analyze` rejects a recording replayed under a new token with its clock shifted
- `/api/decision` returns block / verify / warn / allow across the ladder
- `/api/decision` returns verify for a session with no telemetry at all
- `/api/decision` returns verify until three flushes have been analysed
- `/api/decision` returns verify when the last flush is older than 30 s
- `/api/demo/charge` never charges a blocked, unverified, thin or unknown session
- `/api/demo/verify` rejects a wrong code, upgrades verify to allow, and cannot lift a block
- the SOC endpoints reject a missing or wrong dashboard key

Those run against a stub database rather than Postgres, deliberately: an
authorization check that needs infrastructure to test is an authorization
check that stops being tested.

```bash
cd backend && python -m pytest test_scorer.py -q
```

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

Environment variables: `DATABASE_URL`, `CORS_ORIGINS`, `UVICORN_WORKERS`
(default 4), `VITE_API_URL`, `VITE_DASHBOARD_KEY`, and the two secrets —
`DEEPCHECK_SECRET` (signs session tokens) and `DASHBOARD_KEY` (guards the
SOC endpoints). See `.env.example`. With `DEBUG=1` the backend falls back to
fixed development values and warns on every boot; with `DEBUG=0` it refuses
to start without both, because a missing secret must never quietly mean
"authentication off".

---

## 15. Known limitations and planned work

These are real and the team knows them. Details and acceptance criteria
are in `ESSENTIAL_CHANGES.md`.

1. **Evaluation is still synthetic.** Accuracy is measured on data from the
   same simulator that produced the training set, so it describes fit to the
   simulator and not performance against people. The measurement pipeline
   exists — `record_session.py`, `evaluate.py`, `tools/bot_session.py` — and
   what is missing is the recordings. See `docs/evaluation.md`. This is the
   most important open item.
2. **A bot that reproduces human timing distributions can evade the
   current features.** The sequence model shows this directly: it separates
   naive automation cleanly (p ≈ 0.94) but scores the human-mimicking
   persona at only p ≈ 0.25. Planned mitigations: kinematic plausibility
   checks, `event.isTrusted`, pointer provenance, and the real-data
   evaluation above to measure them.
3. **No rate limiting.** `POST /api/session` will mint tokens as fast as it
   is asked to.
4. **No schema migration tool.** `create_all()` creates tables but does not
   alter existing ones. As a stop-gap, `init_db` applies a short list of
   additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements at boot so
   existing volumes keep working; Alembic is the proper fix.
5. **No key rotation.** Changing `DEEPCHECK_SECRET` invalidates every live
   session token at once.

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
convincingly across six independent signals at once. It complements, not
replaces, the other layers.

**Why these six features?**
Each captures a different physical or cognitive property: scroll variance
and acceleration variance are motor-control signals, hesitation and
entropy are timing signals, click density is intent, focus change is
attention. They are cheap to compute and each is explainable to a fraud
analyst.

**Why three models instead of one?**
The Random Forest is the accurate, explainable core. The Isolation Forest
flags behaviour unlike anything in training, which matters for attacks
the simulator never imagined. The LSTM reads the session as a trajectory
rather than a snapshot, which is what catches behavior that changes
mid-session. The weights 0.5 / 0.2 / 0.3 reflect current trust in each.

**Is the LSTM actually doing anything today?**
It reads the session's real flush history now, and a regression test
asserts it: the same current flush scores differently depending on what
preceded it. It separates naive automation strongly (p ≈ 0.94 against
p ≈ 0.18 for humans). It is weak where the features themselves are weak —
the human-mimicking persona scores only p ≈ 0.25 — which is a limit of the
six signals rather than of the architecture.

**How do you know it works on real people?**
We do not, yet, and `docs/evaluation.md` says so rather than quoting a
number. Today we know it works on twelve regression scenarios and on the
synthetic distribution. The recording and evaluation tooling is built and
waiting on data. We would rather show a smaller real number than a perfect
synthetic one.

**What is the false positive rate? What happens to a real customer who is
flagged?**
Not yet measured on real data. By design a flagged customer is never
silently rejected: 60 – 80 triggers a verification step, only 80 and above
blocks, and median smoothing means a single odd reading cannot trigger
either.

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
for it. 42 ms mean on a laptop includes SHAP. Without SHAP it is under
20 ms. The score is ready long before a human can finish typing a card
number.

**What data do you collect? Is it GDPR / KVKK safe?**
Coordinates, timestamps, scroll offsets and a focus-loss timestamp. No
key values, no field contents, no DOM, no identifiers beyond a random
session UUID. There is nothing to link to a person.

**How does a bank integrate this?**
One script tag, one `DeepCheck.init` call, and a server-side check of the
session's decision before authorising the transaction. The backend is a
container they run inside their own network; no data leaves their
perimeter.

**How does it scale?**
One backend worker handles about 20 sessions flushing every 2 s. Four
workers per container, and containers are stateless, so horizontal scaling
is a matter of running more of them behind a load balancer. Postgres is
the only shared state.

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

## 17. Glossary

- **Flush** — one 2 s SDK upload of the rolling 10 s window.
- **Feature** — one of the six numbers derived from a flush.
- **SHAP** — SHapley Additive exPlanations; per-feature contribution to a
  prediction.
- **Isolation Forest** — unsupervised model that scores how easily a point
  is isolated; easy isolation means anomalous.
- **Median smoothing** — the session's official score is the median of the
  last five raw flush scores.
- **Neutral default** — the midpoint between human and bot training means,
  used when a feature cannot be computed.
- **Contamination persona** — the 10 % of each class simulated to resemble
  the other class, so the model cannot cheat on easy separation.
