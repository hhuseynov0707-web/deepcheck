# Evaluation

> Jüri için Türkçe teknik cevaplar: [`juri-cevaplari.md`](juri-cevaplari.md) —
> özellikle "gerçek tarayıcıda kaydedilmiş insan izleri enjekte edilirse ne
> olur?" sorusu.


Measured 2026-09-06 against the running stack. Every number here comes from
telemetry that travelled the real path: browser input events → `sdk/deepcheck.js`
→ `POST /api/analyze` → the same scoring code that serves the demo.

## What changed

Until now this page said, correctly, that no accuracy figure existed against
anything but the simulator. That is no longer true. `lab/capture.py` drives a
real Chromium browser through the real SDK and records labelled telemetry;
234 samples were captured across six scenarios, and the models are now trained
on that data blended with the synthetic set.

## 2026-09-25: the first real person was flagged, and what it cost to fix

A person opened the demo and filled the card form by hand. The session scored
**"Yüksek Risk"**. It is the first real human this project has ever scored, and
it is recorded (`data/real/human/`, 52 flushes with raw telemetry, person p01).
Scored through the real serving path it gave a per-flush median of **68.0**,
with **90.4%** of flushes at or above the step-up line.

**Cause, measured on that recording's own clock.** Of 1044 deduplicated pointer
gaps, **52.1% are exactly 17 ms and 30.0% are 16 ms**; 89% land within 1 ms of a
multiple of 16.67 ms. Scroll is the same (45.8% / 31.3%). Keydowns are not
(modal share 4.1%, 47 distinct values in 49 gaps). A browser dispatches pointer
events on renderer frame boundaries, so a real browser repeats a millisecond gap
*by construction*. The simulator drew timestamps from continuous distributions
and essentially never repeated one — pointer modal-gap share 0.087 for its human
persona against **0.500** for the recorded person. `zaman_kuantasyonu` exists to
catch a scripted timer repeating an interval, and the forest had learned
"repeated millisecond gap = script". It was the top SHAP feature in **52 of 52**
flushes.

Ablation confirmed the attribution before anything was changed: replacing
`zaman_kuantasyonu` alone with the simulated-human median moved the median from
68.0 to 19.1 and the share at or above 60 from 90.4% to 1.9%.

**Why nothing caught it.** The browser lab's `H1_human` rows sit at
`zaman_kuantasyonu` 0.099, not 0.5. `lab/capture.py` drives the pointer with
`page.mouse.move()` plus a sleep, which dispatches through CDP and *off* the
frame clock. The lab is a real Chromium but not a real user's pointer event
clock, so 234 lab rows and the conformal calibration never saw this
distribution.

### What changed

`backend/train_model.py`, training distribution only — no feature definition and
no threshold was touched:

1. **A frame clock.** Pointer and scroll timestamps are stamped at the next
   renderer frame boundary. Per-session refresh rate 60/120/144 Hz weighted
   0.70/0.20/0.10 — the one machine ever recorded is 60 Hz, but a mix cannot be
   measured from one machine, so the mix is an **assumption**. Dropped frames 3%
   (measured: 32 of 973 intra-burst gaps span more than one frame), modelled as
   coalesced rather than delayed. Clicks and keydowns are *not* frame-stamped:
   in the recording they land on a 16.67 ms multiple 7.1% and 10.2% of the time,
   i.e. at chance.
2. **Pointer motion is a burst on a minimum-jerk path**, not an i.i.d.
   per-sample random walk. Burst length and rest fitted to the recording
   (median 10.5 samples, 417 ms rest).
3. **A `human_autofill` persona** (17.6% of the human class). Before it, every
   human window in training carried at least 15 keystrokes, so "no typing" could
   only come from a bot.
4. **`BOT_REAL_CLOCK_RATE = 0.50`** — half of `bot_sophisticated` drives a real
   browser through real input and gets the same frame clock, so the fix cannot
   become a new one-bit pass.

`data/real/**.json` now reaches training as a second source of real rows, read
in place rather than merged into the git-tracked lab file.

### Before and after

Every column is the same measurement re-run against the retrained bundle.

| measured on | before | after |
|---|---|---|
| **p01, 52 recorded flushes** — per-flush median | 68.0 | **0.0** |
| p01 — share of flushes >= 60 / >= 80 | 90.4% / 9.6% | **5.8% / 5.8%** |
| **p01 — smoothed session score, the value `/api/decision` reads** | **62.7** (verify) | **1.2** (allow) |
| p01 — share of smoothed values >= 60 | 100% | **0%** |
| stored-card autofill, score at the confirm flush | 67.7 | **0.1** |
| autofill + typed CVV, at the confirm flush | 55.2 | **0.3** |
| hand-typed control, at the confirm flush | 66.4 | **0.3** |
| hand-typed control, share of flushes >= 80 | 26.9% | **0%** |
| `benchmark.py` n=200, `bot` detected | 100% | 100% |
| `benchmark.py` n=200, `bot_sophisticated` detected | 100% | 100% |
| `bot_sophisticated` forced onto the frame clock, n=300 | not measurable | median 100.0, **100% >= 80** |
| `benchmark.py` human `rapid_legitimate`: blocked / median | 2.0% / 31.4 | **0% / 7.0** |
| `benchmark.py` human `typical_human`: median | 11.4 | **1.6** |
| scoring latency p95, n=1200 | 67.6 ms | **19.2 ms** |

The autofill rows are **generated** payloads, not a recording: pointer
timestamps resampled from p01's own gap distribution, 5 seeds per scenario,
scored as the SDK's 10 s window every 2 s. They also correct an earlier premise.
Autofill was never a separate bug: the same generator's hand-typed control
scored 66.4 at the confirm flush against autofill's 67.7. A real browser's
pointer clock put an ordinary checkout around 70 whether the card was typed or
stored.

### A second false positive, found by measuring the right number

The per-flush median collapsed (68.0 to 0.0) while the **smoothed** session
score went the wrong way: 62.7 became **88.4**, a block. Three of the 52 flushes
carried no usable activity (3/12 features measured, 0/6 structural); the forest
scores that neutral-fallback coordinate 88.5 / 97.3 / 88.4, and one of the three
was the *last* flush. Against a session sitting at 0.0 that is an 88-point jump,
which took the `LEVEL_SHIFT_POINTS` bypass. The retrain did not create this — it
exposed it, by making the calm flushes calm enough for the jump to clear 35.

