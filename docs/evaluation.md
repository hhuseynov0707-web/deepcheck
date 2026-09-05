# Evaluation

Measured 2026-09-06 against the running stack. Every number here comes from
telemetry that travelled the real path: browser input events → `sdk/deepcheck.js`
→ `POST /api/analyze` → the same scoring code that serves the demo.

## What changed

Until now this page said, correctly, that no accuracy figure existed against
anything but the simulator. That is no longer true. `lab/capture.py` drives a
real Chromium browser through the real SDK and records labelled telemetry;
234 samples were captured across six scenarios, and the models are now trained
on that data blended with the synthetic set.

## Held-out real browser runs

Runs are held out whole, not flushes. Every flush from one browser session is
correlated with its siblings, so a per-flush split would put the same session
on both sides and report a number that will not reproduce.

| Scenario | n | mean p(bot) | flagged |
|---|---|---|---|
| H1_human (legitimate) | 25 | 0.043 | **0%** |
| H2_keyboard_only (legitimate) | 11 | 0.088 | **0%** |
| A2_randomized | 8 | 0.671 | 75% |
| A3_human_mimic | 10 | 0.927 | 100% |
| A4_evasive | 28 | 0.599 | 68% |

**False positives 0%, bot recall 76.1%.**

H2 matters as much as the attack rows. Blocking a keyboard-only user is worse
for a payment product than missing a bot, and a lab that only measured
detection would never surface it.

## The number that is not in that table

The scenarios above all appear in training, held out by run. So the table
answers "does it recognise more sessions of an attack it has samples of?"
and the answer is yes. It does not answer "does it recognise an attack it has
never seen", which is the question that decides whether this works in
production.

For that, a separate adversarial harness was written from motor-control first
principles — minimum-jerk trajectories, Fitts's law, log-normal keystroke
intervals — with no reference to either the simulator's personas or the lab's
scenarios. 125 sessions, 25 per class:

| Persona | Mean score | Stopped |
|---|---|---|
| human | 11.3 | 0% false positives |
| naive headless script | 34.3 | 100%, by the evidence rule |
| straight-line automation | 81.6 | **92% blocked** |
| independent humanised bot | 11.3 | **0%** |
| independent feature-aware bot | 10.3 | **0%** |

Straight-line automation went from being charged with a warning to being
blocked 92% of the time. But an independently written humanised bot scores
11.3 against a human 11.3 — indistinguishable — even though the lab's own
`A3_human_mimic` is now caught 100% of the time.

**So the honest summary is: the detector catches attack techniques it has
samples of, and does not generalise to techniques it has not seen.** The 76%
recall is real and the 0% false-positive rate is real, and neither should be
read as "76% of bots are caught in the wild".

## What this means for deployment

The capture loop is the product, more than any single trained model. A
deployment that never records new labelled traffic will decay as attackers
change technique. What makes that workable is that capture is cheap: a few
minutes of browser time produces a few hundred labelled rows, and retraining
is one command.

## Reproducing

```bash
docker-compose up --build

pip install -r lab/requirements.txt
python -m playwright install chromium

# Record labelled real-browser telemetry (writes lab/real_telemetry.json)
python lab/capture.py --api http://127.0.0.1:8000 --repeats 8 --port 3100

# Retrain; prints the held-out table above
cd backend && python train_model.py
```

## Honest scope

- 234 samples from one machine and one browser build. Input device, screen
  size and operating system all shape pointer kinematics, and none of that
  variation is represented. A real false-positive rate needs many people on
  their own hardware.
- The "human" scenarios are scripted approximations of a person, driven
  through a real browser. They are far better than simulated feature vectors
  and still not recordings of actual customers.
- Scripted attacks establish a floor, not a ceiling. A determined attacker
  with unlimited attempts against a live endpoint is a different adversary,
  and the SHAP breakdown returned by `POST /api/analyze` currently gives that
  attacker a tuning signal.
