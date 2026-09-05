# DeepCheck — Essential Changes (revision 2)

Ordered by how hard a technical fintech jury would push on each point.
Each item names the files to touch and the condition that proves it is done.

## What the previous revision asked for, and its status

All eight items of revision 1 landed in commit `a9918ea`:
server-minted HMAC session tokens, `POST /api/decision` as the only
enforcement point, real flush history into the LSTM, neutral defaults
computed at training time, dashboard key header, Lambda files removed,
README integration guide, model warm-up at boot. Twelve tests cover them.

This revision is a fresh audit of the code as it stands now. The findings
below are new.

**Status of this revision.** Items 2, 3 and 4 are implemented (replay
protection, minimum evidence and freshness, server-side charge), and item 9
came along with 4 because a server-side charge needs a server-side step-up:
`POST /api/demo/verify` records verification on the session and only ever
upgrades `verify` to `allow`. Seven tests cover the four items. Item 11 got
a stop-gap: `init_db` now applies additive `ALTER TABLE ... IF NOT EXISTS`
statements at boot, so existing volumes keep working. Items 1, 5, 6, 7, 8,
10, 12 and 13 remain open.

---

## 1. Record the real evaluation set and publish the numbers  (P0, not code)

**Weakness.** `docs/evaluation.md` correctly says there is no measurement
against real people. The pipeline exists (`record_session.py`,
`tools/bot_session.py`, `evaluate.py`) but `data/real/` is empty. "How well
does it work?" is the first jury question and today the answer is
"unmeasured".

**Change.** Follow `data/real/README.md` exactly: 30+ human sessions across
different people and input devices, 15 naive and 15 jitter bot runs, then
`python evaluate.py --markdown ../docs/evaluation.md`. Put the headline
numbers, with sample counts, in the README.

**Done when.** `docs/evaluation.md` contains accuracy, false-positive rate,
recall and ROC-AUC with n per class, and the README cites them.

---

## 2. Replay protection on `/api/analyze`  (P0) — DONE

**Weakness.** Timestamps are only checked to lie in `[0, year 2100]`. An
attacker records one genuine human session's flushes once, mints a fresh
session token, and replays those exact payloads before every fraudulent
checkout. The score will be human, the token will be valid, and
`/api/decision` will say `allow`. This defeats the whole system with a
20-line script and needs no ML evasion at all.

**Change.**
- `backend/main.py`
  - Reject a flush whose newest event timestamp differs from server receipt
    time by more than `MAX_CLOCK_SKEW_MS` (suggest 15 000). Client clocks
    drift, so log the skew distribution for a week before tightening.
  - Reject a flush whose newest event timestamp is older than the previous
    flush's newest timestamp for the same session (time must move forward).
  - Store `sha256(payload)` per flush on `BehaviorData`; reject an exact
    duplicate within the same session.
  - All three rejections return 422 with a Turkish message and are logged.
- `backend/models.py` → add `payload_hash` and `newest_event_at` columns.
- `sdk/deepcheck.js` → no change; real clients already satisfy this.
- `backend/test_scorer.py` → `test_analyze_rejects_stale_timestamps`,
  `test_analyze_rejects_replayed_payload`.

**Done when.** Replaying a recorded human session under a new token yields
422 on the first flush, and `/api/decision` for that session returns
`verify`.

---

## 3. Minimum evidence and freshness before a decision  (P0) — DONE

**Weakness.** `/api/decision` trusts the score after a single flush. One
2-second window of plausible telemetry is enough for `allow`. There is also
no freshness check: a session last seen an hour ago still yields its old
verdict, so a token stolen from a shared machine or via XSS on the merchant
page can be cashed in later.

**Change.**
- `backend/main.py` `/api/decision`
  - Require at least `MIN_FLUSHES_FOR_DECISION` analyzed flushes (suggest
    3, which is 6 seconds of behaviour) or return `verify`.
  - Require `last_seen_at` within `DECISION_MAX_AGE_S` (suggest 30) or
    return `verify`.
  - Both constants documented next to `ACTION_LADDER`.
- `frontend/src/pages/Demo.jsx` → if the verify reason is "not enough
  behaviour yet", show a Turkish hint rather than the OTP modal.
- Tests: `test_decision_verifies_with_too_few_flushes`,
  `test_decision_verifies_when_stale`.

**Done when.** Submitting the form within 4 seconds of page load returns
`verify`; waiting 8 seconds of normal use returns `allow`.

---

## 4. Move the charge itself behind the server in the demo  (P0) — DONE

**Weakness.** `Demo.jsx` calls `/api/decision` and then decides in the
browser whether to run `processPayment()`. The ladder moved to the server,
but the *act* of paying is still gated by browser code an attacker can
edit. A jury member opening DevTools will see it.

**Change.**
- `backend/main.py` → add `POST /api/demo/charge` taking
  `{session_id, amount}` plus the session token. It calls the same decision
  logic internally and only when `action` is `allow` or `warn` returns
  `{status: "charged", ...}`; otherwise it returns the decision unchanged
  and no charge happens. This is the pattern a merchant backend follows.
- `frontend/src/pages/Demo.jsx` → replace the decision call with the charge
  call. The page only renders what the server returned.
- README integration section → show the merchant-server-side call.

**Done when.** Deleting every `if` in `Demo.jsx` still cannot produce a
"charged" response for a blocked session.

---

## 5. The dashboard key is public  (P1)

**Weakness.** `VITE_DASHBOARD_KEY` is compiled into the frontend bundle and
served to anyone who opens `/dashboard`. The header check on
`/api/sessions` is therefore defeated by view-source. It reads as
authentication but is not.