The bypass now requires the current flush to have measured at least one
structural feature. The gate is deliberately *not* `provisional`: `headless_bot`
is also provisional (4/12 measured), so gating on that would have reopened the
mid-session handover hole the bypass exists to close. Structural coverage
separates them — human idle flushes 0/6, `headless_bot` 1/6,
`fast_keyboard_only_no_mouse` 2/6, `scripted_motion_bot` 4/6.
`LEVEL_SHIFT_POINTS` is still 35.0 and the window is still 5.

### End to end, in the live stack

The recording itself **cannot** be replayed through `/api/analyze`: the payload
fingerprint is deliberately clock-independent, so re-posting p01's windows is
exactly the replay the guard exists to stop — it refused all 52. Instead a real
Chromium was driven against the running container with pointer events paced to
p01's measured gap distribution (median 17 ms):

| live run | measured `zaman_kuantasyonu` | per-flush range | `/api/demo/charge` |
|---|---|---|---|
| card typed by hand | 0.374 | 6.5 – 22.7 | `allow`, 6.7, **charged** |
| card filled from the browser's store | 0.464 | 0.0 – 15.3 | `allow`, 6.4, **charged** |

p01's own recording measures 0.500 and the browser lab measures 0.099, so both
runs are genuinely in the quantised regime that used to score around 70. The
demo showed **"GERÇEK KULLANICI"** throughout. These are scripted sessions in a
real browser, not people: the pointer *clock* is real, the pointer *path* is a
minimum-jerk glide written for the test.

### What got worse

Reported because it did, not because it is comfortable.

- **Five `natural_human` test fixtures moved from 0.0–3.5 to 47.6–64.3**, out of
  the allow band and into step-up. Those fixtures build an i.i.d. Gaussian
  random-walk pointer path sampled at uniform(50,150) ms. Feeding the *same*
  generator a frame clock and/or velocity persistence (5 seeds, median):

  | fixture variant | served (old) | retrained (new) |
  |---|---|---|
  | as written (i.i.d., 50–150 ms gaps) | 2.5 | 49.2 |
  | frame clock only | **84.3** | 43.7 |
  | velocity persistence only | 24.0 | 26.3 |
  | frame clock + persistence | **87.5** | 30.2 |

  The more like a real browser the fixture gets, the worse the old model does
  and the better the new one does. The old model passed that test by being
  tuned to a pointer stream that does not exist. The bound was moved to the
  block line rather than deleted, and the reason is in the test's own comment.
- **The bot fixtures lost margin**: `scripted_motion_bot` 98.5 to 86.5 (bound
  >80), `bot_with_incidental_pause` 98.3 to 84.1, `fast_keyboard_only_no_mouse`
  99.9 to 88.5. All still pass. This is the expected direction: the old forest
  scored the simulator's own personas 0.2 (human) and 100.0 (bot), two classes
  so separable that they were a different problem from the one being served.
- **The 234 lab rows are now in the wrong coordinate system.**
  `lab/real_telemetry.json` stores already-normalised vectors and **no raw
  events** (checked: 0 of 234 carry one), and the log-scale endpoints moved in
  this retrain. Trained *with* them lab bot recall looks improved; trained
  *without* them it collapses to 0%. **Neither number is trustworthy**, and no
  lab-derived figure on this page should be quoted again until
  `lab/capture.py` has been re-run. That includes the table immediately below
  this section.

### What one real session can and cannot support

It is **one person, one machine, one 60 Hz monitor, one hand, one mouse**
(`pointer_mouse=50, pointer_pen=0, pointer_touch=0`). It is on the **training**
side of the split — with one recorded person there is nobody to hold out, and
training prints that unprompted — so every p01 number above is **in-sample**.
The simulator's frame clock was also *fitted* to this session's gap
distribution, so it was never a clean holdout in the first place.

What that supports: the failure mode was identified, reproduced from the raw
event timings, and is gone on the session that exhibited it and on a live
browser driven at the same clock. What it does **not** support: any
false-positive rate. One session is an anecdote. Nothing here says what happens
on a trackpad, a touch screen, a pen, a 120 Hz laptop or a throttled remote
desktop, because none of those has ever been recorded.

## Held-out real browser runs

Runs are held out whole, not flushes. Every flush from one browser session is
correlated with its siblings, so a per-flush split would put the same session
on both sides and report a number that will not reproduce.

| Scenario | n | mean p(bot) | flagged |
|---|---|---|---|
| H1_human (legitimate) | 28 | 0.083 | **0%** |
| A1_naive | 4 | 0.812 | 100% |
| A2_randomized | 12 | 0.689 | 75% |
| A3_human_mimic | 11 | 0.895 | 100% |
| A4_evasive | 16 | 0.968 | 100% |

**False positives 0%, bot recall 93.0%** — against these scenarios, on the
re-captured lab (2026-09-25). Before the re-capture the same table read 76.1%
recall; the section below explains why the earlier number was measured on rows
in a coordinate system that no longer existed.

`H2_keyboard_only` has **no held-out runs at all** in this split, which is a
weakness of the split and not a result: the assignment is a hash of the group
name, and all seven H2 runs happened to land on the training side. Keyboard-only
users are covered instead by protocol D in the model-selection study below,
where the whole scenario is held out — the shipped forest challenges 5% of them
at the step-up line and blocks none.

Read "false positives" carefully. The H1 and H2 rows are *scripted* human
behaviour driven through a real browser, not recordings of people. So the 0%
means the detector does not flag this lab's model of a legitimate user. It is
a far stronger statement than 0% against simulated feature vectors, and a far
weaker one than 0% against customers. Nobody should quote it as the latter.

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
samples of, and does not generalise to techniques it has not seen.** The 93%
recall is real and the 0% false-positive rate is real, and neither should be
read as "93% of bots are caught in the wild".

That sentence is now measured per model rather than asserted. Holding a whole
attack scenario out of training and scoring it, the shipped forest catches 0% of
an unseen randomised-bot family at either threshold — at every one of five
seeds — while a plain MLP on the same twelve features catches 75%, also at every
seed. The failure to generalise is a property of the model family. Why the MLP
is nevertheless not adopted is measured too, in the model-selection section
below.

## A correction, and the first movement on mimicry

Until 2026-09-07 the container served a model trained on **synthetic data
only**. It resolves the telemetry path to `/lab`, the parent of `/app`, and
`lab/` was not mounted into it, so training silently fell back and printed one
line about it. The host, where `lab/` sits beside `backend/`, trained on the
real rows. Every adversarial number published before that date therefore
described the host model rather than the one serving the demo, and the
conformal guard was inert because a synthetic-only run produces no held-out
human scores to calibrate on.

`lab/` is mounted now, the fallback prints a banner instead of a line, and the
path takes a `REAL_TELEMETRY_PATH` override. Re-measured against the corrected
model, 12 sessions per class:

