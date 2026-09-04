# DeepCheck Adversarial Bot Lab

Drives a **real Chromium browser** against the **real SDK** and the **real
backend**. Every number produced here travels the same path as a production
session: browser input events → `sdk/deepcheck.js` → `POST /api/analyze` →
server-side `POST /api/transaction`.

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
# 1. Backend, with the harness origin allowed through CORS
cd backend
DEEPCHECK_SECRET=lab DEEPCHECK_OPERATOR_KEY=lab \
DEEPCHECK_ALLOWED_ORIGINS=http://127.0.0.1:3000 \
uvicorn main:app --port 8000

# 2. Attack ladder (in another shell, from the repo root)
pip install -r lab/requirements.txt
python lab/bot_lab.py --api http://127.0.0.1:8000

# 3. Capture labelled telemetry for training/evaluation
python lab/capture.py --api http://127.0.0.1:8000 --repeats 10
```

The harness is served on port 3000 so it matches the backend's default CORS
allowlist. Nothing else may be listening there.

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
