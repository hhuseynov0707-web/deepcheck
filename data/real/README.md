# Recording real sessions

This is the open item the whole project rests on. Every "human" the detector
has ever seen is a script: the browser lab's H1 and H2 scenarios are Playwright
approximations of a person, so the model has learned one author's idea of what
people look like rather than what people actually do. That is why an
independently written humanised bot is still approved — it only has to match
the idea. Before the model was trained on real browser rows it scored 11.3
against a human's 11.3, indistinguishable; after, 22.7 against 17.3, AUC 0.92
(`docs/evaluation.md`). The signal exists and the ladder is not placed to act
on it, and that calibration waits on exactly the recordings described here.

**This directory is empty.** It holds a README and two `.gitkeep` files.
Nothing in this project has ever been measured on a real person.

Real recordings are the fix, and they need people rather than code.

## The short version

```bash
docker compose up -d                      # demo at http://localhost:3000/demo
cd backend

# 1. Look before you write: what is in the last 20 minutes, and is it usable?
python record_session.py --label human --preview --since 20m

# 2. Keep the ones that were actually that person, naming who they were.
python record_session.py --label human --person p01 --to-training <session-id>

# 3. Retrain with them included.
python train_model.py
```

`--preview` writes nothing. Use it every time: `--since` sweeps a window, and a
window almost never contains only the person you were watching.

`--to-training` is the part that matters. Without it a recording lands in
`data/real/` where only `evaluate.py` can read it, and the model never sees it.
With it, the session's flushes are merged into `lab/real_telemetry.json`, which
is what `train_model.py` blends into training.

`--person` is required alongside it, and is not bookkeeping. The holdout split
groups by person: without one, the best it can do is split by session, and
somebody who sat down ten times then lands on both sides of the split. The
accuracy that comes out then answers "does it recognise this person again"
rather than "does it work on somebody new", and only the second question
justifies the trouble of collecting any of this. Any stable pseudonym works —
`p01`, `p02` — and no real names are needed or wanted.

## Record within the hour

The retention sweep blanks raw telemetry after an hour (`RAW_RETENTION_HOURS`)
and deletes the rows after a day. Past the first hour a recording cannot even
be *checked*: an empty mouse channel is both a keyboard-only person and a row
that has aged out, and nothing left in the row tells them apart. The recorder
refuses to guess and skips those flushes, saying so.

Record while the person is still in the room.

## Three things the recorder refuses to do quietly

Each of these was a way to poison the dataset without ever seeing an error.

**A driven browser filed as a person.** If the browser reported
`navigator.webdriver`, or synthesised its own events, the session is rejected
unless you pass `--force`. Both signals are trivially defeated by an attacker,
which is exactly why they are worthless as detection and useful here: nobody
recording their own colleagues is trying to defeat them, so when one fires it
is a Playwright window somebody left open.

**A flush that measured almost nothing.** Every feature has a neutral fallback,
so a window in which the person did nothing still produces a full twelve-number
vector — one made almost entirely of fallbacks. Labelled "human" that teaches
the model that an empty window is a person, and an empty window is exactly what
a naive headless bot sends; the `A1_naive` rows in the same file say the
opposite, so the two cancel and the model learns nothing where it most needs to
learn something. Flushes measuring fewer than six of the twelve features are
dropped and counted (`--min-measured` to change it).

**A merge with no person attached.** See above.

## You cannot script the collection

Worth stating because it is the obvious shortcut. Driving the demo through
Chrome DevTools was tried: the session scored **99.5 — "Bot Tespit Edildi"** —
and produced 40 flushes of which **zero** were usable, because the automation
inserts text without dispatching keystrokes and teleports the pointer instead
of moving it. The recorder rejected the whole session on quality alone, before
any provenance check was needed.

That is the tooling working correctly, and it is also the point: the thing that
makes real recordings valuable is precisely the thing that cannot be
manufactured. It needs hands on a keyboard.

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
   python record_session.py --label human --person p01 --preview <session-id>
   python record_session.py --label human --person p01 --to-training <session-id>
   ```

   When several people went in a row, sweep the window — but preview it first
   and record each person under their own `--person`:

   ```bash
   python record_session.py --label human --preview --since 20m
   ```

   Prefer the relative form (`20m`, `2h`) over an absolute timestamp. Absolute
   times are read as UTC, so an operator typing their own wall clock in UTC+4
   sweeps four extra hours of sessions and hand-labels every one of them as a
   person.

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

**Scroll.** In all 234 rows the lab has captured, `scroll_hizi_varyansi` was
never measured once — no scenario scrolls, so every one of those rows carries
its neutral fallback for that feature. A recording in which the person scrolls
the page is the only way one of the twelve features has ever been observed at
all. Let people read the page.

## The other collection, for the per-customer profile

The bot score needs *many people once*. The per-customer profile layer needs
the opposite: **one person many times**, because it compares a session against
that same customer's own past sessions. Nothing in it has been validated on a
real person, and this is the protocol that would do it
(`docs/profile-evaluation.md` §12):

- **one consenting person**, at least **20 sittings** — the layer cannot
  compare at all below 19 reference sessions per input type, which is
  arithmetic (`ceil(1/0.05) − 1`), not a tuned threshold;
- **three device classes**: a phone, a laptop trackpad and a mouse. A profile
  is compared only within one input modality, with no pooled fallback, so each
  device class needs its own 19;
- recorded with `record_session.py --person`, the same flag the bot-score
  holdout uses.

Until that exists, every rate published for that layer was measured on
**synthetic identities**, and every false-challenge figure there is a **lower
bound**: a synthetic person is a generator with fixed parameters, so their
sessions are more self-consistent than any real person's. It is the
precondition for turning `PROFILE_ESCALATION=1` on outside a demo.

The synthetic demo customers (`backend/demo_seed.py`) are **not** a substitute
and must never be recorded as people: `record_session.py` refuses a session
flagged `is_synthetic`, and its `--since` sweep skips them.

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