| Persona | Mean | Enforcement | AUC vs human |
|---|---|---|---|
| human | 17.3 | 100% allow | — |
| naive headless script | 22.2 | 100% verify | 0.33 |
| straight-line automation | 84.8 | **100% block** | 1.00 |
| independent humanised bot | 22.7 | 100% allow | **0.92** |
| independent feature-aware bot | 13.4 | 100% allow | 0.08 |

**The humanised bot's separability went from 0.56 to 0.92.** That is the first
real movement on mimicry this project has measured, and training on real
browser telemetry is what produced it: means separated (22.7 against 17.3)
rather than drifting up together. The signal now exists.

It is still not stopped, and the reason is the ladder rather than the model —
22.7 sits far below the 40 where anything happens. Repositioning the thresholds
against an in-house adversary would be fitting the product to the attacker
rather than to real users, so that calibration waits for recorded human
sessions. Which is the same conclusion as everything else on this page.

Straight-line automation reached 100% blocked. Human false positives stayed at
zero. The feature-aware bot is unmoved at 0.08, below the human mean, because
it was tuned against feature definitions that did not change.

Caveat: 12 sessions per class. The separation of the means is real; the
precision of 0.92 is not.

## Decision-layer additions, and what they measured

Three statistical layers were added on top of the unchanged ensemble. (Since then the
ensemble itself changed: the score is now the RandomForest alone. The held-out
numbers above were always the forest's, so they now describe the shipped scorer.
See `backend/model_selection.py` and the fourth question in `juri-cevaplari.md`.)

**Sequential testing (SPRT)** replaced the fixed three-flush evidence rule.
A blatant session is decided once the three-flush floor is met, and an
ambiguous one keeps collecting. The bounds are an operating point on
uncalibrated scores, not Wald's error rates (see `backend/main.py` above
`SPRT_NOMINAL_ALPHA`). Crossing the bot bound is at least step-up. Side effect worth naming: a mid-band score no longer charges the
card on a few flushes, which closes the "Şüpheli means the payment goes
through" gap the adversarial run found.

**Conformal guard**, calibrated on 28 held-out scores from the lab's scripted
Playwright human personas -- scripts driving a real browser, not people. The
smallest p-value that many samples can produce is 0.034, which just clears the
0.05 the guard asks for. As served it softens nothing: the highest calibration
score is 33.2, so every score of 80 or more gets p = 1/29 = 0.034 < 0.05 and
no block is ever turned into a verify. (It read n=36, peak 27.71 before the
2026-09-25 lab re-capture changed the holdout; neither version fires.) It protects no real user until it is
calibrated on real ones (`backend/scorer.py`; the model bundle logs this state
at load).

**Cross-session behavioural clustering: measured, and shipped disabled.** The
idea was that mimicry cannot avoid repeating itself. Against the harness it
did not hold. 25 sessions of an independently written humanised generator
produced 30 distinct buckets, the same spread as 25 human sessions, because a
competent bot randomises its own parameters between runs. At 4 peers the rule
challenged 40% of humans to catch 60% of mimics; at 6 peers, 8% of humans to
catch 4%. A control that flags more customers than attackers is worse than no
control, so it is off by default.

That is a negative result and it is worth stating: behavioural quantisation
identifies a *kind of motion*, not a *particular script*. What the industry
actually clusters on is identity, which is far more stable across runs.

## Per-interaction-style false positives, and one that is bad

`backend/benchmark.py` reports the rate that decides whether this is
deployable: not global accuracy, but how often each *kind* of legitimate user
is refused. Every figure carries a 95% Wilson interval and its sample size,
because "under 1%" from thirty sessions is not a measurement. 60 sessions per
slice:

| Legitimate style | Blocked (95% CI) | Median risk |
|---|---|---|
| keyboard_only | 0.0% [0.0, 6.0] | 8.3 |
| slow_typist | 0.0% [0.0, 6.0] | 17.7 |
| low_pointer | 0.0% [0.0, 6.0] | 9.4 |
| rapid_legitimate | 0.0% [0.0, 6.0] | 27.3 |
| typical_human | 0.0% [0.0, 6.0] | 17.4 |
| **sparse_first_flush** | **35.0% [24.2, 47.6]** | 35.8 |

Five of six slices are clean, including the keyboard-only users who are most
at risk. The sixth is not: a window carrying only two or three pointer samples
and a handful of keystrokes -- which is what the opening seconds of any real
session look like -- scores above the block threshold a third of the time.

The cause is the thin end of the neutral-fallback design. Below the
small-sample gates the structural features cannot be measured at all, so the
fallbacks apply, and at two or three samples the remaining measured features
are dominated by their own sampling error.

### Fixed, by saying "not yet" instead of guessing

The first attempt was wrong and is worth recording. Raising the entropy
feature's small-sample gate to match the other timing statistics looked like
the obvious fix -- entropy over two gaps returns 0.0, which is the *most*
bot-like value the feature can take. It made things worse: 35% became 51.7%,
because the neutral fallback for entropy is itself closer to the bot end than
the measured values were. It was reverted.

What the measurement actually showed is that these windows have nothing to
score. Counting how many of the twelve features were genuinely measured
separates cleanly:

| Slice | Features measured (of 12) |
|---|---|
| sparse_first_flush | 4.1 avg, never above 5 |
| keyboard_only | 6.0 |
| low_pointer | 7.7 |
| rapid_legitimate | 9.8 |
| slow_typist | 10.5 |
| typical_human | 12.0 |

So a flush measuring fewer than six features is now returned as
**provisional**. The score is still computed and stored, because the history
chart and later analysis should see what the model said; what changes is that
the interface shows "still measuring" rather than a risk colour.

| Slice | Blocked before | Blocked after | Held back |
|---|---|---|---|
| sparse_first_flush | 35.0% | **0.0%** | 100% |
| every other slice | 0.0% | 0.0% | 0% |

Attack detection is unchanged at 100% for both families, because the flag is
display-only: the decision layer never reads it, so a bot with a thin window is
still scored and still refused. 27% of naive-bot windows are provisional, which
means their badge stays neutral while the charge is declined anyway.

This is a presentation fix, not a detection one, and it is the honest scope: a
window with three pointer samples genuinely does not say whether a person is
present, and the system should say so rather than guess.

## What this means for deployment

The capture loop is the product, more than any single trained model. A
deployment that never records new labelled traffic will decay as attackers
change technique. What makes that workable is that capture is cheap: a few
minutes of browser time produces a few hundred labelled rows, and retraining
is one command.

