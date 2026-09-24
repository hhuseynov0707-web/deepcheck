# DeepCheck Adversarial Bot Lab

Drives a **real Chromium browser** against the **real SDK** and the **real
backend**. Every number produced here travels the same path as a production
session: browser input events → `sdk/deepcheck.js` → `POST /api/analyze` →
server-side `POST /api/demo/charge`, which runs the same decision code as
`POST /api/decision` and charges only on `allow` or `warn`.

## Why this exists

Accuracy on synthetic data is a self-assessment. The models are trained on
personas written by the same author as the detector, so separation on that
data is partly self-fulfilling. An attack scripted independently and executed
in a real browser is a genuinely held-out adversary.

That distinction was not theoretical. The first run of this lab found that the
identical evasive attack scored **88.9 (blocked) in the simulator** and
**36.5 (approved) through real Chromium** — because two of the heaviest timing
features are inverted between the two distributions:

| feature | real bot | synthetic bot | what the model had learned |
|---|---|---|---|
| `etkilesim_entropisi` | 0.225 | 0.836 | high = bot → real bot read as human |
| `duraklama_dagilimi` | 1.000 | 0.264 | high = human → real bot read as human |

`capture.py` is the response: it records labelled real-browser telemetry so the
detector can be trained and graded on the distribution it actually faces.

## Running it

```bash
# 1. Backend, with the harness origin allowed through CORS, Postgres up and a
#    trained model on disk. DEMO_ENDPOINTS=1 is required: /api/demo/charge
#    answers 404 without it.
cd backend
DATABASE_URL=postgresql+asyncpg://deepcheck:deepcheck@127.0.0.1:5432/deepcheck \
DEEPCHECK_SECRET=lab DASHBOARD_KEY=lab DEBUG=0 DEMO_ENDPOINTS=1 \
CORS_ORIGINS=http://127.0.0.1:3100 \
uvicorn main:app --port 8000

# 2. Attack ladder (in another shell, from the repo root)
pip install -r lab/requirements.txt
python -m playwright install chromium
python lab/bot_lab.py --api http://127.0.0.1:8000

# 3. Capture labelled telemetry for training/evaluation
python lab/capture.py --api http://127.0.0.1:8000 --repeats 8
```

Both tools serve the harness on **127.0.0.1:3100** — 3000 is taken by the demo
frontend container in this repository — so that is the origin `CORS_ORIGINS`
must allow. Nothing else may be listening there.

`capture.py` reads `GET /api/score/{id}` to check the features the server
stored against the ones it extracts locally from the same raw payload, so it
needs the backend's `DASHBOARD_KEY` (`--dashboard-key`, or the environment
variable). `--no-verify` skips that check and the `backend/` import with it.

## The ladder

| id | scenario | what it does |
|---|---|---|
| H1 | human | ballistic pointer motion, natural form-fill rhythm |
| H2 | keyboard_only | Tab navigation, no pointer at all — legitimate |
| A1 | naive | scripted focus + instant typing, no pointer |
| A2 | randomized | random delays, teleporting pointer jumps |
| A3 | human_mimic | linearly interpolated paths, varied typing |
| A4 | evasive | IID Gaussian jitter — the transcribed real evasion |
| A5 | adaptive | reads its own score back and retunes each round |

H1 and H2 are the ones that matter most. Blocking a legitimate keyboard-only
user is a worse outcome for a payment product than missing a bot, and a lab
that only measures detection would never show it.

## Reporting rule

For the adaptive attack the lab reports **per-round detection at round N**, not
a cumulative "detected by round N". A cumulative figure rises monotonically and
would still look strong on a run where the attacker was caught early and then
broke through at the end — which is exactly the outcome that matters.

## Honest scope

- The harness is a minimal payment form, not the React demo page. The SDK and
  the backend are what is under test; the React page adds styling on top of
  this identical path, so driving it would exercise Vite rather than the
  detector.
- The flush interval is left at the production 2000 ms. Shortening it to speed
  the lab up would change how much evidence each flush carries, and therefore
  the decisions — the lab would stop measuring what the product does.
- These are scripted attacks, not a determined human attacker with unlimited
  attempts. They establish a floor, not a ceiling.
- **H1 and H2 are scripts, not people.** Every kinematic property they have was
  chosen by whoever wrote the scenario, so a 0 % false-positive rate against
  them means "does not flag this lab's model of a user", never "does not flag
  customers".

## What the capture file currently cannot tell you

The dataset in `lab/real_telemetry.json` (234 flushes, 47 runs, captured
2026-09-06) has a measured blind spot, counted directly from the file:

| feature | state in those 234 rows |
|---|---|
| `scroll_hizi_varyansi` | **never measured once** — all 234 sit at its neutral fallback, because no scenario scrolls |
| `ivme_degisimi` | exactly 1.0 in **90** rows, including *every* `H1_human` and *every* `A3_human_mimic` row |
| `duraklama_dagilimi` | exactly 1.0 in **109** rows |
| `tiklama_oncesi_hareket` | measured in 66 rows, and at 1.0 in all 66 |
| `odak_degisimi` | 0.0 in all 234 — a real measurement, not a fallback: the scripted runs never blur the tab |

A feature pinned at a boundary cannot separate anything, so `ivme_degisimi`
carries the same single bit for the lab's model of a person and for its best
mimic. The cause is visible in the served bundle: the log-percentile endpoints
for that feature are 10^−7.021 … 10^−5.532, fitted on the **simulator's**
distribution, and real Chromium pointer motion sits above the top of the range.

The repair is half built and **not finished**:

1. `capture.py` now records the `raw` channels beside the features, so a
   rescaling can be replayed instead of re-captured.
2. `train_model.compute_feature_scaling(real_raw=…)` blends real raw values
   into the percentile pool, weighted so they carry the same total mass as the
   synthetic values. The blend can only **widen** the range.
3. The samples in the existing file **predate step 1 and carry no `raw`**, so
   `load_real_raw_for_scaling()` returns an empty list and retraining today
   changes the scale not at all.

Re-capturing (with at least one scenario that scrolls), retraining and
re-measuring is the next step and has not been done: `TECHNICAL_GUIDE.md` §20.
