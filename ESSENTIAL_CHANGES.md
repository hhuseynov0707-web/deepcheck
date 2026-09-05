# DeepCheck — Essential Changes Before Jury Evaluation

Ordered by how hard a technical fintech jury would push on each point.
Each item lists the files to touch and the condition that proves it is done.

---

## 1. Server-side enforcement with signed session tokens  (P0)

**Problem.** The API trusts any client-supplied `session_id`, and the
block / verify decision is made in `Demo.jsx`. A bot that never loads the
SDK, or posts fake telemetry under a victim's id, is never stopped.

**Change.**
- `backend/main.py`
  - `POST /api/session` → creates a Session row and returns
    `{session_id, token}` where `token = HMAC-SHA256(SECRET, session_id)`.
  - `POST /api/analyze` → require header `X-DeepCheck-Token`; reject 401 if
    the HMAC does not match `session_id`. Remove the "mint a uuid if missing"
    fallback.
  - `POST /api/decision` → body `{session_id}` + token; returns
    `{action: "allow" | "warn" | "verify" | "block", risk_score, label}`
    computed from the smoothed score with the 40/60/80 ladder. This is the
    only place the ladder is applied.
  - `SECRET` read from env `DEEPCHECK_SECRET`; refuse to start if unset
    outside `DEBUG=1`.
- `sdk/deepcheck.js`
  - On `init()`, if no `sessionId` given, call `POST /api/session` first and
    store both id and token. Send the token header on every flush.
  - Expose `DeepCheck.getToken()`.
- `frontend/src/pages/Demo.jsx`
  - On "Onayla", call `POST /api/decision` and act on `action`. Delete the
    local threshold comparisons at lines ~70-73. Keep the badge for display
    only.
- `backend/test_scorer.py` → add `test_api_rejects_bad_token`,
  `test_decision_blocks_bot_session`.

**Done when.** `curl -X POST /api/analyze` without a valid token returns 401,
and submitting the form with the SDK blocked results in `verify`, never
`allow`.

---

## 2. Give the LSTM real temporal input  (P0)

**Problem.** `build_sequence_from_features()` tiles one feature row ten
times; `train_model.build_sequences()` does the same plus noise. The LSTM
sees no time information yet carries 30 % of the ensemble weight.

**Change.**
- `backend/main.py` → before scoring, load the last 9 `BehaviorData`
  feature rows for the session and pass them to `scorer.compute_risk(raw,
  history=[...])`.
- `backend/scorer.py` → build the sequence as `history + [current]`,
  left-padded with the current row when fewer than 10 exist.
- `backend/train_model.py` → simulate each session as 10 consecutive
  1-second windows through `extract_features()` instead of one aggregate;
  train the LSTM on those real sequences. Keep RF/IsoForest on the final
  aggregate row.
- `backend/lstm_model.py` → keep `build_sequence_from_features` only as the
  padding helper; document that it is no longer the inference path.

**Done when.** A session whose features drift from human to robotic over
20 s scores higher on the LSTM than a session that is robotic from the
first flush, showing the model reacts to trajectory, not just level.

---

## 3. Evaluate on real recorded sessions  (P1)

**Problem.** Training and test both draw from `_simulate_session()`. The
reported accuracy measures fit to the simulator, not to people.

**Change.**
- `backend/record_session.py` → small CLI that tails Postgres for a labelled
  session id and writes its flushes to `data/real/{label}/{id}.json`.
- Record ≥ 30 human sessions (teammates, friends, different mice and
  laptops) and ≥ 30 bot sessions (Playwright scripts with and without
  jitter).
- `backend/evaluate.py` → loads `data/real/`, runs `compute_risk`, prints
  accuracy, false-positive rate and ROC-AUC. Commit the printed report to
  `docs/evaluation.md`.

**Done when.** `docs/evaluation.md` shows the numbers, and the README cites
them instead of synthetic accuracy.

---

## 4. Compute neutral defaults at training time  (P1)

**Problem.** `NEUTRAL_DEFAULTS` in `scorer.py` is a hand-maintained constant
that must be recomputed whenever personas change; it has drifted once.

**Change.**
- `backend/train_model.py` → compute the human/bot midpoint per feature from
  the generated dataset and store it in the pickle under
  `bundle["neutral_defaults"]`.
- `backend/scorer.py` → read `NEUTRAL_DEFAULTS` from the bundle; keep the
  constant only as a fallback for old pickles, with a startup warning.

**Done when.** Changing a persona mean in `train_model.py` and retraining
changes the neutral value without any edit to `scorer.py`.

---

## 5. Protect the SOC dashboard endpoints  (P1)

**Problem.** `GET /api/sessions` and `GET /api/score/{id}` are public. A
jury member will ask whether anyone can watch every customer's session.

**Change.**
- `backend/main.py` → require header `X-Dashboard-Key` equal to env
  `DASHBOARD_KEY` on both endpoints.
- `frontend/src/pages/Dashboard.jsx` → read the key from
  `VITE_DASHBOARD_KEY` and send it. Show a Turkish "Yetkisiz erişim" message
  on 401.
- `docker-compose.yml` → set both env vars from a `.env` file; add
  `.env.example`.

**Done when.** Opening `/api/sessions` in a plain browser tab returns 401.

---

## 6. Remove the dead AWS deployment path  (P2)

Delete `backend/lambda_handler.py`, `backend/template.yaml`, and the
`mangum` line in `backend/requirements.txt`. Docker Compose is the only
deployment story.

---

## 7. README integration guide  (P2)

Add a section "Entegrasyon" showing the three steps a bank takes:

1. `<script src="https://<host>/deepcheck.js"></script>`
2. `DeepCheck.init({ apiUrl: "https://<host>" })`
3. On checkout, `POST /api/decision` with the session id and token, and act
   on `action`.

Include the response JSON and the four actions with their Turkish labels.

---

## 8. Housekeeping  (P2)

- Delete the untracked `AGENTS.md` (byte-for-byte copy of `CLAUDE.md`).
- `backend/entrypoint.sh` → print a Turkish notice with the expected
  training time on first start, so a cold container is not mistaken for a
  hang.
- Warm the model once at startup (`scorer.get_bundle()` already runs; also
  call `compute_risk` on a dummy payload) so the first real flush is not
  the slow one.

---

## Order of work

1 → 2 → 3 run in that sequence because 3 needs the real inference path
from 1 and 2 to be measured honestly. 4, 5, 6, 7, 8 are independent and
can be done in any gaps.