## Recording real people

The gap above closes one way only: sessions from actual people. The tooling is
now wired end to end, which it was not before —

```bash
cd backend
python record_session.py --list
python record_session.py --label human --to-training <session-id>
python train_model.py
```

`--to-training` is the part that was missing. Recordings previously landed in
`data/real/`, which only `evaluate.py` reads, so a session captured from a real
person never reached the model. They are now merged into the file training
blends, and they appear in the holdout table as `R1_human_live` and
`R2_bot_live`, separate from the lab's scripted scenarios.

See `data/real/README.md` for what to collect. Variety matters more than
volume: pointer kinematics vary more with input device than with anything
else, and keyboard-only users are both the group most at risk of being wrongly
blocked and the group the model has the least evidence about.

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

## A defect in this page's own data: four features saturate

Counted directly from `lab/real_telemetry.json`, the 234 rows every number
above rests on:

| feature | state in those 234 rows |
|---|---|
| `scroll_hizi_varyansi` | **never measured once** — all 234 sit at its neutral fallback 0.349, because no scenario scrolls |
| `ivme_degisimi` | exactly 1.0 in **90** rows — *every* `H1_human` row (50/50) and *every* `A3_human_mimic` row (40/40) — and at its fallback in the other 87 |
| `duraklama_dagilimi` | exactly 1.0 in **109** rows (H1 37/50, A3 32/40, A4 40/57) |
| `tiklama_oncesi_hareket` | measured in 66 rows and at 1.0 in all 66; fallback in the other 168 |

A feature pinned at a boundary contributes nothing to a split. So between the
lab's model of a person and the lab's best mimic, `ivme_degisimi` carries one
bit and it is the **same** bit — which is one reason `A3_human_mimic` is caught
by the other features rather than by the kinematic one it was designed for.
The cause is visible in the served bundle: the log-percentile endpoints for
that feature are 10^−7.021 … 10^−5.532, fitted on the simulator's
distribution, and real Chromium pointer motion sits above the top of the range.

**So the table at the top of this page was produced with one of the twelve
features never present and three more degraded.** Nothing here is invalidated
by that — the recall and the false-positive rate are what the served model does
on these runs — but "what these twelve features can do" is not what was
measured.

**Update, 2026-09-25.** Three of the four are largely repaired, and the fix was
not the one expected. Counted on the 52 flushes of the one real recording —
which, unlike the lab rows, *are* in the shipped coordinate system, because the
archive keeps raw events and they are re-extracted:

| feature | at the ceiling, old bundle | at the ceiling, shipped bundle |
|---|---|---|
| `ivme_degisimi` | 48/52 | **3/52** |
| `scroll_hizi_varyansi` | 27/52 | **0/52** |
| `tereddut_skoru` | 9/52 | **4/52** |
| `duraklama_dagilimi` | 44/52 | **44/52** — unchanged |

What moved the endpoints was the **simulator**, not the real data. The
log-percentile endpoints are fitted to the training distribution, and giving the
simulator a frame clock changed that distribution. The intended repair —
blending real raw values into the percentile pool — did **not** fire: 49 real
raw flushes now enter the pool but yield 46/47/48 values per log-scaled feature
against the `MIN_SCALING_VALUES = 50` floor. The floor was **not** lowered to
clear that by two values; it exists so that one odd run cannot set the scale. A
second recording crosses it.

`duraklama_dagilimi` is untouched because it is not log-percentile scaled at
all: it is a coefficient of variation divided by a hand-picked
`DISPERSION_DIVISOR = 1.5`, out of reach of this mechanism entirely. It is now
the dominant residual ceiling. Moving it into the fitted set changes a feature's
computation and so requires a `FEATURE_SCHEMA_VERSION` bump.

Two other gaps are unchanged and still open: `kanal_gecis_gecikmesi` was
measurable in only **2 of 52** real flushes and `tiklama_oncesi_hareket` in 19
of 52, so on real traffic those two are mostly the neutral fallback.

The 234 lab rows above can no longer be re-counted in the shipped coordinate
system at all — they store normalised vectors and no raw, so a retrain leaves
them frozen. Re-capturing with `lab/capture.py`, with at least one scenario that
scrolls, is now a prerequisite for quoting any lab number, not just an
improvement to it.

## 2026-09-25 (later the same day): the lab was re-captured, and every component was re-tried

The frame-clock fix above raised a fair objection: the Isolation Forest and the
LSTM were each dropped from the score *on measurement*, and those measurements
were taken against a simulator we now know was wrong about real browsers. A
model judged on a broken distribution was judged unfairly. So the whole
model-selection study was re-run — and before it could be, a second problem had
to be fixed.

### The browser lab was in a dead coordinate system, and it carried half the model

`lab/real_telemetry.json` stored only **normalised feature vectors** and no raw
events. Those vectors are frozen to whichever `feature_scaling` and
`neutral_defaults` were serving at capture time. The frame-clock retrain moved
both. So the 234 lab rows — carrying, by design, **50% of the forest's fitted
mass** — were describing points that no longer existed.

Re-capturing the same six scenarios with `lab/capture.py` (which now writes raw
telemetry per flush) and re-extracting under the scale in force gives the size
of the move directly. Median normalised value, old stored vs new re-extracted,
same scenario, same script:

| feature | A1_naive | A3_human_mimic | A4_evasive | H1_human |
|---|---|---|---|---|
| `ivme_degisimi` | 0.51 → 0.46 | 1.00 → 0.76 | 0.61 → **0.15** | 1.00 → **0.52** |
| `tereddut_skoru` | 0.96 → **0.57** | 0.40 → 0.33 | 0.39 → 0.27 | 0.29 → 0.21 |
| `hiz_otokorelasyonu` | 0.49 → 0.64 | 0.86 → 0.82 | 0.49 → 0.43 | 0.73 → 0.74 |

The first two are the log-percentile-scaled features. The third is a feature
that in these scenarios was usually *unmeasured*, so it carried the neutral
fallback — which had moved too. Nothing was renamed, so no hash could see any
of it.

**252 flushes across 46 runs were re-captured, every one carrying raw**, and the
recorded person's 49 usable flushes join them. All 301 real rows are now
re-extracted on every load and can no longer freeze. As a side effect the
percentile pool for the log-scaled features crossed `MIN_SCALING_VALUES` for the
first time with real values in it (209 for `tereddut_skoru`, 156 for
`ivme_degisimi`, against 46–48 before), so two of the three endpoints are now
fitted with real browser values in the sample. `scroll_hizi_varyansi` still has
only 46 and its endpoints are still the simulator's alone — the floor was not
lowered to clear it.