**Change.**
- `frontend/src/pages/Dashboard.jsx` → remove the env key. Show a Turkish
  login field ("Pano erişim anahtarı"); keep the entered key in
  `sessionStorage` only; on 401 clear it and show the field again.
- `docker-compose.yml` → drop `VITE_DASHBOARD_KEY`.
- Rotate the dev fallback key so old bundles stop working.

**Done when.** The built frontend bundle contains no dashboard key string.

---

## 6. A missing LSTM file silently runs random weights  (P1)

**Weakness.** `scorer.ModelBundle` loads `lstm_model.pt` only if it exists.
If `model.pkl` is present but the LSTM file is not, the LSTM runs with
random initialisation and contributes 30 percent noise to every score with
no error anywhere. `entrypoint.sh` only checks for `model.pkl`.

**Change.**
- `backend/scorer.py` → raise `FileNotFoundError` with a Turkish message
  if `lstm_model.pt` is missing, same as for `model.pkl`.
- `backend/entrypoint.sh` → train if *either* file is missing.
- Test: `test_bundle_requires_lstm_weights`.

**Done when.** Deleting `lstm_model.pt` and starting the API makes
`/api/health` report `model yüklenmedi`.

---

## 7. No rate limiting anywhere  (P1)

**Weakness.** `/api/session` and `/api/analyze` accept unlimited calls.
Each analyze costs about 50 ms of CPU, so one client can saturate all four
workers, and unlimited session minting fills the database. A fintech jury
will ask about abuse resistance.

**Change.**
- Add `slowapi` to `requirements.txt`.
- `backend/main.py` → per-IP limits: `/api/session` 10 per minute,
  `/api/analyze` 60 per minute per session id, `/api/decision` 10 per
  minute. Return 429 with a Turkish message.
- Document the limits in the README integration section.

**Done when.** A loop of 100 session mints from one IP gets 429 after the
tenth.

---

## 8. Unbounded storage of raw telemetry  (P1)

**Weakness.** Every flush stores the full raw telemetry JSON, up to 2000
mouse points, and nothing is ever deleted. A day at a stand produces
gigabytes, and raw behaviour recordings of real people are kept forever,
which is a privacy question as much as a disk question.

**Change.**
- `backend/main.py` → a background task on startup that every 10 minutes
  deletes `behavior_data` rows older than `RETENTION_HOURS` (suggest 24)
  and sessions with no rows left.
- Optionally null out the raw JSON columns after 1 hour while keeping the
  six features and score, since only `record_session.py` needs the raw data
  and it runs promptly.
- Document retention in the README privacy section.

**Done when.** Rows older than the retention window disappear without a
restart.

---

## 9. The verification step accepts any six digits  (P1) — DONE with item 4

**Weakness.** `VerificationModal.jsx` succeeds on any 6-character code after
a timer. A jury member who gets `verify`, types `000000`, and is let through
will conclude step-up is a bypass, not a control.

**Change.**
- `frontend/src/components/VerificationModal.jsx` → accept only a fixed
  demo code and print it in the modal: "Demo doğrulama kodu: 482913. Gerçek
  entegrasyonda SMS/OTP sağlayıcınız kullanılır." Wrong codes show a Turkish
  error.
- Better: `POST /api/demo/verify` on the server that records the
  verification on the session so `/api/demo/charge` can see it.

**Done when.** An incorrect code is rejected and the correct one is visible
as clearly labelled demo behaviour.

---

## 10. Continuous integration  (P2)

**Weakness.** No `.github/workflows`. Twelve tests exist but nothing runs
them on push, and a jury reading the repo sees no green check.

**Change.** `.github/workflows/ci.yml` running `pip install -r
backend/requirements.txt`, `python train_model.py` with a reduced
`N_SESSIONS` env override for speed, and `pytest backend`. The API tests
already override `get_db`, so no Postgres service is needed.

**Done when.** The README shows a passing badge.

---

## 11. Schema changes on an existing volume  (P2) — stop-gap in place

**Weakness.** `init_db` uses `create_all`, which never alters existing
tables. Item 2 adds columns; anyone with an old volume gets a 500 on the
first flush.

**Change.** Either add Alembic with one initial migration, or document
`docker-compose down -v` as the upgrade step in the README and have
`init_db` log the schema version it expects.

**Done when.** Starting the new code against an old volume either migrates
or fails at boot with a clear Turkish message, never at request time.

---

## 12. Production frontend build  (P2)

**Weakness.** Compose runs the Vite dev server as the "product" frontend.
It works, but a jury may ask why a fintech product ships a dev server.

**Change.** Multi-stage `frontend/Dockerfile`: `npm run build`, then serve
`dist/` and `sdk/deepcheck.js` with nginx. Keep the dev command available
under a `docker-compose.dev.yml` override.

**Done when.** `docker-compose up` serves static files and the SDK from
nginx on port 3000.

---

## 13. Cheap forge-resistance signals  (P2, after item 1)

**Weakness.** All six features are timing and geometry, which a
sufficiently careful script can imitate.

**Change.** In `sdk/deepcheck.js`, record per flush the count of events with
`isTrusted === false`, the `pointerType` distribution, and whether
`navigator.webdriver` is set. Add them as features only after item 1 gives
a baseline to measure their effect against. Note for the jury: Playwright
and Puppeteer produce trusted events through the browser protocol, so
`isTrusted` catches JavaScript-synthesised events, not driven browsers.
`navigator.webdriver` is the reverse.

**Done when.** `evaluate.py` shows the jitter-bot recall improving with the
new features, or they are dropped with that result recorded.

---

## Order of work

Items 2, 3 and 4 close the remaining ways past the server-side gate and
should go first, together, in one commit. Item 1 is data collection and
runs in parallel from day one. Items 5 to 9 are each an afternoon. Items
10 to 13 are polish and can be done in any order.
