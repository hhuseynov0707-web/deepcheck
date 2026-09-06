# Recording real sessions

This is the open item the whole project rests on. Every "human" the detector
has ever seen is a script: the browser lab's H1 and H2 scenarios are Playwright
approximations of a person, so the model has learned one author's idea of what
people look like rather than what people actually do. That is why an
independently written humanised bot scores 11.4 against a human 11.4 — it only
has to match the idea.

Real recordings are the fix, and they need people rather than code.

## The short version

```bash
docker compose up -d                      # demo at http://localhost:3000/demo
cd backend
python record_session.py --list           # find the session someone just used
python record_session.py --label human --to-training <session-id>
python train_model.py                     # retrain with it included
```

`--to-training` is the part that matters. Without it a recording lands in
`data/real/` where only `evaluate.py` can read it, and the model never sees it.
With it, the session's flushes are merged into `lab/real_telemetry.json`, which
is what `train_model.py` blends into training.

## Running a collection session

1. **Start the stack** and leave it running. Warm it up first: a cold container
   trains for several minutes before it serves.

2. **Sit someone down at the demo** and let them buy the keyboard. Do not coach
   them, do not tell them a bot detector is watching, and do not ask them to
   "act natural" — all three change how people move. Let them read the page,
   hesitate, mistype, correct themselves. Those are the behaviours worth
   capturing and the simulator has none of them.

3. **Let it run for at least 10-15 seconds** of real interaction. Below three
   flushes there is not enough evidence to decide, and the session is thin.

4. **Record it while you still know whose it was.**

   ```bash
   python record_session.py --list --limit 10
   python record_session.py --label human --to-training <session-id>
   ```

   Or capture everything since a moment, when several people went in a row:

   ```bash
   python record_session.py --label human --to-training --since 2026-09-07T14:00
   ```

5. **Retrain and look at the holdout table.**

   ```bash
   python train_model.py
   ```

   It prints per-scenario results on held-out runs. Recorded sessions appear as
   `R1_human_live` and `R2_bot_live`, kept separate from the lab's scripted
   scenarios so you can see at a glance whether real people behave like the
   scripts claimed.

## What to collect

**Variety matters more than volume.** Thirty sessions from one person on one
laptop measure that person and that laptop. Pointer kinematics change with the
input device more than with anything else, so aim for:

- different people, including people who are slow or unfamiliar with the form
- mouse, trackpad and touchscreen
- different screen sizes and operating systems
- at least a few keyboard-only users who barely touch the pointer

That last group is the one most at risk of being wrongly blocked, and it is the
one the current model has the least evidence about.

**Aim for 30+ human sessions before quoting any false-positive number.** With
36 calibration samples the finest false-positive rate that can be asserted is
about 3%; the arithmetic floor is 1/(n+1) and training prints it.

## Labelling

The label is ground truth and nothing checks it. `human` means a person was at
the keyboard. It does not mean "a script that behaves politely". Mislabelling
one automated run as human does not slightly degrade the numbers — it teaches
the model that automation is human, which is the exact failure this data is
meant to correct.

For bot rows, `lab/bot_lab.py` drives real Chromium through the attack ladder,
and `lab/capture.py` records the result. Those are already labelled correctly.

## Privacy

The SDK records coordinates, timestamps and a focus-loss count. It never reads
key values, field contents or the DOM, so a recording contains no card number,
no name and nothing that identifies the person. Even so, `data/real/` is
gitignored: these are behavioural recordings of real people, they stay local,
and only the aggregate report is published.

Tell people what is being recorded before they sit down. It costs one sentence
and it is the difference between a demo and a consent problem.