### What the corrected data did to the served model

Retraining on it and measuring the *session-level* score `/api/decision` reads —
not the per-flush score — on **held-out lab runs only**, runs the model never
trained on:

| | before (stale lab rows) | after (re-captured) |
|---|---|---|
| bot runs reaching block (≥80) | 11.1% | **77.8%** |
| bot runs reaching step-up (≥60) | 55.6% | **100%** |
| human runs reaching step-up (≥60) | 0.0% | 0.0% |
| human runs reaching block (≥80) | 0.0% | 0.0% |

n = 9 held-out bot runs and 4 held-out human runs. Small; the direction is the
claim, not the decimals. Across *all* 252 lab flushes the same direction holds:
bot flushes at or above 80 go 28.6% → 90.3%, human flushes stay at 0.0% at both
lines, and their median session score falls (H1 8.4 → 0.1, H2 5.0 → 0.4).
`A4_evasive` — the attack that used to score 36.5 and be approved — now blocks
8 runs out of 8.

The one recorded person improves too: final smoothed session score 1.2 → **0.0**,
and the number of flushes taking the level-shift bypass falls 10 → **0**. Both
in-sample; p01 is on the training side, as always.

No threshold, weight or constant was changed. The rows were put back into the
coordinate system they are scored in, and that is the whole intervention.

The retrain also moved the scaling endpoints, so `FEATURE_SCHEMA_VERSION` went
2 → 3 for the same reason it went 1 → 2: re-extracting the 52 recorded flushes
under both bundles gives `tereddut_skoru` mean −0.039 (max |d| 0.083) and
`ivme_degisimi` mean −0.033 (max |d| 0.044), the other ten features
bit-identical. A fifth the size of the previous move and still far too large to
compare stored customer vectors across, since `profiles.py` judges deviation
against a scale floor of 0.0062. The guard retired the synthetic demo customers
as stale and they were re-seeded.

### What got worse

**The five `natural_human` test fixtures moved further out of bounds**: 47.6–64.3
→ 61.7–83.1, with seed 4 now above the block line. This was investigated rather
than accommodated, by varying one axis of the fixture generator at a time:

| fixture variant | pointer autocorr | before | after |
|---|---|---|---|
| as written (i.i.d. walk, 50–150 ms gaps) | 0.39–0.50 | 49.2 | 62.2 |
| frame clock only (i.i.d. walk, 17 ms) | 0.48–0.56 | 43.7 | 58.9 |
| smooth path only (50–150 ms gaps) | 0.52–0.74 | 26.3 | 43.5 |
| frame clock + smooth path (realistic) | 0.82–0.92 | 30.2 | 46.0 |

The previous step argued this was a fixture artifact — that the more
browser-like the fixture got, the better the new model did. **That argument does
not survive this retrain.** Every variant rose, including the most realistic
one. The ordering still holds, and the fixture's pointer autocorrelation of
0.39–0.50 is nothing like the 0.855 of the one hand ever recorded, but the
honest statement is that the model became harsher on hand-written synthetic
human streams.

Set against that, everything measured on *real browser input* improved or held:
the recorded person, all 98 real-Chromium human flushes, and all six of
`benchmark.py`'s legitimate slices (0 blocked and 0 stepped up at n = 200 each,
though three of six medians rose — `keyboard_only` 2.4 → 7.4 and
`sparse_first_flush` 12.8 → 24.8 are the largest). The fixtures' bounds were
**not** relaxed. They are failing, and they are listed as failing.

### The retrial: does any dropped component earn its place back?

`backend/model_selection.py` was rebuilt and re-run end to end: eight tabular
families plus LSTM variants and blends, on 301 real rows blended with 6,400
simulated sessions, under six protocols. Two of them decide anything.

* **D — leave-one-scenario-out.** The scenario being scored was never in
  training. Every real customer, and every attack worth worrying about, is
  unseen by construction.
* **R — leave-one-*person*-out.** The recorded person's own flushes, scored by a
  model trained on the simulator and the entire browser lab but not on them,
  then put through `smooth_session_score()` — the value the payment turns on.

| model | C.auc | C.tpr@.8 | worst unseen human ≥.6 | ≥.8 | unseen A2 bot ≥.8 | R: p01 session | latency |
|---|---|---|---|---|---|---|---|
| **RandomForest (shipped)** | 0.996 | 0.81 | **0.05** (H2) | **0.00** | 0.00 | 40.9 | 13.3 ms |
| ExtraTrees | 0.998 | 0.92 | 0.63 (H2) | 0.00 | 0.00 | 46.2 | 20.8 ms |
| HistGB | 0.995 | 0.95 | 0.15 (H1) | 0.02 | 0.00 | 97.9 | 4.6 ms |
| LightGBM | 0.994 | 0.93 | 0.30 (H2) | 0.04 | 0.00 | 39.2 | 0.5 ms |
| XGBoost | 0.996 | 0.94 | 0.14 (H2) | 0.00 | 0.00 | 72.6 | 0.5 ms |
| LogReg | 0.982 | 0.79 | 0.42 (H2) | 0.14 | 0.12 | 0.0 | 0.2 ms |
| MLP | 0.996 | 0.92 | **0.04** (H1) | 0.02 | **0.75** | 1.1 | 0.2 ms |
| Isolation Forest (one-class) | 0.657 | 0.45 | 0.89 (H1) | 0.45 | 0.00 | 97.6 | 7.4 ms |

**A claim from the previous study is retracted.** It said that with the H1 human
scenario held out, LightGBM blocked 74% of those unseen humans at 80 while the
forest blocked none. That number was measured on the stale lab rows and does not
reproduce: the same protocol now gives LightGBM 0.04 and the forest 0.00. The
*conclusion* survives the fair retrial, but a conclusion that happens to survive
is not the same as a measurement that held, and the evidence quoted for it was
wrong.

**Gradient boosting genuinely is better at catching bots**, and it is not noise:
+0.11 to +0.15 on tpr@0.8, with a bootstrap over the 47 real groups excluding
zero. Its extra false-challenge cost on the lab (+0.014 to +0.020 on fpr@0.6)
*is* inside the noise.

**The last column is the uncomfortable one, and it indicts the shipped forest.**
With the randomised-bot scenario held out of training, the forest catches *none*
of it — 0.00 at both lines — and the MLP catches 0.75. Refitted at five
`random_state` values the forest gives 0.00 every time and the MLP 0.75 every
time; on the naive-bot scenario, forest 0.57 and MLP 1.00 at every seed. This
page has long said the detector "catches attack techniques it has samples of,
and does not generalise to techniques it has not seen". That sentence is now
measured per model, and it says the limitation is a property of the **model
family**, not of the data.

**So why is the MLP not adopted?** Because the same seed sweep shows where it is
unstable. Its share of an unseen keyboard-only human scenario challenged at 60
runs 0.00 / 0.00 / 0.07 / 0.16 / 0.33 across five seeds, and its recall on the
unseen evasive family at 80 runs 0.06 / 0.12 / 0.33 / 0.44 / 0.61. The forest's
corresponding spreads are 0.00–0.05 and 0.00–0.00. A model whose
false-challenge rate on unseen legitimate users is a coin flip over
`random_state` cannot hold a payment gate, and choosing the seed that looks best
is the exact move this project's honesty rule exists to stop. Seed-averaging or
ensembling it is the obvious next experiment, and it needs real people to be
measured against, not another lab run.

The one real person cannot break the tie either: held out by person, the seven
models give that single session 0.0, 1.1, 39.2, 40.9, 46.2, 72.6 and 97.9 —
three allows, three verifies and a block. Nor is it stable within a family:
running the same study against the *previous* bundle, whose scale differs by
0.04 on two of twelve features, moved LightGBM's number on that session from 0.0
to 39.2 while leaving its lab metrics unchanged to three decimals.

**The LSTM: still out, with a better reason.** Trained as it used to be shipped
(simulated full sequences) it reaches ROC-AUC 0.848 with a Brier of 0.440 on the
real rows. Trained on the shape the *serving* path actually produces — padded
prefixes, which `build_sequence()` emits from the first flush — 0.871, Brier
0.424: it ranks far better than it calibrates. Blending the real rows in out of
fold finally fixes the calibration (Brier 0.113) without closing the rank gap to
the forest's 0.996. Every blend is worse than the forest alone, and on the one
job it exists for it still loses: on simulated hand-overs at flush 6, the forest
reading only the current flush catches 100% at flush 6, the padded-prefix LSTM
63% (100% one flush later), and the LSTM as formerly shipped nothing until flush
10. Neither variant raises a single pure-human session at any point. Handing the
forest the previous flushes as extra columns adds nothing either (0.996 vs
0.996, tpr@0.8 0.80 vs 0.81).

**The Isolation Forest: still out, and the old reason retracted.** It used to be
rejected for being *inverted* — ROC-AUC 0.340 on real browser rows, worse than
chance, systematically voting for the attacker. On the corrected data it is no
longer inverted (0.657). It is merely useless in the direction that matters: it
puts 38% of legitimate flushes, 76% of the recorded person's flushes and 87% of
unseen H1_human flushes at or above the step-up line, and gives a stored-card
checkout a median session score of 54.0 where the forest gives 0.8. That is what
a one-class detector has to be here — it learns "normal" as the human
distribution, and in this product the attack *is* looking human — so the only
thing it can be confident about is that an unusual human is unusual. Every blend
that gives it weight loses.

**Verdict: nothing earns its place back; the RandomForest keeps the score
alone** — not because it won, but because it is the most stable model on the one
axis a payment gate cannot be wrong about, and the measurement that would
justify trading that stability away does not exist yet. What would produce it:
20–30 recorded people, several sessions each, split by person. Then protocol R
becomes a rate with an interval instead of an anecdote, and that rate picks the
family. Devices matter as much as headcount — no trackpad, touch, pen or
throttled session has ever been recorded, and the simulator's 0.70/0.20/0.10
refresh-rate mix is an assumption fitted to the only machine there is.

Reproduce with `cd backend && python model_selection.py`; the output at
`backend/model_selection_results.json` carries its own reading guide and its own
list of what its numbers cannot say.

### Open defect found while verifying this ship: the empty-window coordinate scores 99

Driving the live demo in a real browser four times gave `allow` at 0.9, 1.4 and
1.4 — and once `warn` at 41.0. The outlier was traced, and it is not caused by
this retrain.

A flush that measured almost nothing still produces a full twelve-number
vector, because every feature has a neutral fallback. In the outlier run the
first flush carried one or two events and came back with eleven of twelve
features at their fallback. **That coordinate scores 99.1.** It scored 99.0
under the previous bundle and 99.0 under the one before that, so this is a
standing property of the model, not a regression — but it is now measured
exactly, and it has a path to the payment:

* `compute_risk` marks the flush `provisional`, and `smooth_session_score`'s
  level-shift bypass refuses it because it measured no structural feature
  (that gate was added in the previous step, and it held here — the session
  score was never set from it).
* But `_decide_on_evidence` reads `BehaviorData.risk_score` for the last ten
  flushes **with no filter on `provisional`**, so a 99.1 goes straight into the
  sequential statistic. Three of them at the start of a session are what moved
  that run from `allow` to `warn`.
* The live badge on the demo page shows the per-flush score, so for the first
  two seconds a real customer can be shown "Bot Tespit Edildi 🚫".

Why the coordinate scores what it does is worth stating, because it is not a
bug in any one number. The neutral fallbacks are each the *median of the human
distribution* for that feature, computed by `converge_neutral_defaults`. In
twelve dimensions the point made of twelve marginal medians is not a typical
point — no actual human sits there — so the forest has no training rows nearby
and fills the region from whatever its splits happen to say. Moving one feature
away from its fallback is enough to collapse the score (`duraklama_dagilimi`
0.491 → 0.791 gives 24.0, `scroll_hizi_varyansi` 0.349 → 0.649 gives 28.0,
`zaman_kuantasyonu` 0.337 → 0.037 gives 51.6).

This was **not** fixed here. The fix is in the decision path — excluding
provisional flushes from the sequential statistic, or refusing to score a
vector that is mostly fallback at all — and either one re-derives the SPRT
operating point, which is a measure-set-re-measure loop that should not be run
on the same afternoon as a model change. It is written down instead, with the
number, as the first thing to pick up.

### Resolved 2026-09-26 — and the first fix was wrong

**One correction to the above first.** "For the first two seconds a real
customer can be shown 'Bot Tespit Edildi'" is not right for this coordinate:
`compute_risk` marks it `provisional` (fewer than six features measured), and
`Demo.jsx` never colours a provisional score. A thin flush that happens to
measure six or more *marginal* features is not provisional and is shown.

**What was changed.** A flush that measured none of the six structural features
— the ones that describe the generator rather than the sample, read from
`measured_mask` — is no longer evidence in either direction. The sequential
statistic reads only observed flushes, and a decision that rests on fewer than
three of them is `verify("insufficient_evidence")`: neither an approval nor a
block. Code and full reasoning: the comment above `main._structural_bits`.

**The first version was wrong, and a review measured how.** It excluded
unobserved flushes from the statistic and, when fewer than three observed
flushes remained, *skipped* the test and let the ladder decide on the smoothed
median. An adversarial review (four lenses, every decision through the real
`_decide_on_evidence`, payloads through the real `compute_risk`, an emulation
of `deepcheck.js`'s buffer for the attack scripts) found that an unobserved
flush's score is **not** reliably high. The six marginal features stay partly
measurable and `hesitation_intervals` is supplied by the client, so thin
windows can be steered low: 3000 crafted unobserved payloads scored a median of
**29.7**, hill-climbed ones **3.5**. A naive script — a pointer move every few
seconds, fields set by value, one click — produces flushes that swing between
~30 and ~100, and the ladder's median then charged it: **88 of 300** schedules
at the click flush, against **0 of 300** before. Re-checked independently:
17 of 60 against 0 of 60. That version never shipped past this branch.

**Measured, final rule** (OLD = every flush counted, as before any gate):

| attack, passing decision points | OLD | first gate | final |
|---|---|---|---|
| SDK-faithful trickle script, 300 schedules | 0 / 2340 | 337 weakened | **0 / 2340** |
| SDK-faithful burst script, 299 schedules | **547** / 2030 | 1360 | **0 / 2030** |
| pointer-only script, at the click, 300 | 0 | 47 | **0** |
| pointer + keydown script, at the click, 300 | 0 | 57 | 1 |
| all-unobserved, random knobs (S1) | **883** / 1600 | 1479 | **0** |
| all-unobserved, hill-climbed (S2) | **1600** / 1600 | 1600 | **0** |
| 2 observed human + unobserved automation (S6) | 111 / 1360 | 360 | **0** |
| simulator personas bot_sophisticated, drift_to_bot, drift_to_human | unchanged | unchanged | unchanged |
| simulator persona `bot` (60 of 640 windows unobserved) | — | — | 22 of 512 points block → verify, **never charged** |

The first four rows are scripts emulating what `deepcheck.js` actually emits. S1 and S2 are hand-built payloads — sendable over HTTP, not what the SDK produces. The bold OLD figures are holes that existed **before any of this**: an
all-unobserved session built from steerable flushes was charged by the old rule
too. The final rule closes them.

| legitimate (benchmark.py slices, synthetic, 60 seeds × prefixes 3–10) | OLD | final |
|---|---|---|
| no thin opening flushes, every slice | — | **identical** |
| 2–4 thin openings: blocks per 480 | 3–8 | **0** |
| all-thin sessions (every flush a sparse opening): blocks per 480 | 24–33 | **0** |
| prefix-3 checkout, 2 thin + 1 real flush: blocked | 13–15 / 600 | **0 / 600** |
| same: charged | 59.5–72.3% | 0% — "a few more seconds" |

The cost is structural and exact: a checkout decided before three flushes have
observed anything gets `insufficient_evidence` instead of a charge — one
decision point per thin opening flush (60 per 480 per opening). The page turns
that into "a few more seconds", and a second hint into a step-up. The SDK ships
a 10 s rolling window, so real activity stays observed for about five flushes
once it starts; the hint costs about two seconds. These are **synthetic**
customers, so the rate is a lower bound on real friction.

**What it does not close.** Unobserved automation followed by, or interleaved
with, **three or more human-looking observed flushes**: 1333 → 1920 of 2560
passing (S4), 1 → 300 of 800 alternating (S5a). One SDK-faithful script also moved: pointer + keydown every few seconds, 0 → 1 of 2331 points. This is not a new capability.
The same attacker, emitting nothing during the automated part — the SDK sends no
flush for an empty window, so the server has no row — passes the old rule
**40 of 40** at every M ≥ 3. Producing human-looking observed flushes on demand
is defeating the per-flush model itself, the limit already stated at
`SPRT_MAX_FLUSHES`.

**Live, in the running stack** (real Chromium, pointer paced to p01's
measured gap distribution, card typed; the pointer *clock* is real, the path is
scripted — these are not people): **8 of 8** checkouts charged, `allow` at
0.9–1.4, with opening flushes scoring up to 75.2. Before the gate the previous
step's four runs gave one `warn` at 41.0. Eight runs cannot show a rate; the
replays above are the evidence, this shows the path works end to end.

**Not evidence about this change**, although it looks like it: the simulator's
human personas never produce an unobserved flush (0 of 3000), and the one real
recording (p01) never has more than two in a window, so both are unaffected by
construction.

### Round two: three more defects, found by reviewing the fix

A second adversarial round attacked the final rule itself — an attack lens, a
code lens with mutation testing, each serious finding re-run by two independent
refuters. It found three things that were wrong, all now fixed and pinned by
tests, and one that is not the gate's to fix.

**1. A block could still rest on unobserved flushes.** The gate guarantees three
observed flushes in the window, but the ladder reads the *smoothed* score, the
median of the newest five — thin flushes included. Three thin, high flushes at
the end of a session decided a block by themselves, and a block is the one
verdict a step-up cannot lift. Measured on synthetic sessions with thin tails
(benchmark.py slice bodies, 60 seeds × 5 bodies, prefixes 3–10, n = 2400 per
pattern): four-flush tails blocked **7** under the pre-gate rule and **10**
under the gate; five-flush tails **9** and **15**. Now, a block is held at
step-up when fewer than three of the five flushes behind the smoothed score were
observed: **0 and 0**. It can only turn a block into a verify, so no attack
count above changed (re-run on the final code: every passing count identical).

**2. One step-up approved every checkout for five minutes.** `verified_at` was
written once and never cleared, so after one verification every decision that
came back `verify` became `allow` for `VERIFICATION_VALID_S` = 300 s. The
review charged the same all-unobserved session five times in a row on a single
step-up, bounded only by the rate limit (~400 per verification). A
verification is now **spent by the approval it produces**, with a
compare-and-set so two concurrent checkouts cannot both spend it, and without
touching `last_seen_at` (the freshness clock the staleness rule reads). One
step-up authorises one approval, the way a 3-D Secure authentication covers
one transaction.

**3. The tests did not pin the gate's definition.** Every test built its masks
from the same function it was testing. A real mask is never 0 — click density
and focus changes are always measured — so a mutant counting *any* set bit as
"observed" passed all 181 tests, and in production that mutant is the same as
having no gate. Three other definitions survived too. A test now reads masks
from payloads `compute_risk` actually produced (one pointer event: mask 48, no
structural feature; twelve jittered keydowns: two of six). Checked by mutation:
all four surviving definitions are now caught, and removing the gate fails five
tests.

**Not the gate's to fix: a keyboard-only script evades the model.** The rule's
protection is "an observed flush is scored by the model". That holds for
pointers — straight-line and random-walk pointer flushes are observed and score
80–95. It does not hold for keys. A script that types the card number, expiry
and CVV as keydowns with **randomised gaps** (a `time.sleep(random.uniform(…))`
between keys, no pointer, no motor model) produces flushes that measure the two
key-channel structural features — so they are observed — and the forest scores
them as a person: observed-flush median **1.6**, and a 23-keydown card entry was
**charged 119 of 120** times (SDK-faithful emulation, fresh seeds, decision at
the click). It is charged identically under the rule before any gate, so this
is a hole in the model, not a regression; constant key timing is caught (0 of
120). Keyboard-only people and stored-card autofill are legitimate trained
classes, which is exactly the region this script sits in. It is the cheapest
realistic card-testing attack found in this project, and **nothing here claims
to stop it**; closing it needs keyboard-specific signals measured against real
people, which this project does not have yet.

**Accepted and stated:** the internal reason `unobserved` is shown to the client
as `insufficient_evidence`, not collapsed into `step_up`, so the page can say "a
few more seconds" instead of opening an OTP box in the first seconds of every
checkout. That leaks one bit, "fewer than three of your flushes were observed".
The review judged it low-value: acting on it means producing observed flushes,
which is what puts a session in front of the model.

## The architecture: which component answers which threat

This is a system, not a classifier, and its parts answer different questions.
Naming them together is also the only honest way to state the one thing none of
them answers.

| Layer | Where | The threat it answers | What it is measured at |
|---|---|---|---|
| **Per-flush population model** | `scorer.compute_risk` — a RandomForest over 12 behavioural features | Behaviour whose kinematics and timing were not drawn from a human population: naive scripts, straight-line automation, randomised jitter | ROC-AUC 0.996 on 301 real rows; 0% of real-browser human flushes challenged; `A4_evasive` 8/8 runs blocked. **Does not generalise**: an unseen attack family is caught 0% of the time (measured above) |
| **Sequential evidence** | `main.py`, an SPRT-shaped test over the per-flush scores of **observed** flushes only | A session individually ambiguous per flush but leaning consistently one way — and a bot buying approval with a few calm flushes. Crossing the bot bound is at least step-up, whatever the smoothed score says. Fewer than three observed flushes is `verify`, never an approval or a block | An operating point on uncalibrated scores, not Wald error rates. Measured on synthetic sessions, the held-out lab runs, and an adversarial review of the observed-flush gate (above) |
| **Level-shift rule** | `scorer.smooth_session_score` | Mid-session hand-over: a person starts the form, automation finishes it. A plain median hides it | 185/185 simulated hand-overs caught on the first automated flush, against 0/185 for median smoothing alone. Gated on the flush having measured at least one structural feature, after an idle window cost a real person a payment |
| **Per-customer conformal profile** | `profiles.py`, off by default | Account takeover: a session on a known customer's reference that does not behave like that customer. It can only ask for **more** verification — never blocks, never approves, and matching earns no discount | `docs/profile-evaluation.md`. Gives nothing against card-testing bots, which arrive with no customer reference and never reach it |
| **Conformal guard** | `scorer.conformal_p_value` | Blocking an unusual but legitimate human, by refusing to block on a score that is unremarkable among held-out humans | **Currently inert.** Its calibration sample is 28 session scores from scripted lab humans with a maximum of 33.2, so a score of 80 gets p = 0.034 < α = 0.05 and no block is ever softened. It protects nobody until it is calibrated on real people |
| **Attestation and replay defences** | `POST /api/session` + `/api/session/attest`, the replay hash, the `client_sent_at` age check | A caller that skips the browser entirely, and re-posting a captured payload | It does **not** prove a browser: a plain Python script obtained a token 50 times out of 50. It raises the floor; it is not a gate |

### What none of them answers: a competent replay of a real person's telemetry

If an attacker obtains a real person's pointer and keystroke timings — malware, a
compromised extension, a hostile SDK — and replays them coherently through a
real browser under a fresh session and token, every layer above passes it:

* the population model sees human kinematics, because it **is** human kinematics;
* the sequential test accumulates human evidence, flush after flush;
* the level-shift rule sees no shift, because the generator never changes;
* the per-customer profile *matches* — and matching gives no discount, but it
  also raises nothing;
* the conformal guard would be protecting the replay, not catching it;
* attestation is satisfied, because a real browser is genuinely being driven.

This is an information-theoretic limit, not a modelling gap. The classifier sees
only the vector; if the vector was drawn from the human distribution, the
decision is "human", and no function of that telemetry separates it. The
project's own measurements are consistent with it: driving the *same* evasive
attack through real Chromium instead of the simulator moved it from 88.9
(blocked) to 36.5 (approved) before those rows were trained on, and an
independently written humanised bot still scores 11.3 against a human's 11.3 in
the adversarial harness above.

The one narrow seam is cross-channel consistency (`tiklama_oncesi_hareket`,
`kanal_gecis_gecikmesi`): stitching a recorded mouse trace onto synthetic
keystrokes, or reusing one trace across differently-timed form fills, shows the
join. A competent attacker closes it by recording whole sessions. It is offered
as extra work for the attacker, not as a defence.

The answer is not more behavioural features. It is signals that are properties
of the **input stack** rather than of the reported coordinates — which this
project does not have. `juri-cevaplari.md` works through the same argument in
Turkish, questions 2–5.

## Honest scope

- **The "human" class is Playwright, not people.** H1 and H2 are scripted
  approximations executed in a real Chromium. Every kinematic property they
  have was chosen by whoever wrote the scenario, so the model has been taught
  what this lab thinks a person looks like. The structural features are
  therefore an unverified mitigation, not proof that human-like automation is
  caught.
- 234 samples from one machine and one browser build. Input device, screen
  size and operating system all shape pointer kinematics, and none of that
  variation is represented. A real false-positive rate needs many people on
  their own hardware.
- The "human" scenarios are scripted approximations of a person, driven
  through a real browser. They are far better than simulated feature vectors
  and still not recordings of actual customers.
- Scripted attacks establish a floor, not a ceiling. A determined attacker
  with unlimited attempts against a live endpoint is a different adversary,
  and a SHAP breakdown returned to the scored client would give that attacker
  a tuning signal -- which is why `POST /api/analyze` no longer returns it
  (`SHAP_IN_ANALYZE` defaults to 0; the SOC panel reads it behind the
  dashboard key).
