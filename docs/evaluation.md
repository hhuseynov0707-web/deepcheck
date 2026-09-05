# Evaluation

## Status: no real-session measurement exists yet

**There is no accuracy number for DeepCheck against real traffic, and this
page will not invent one.** Every figure the project has published so far —
including the RandomForest accuracy `train_model.py` prints at the end of a
training run — is measured on data produced by `train_model.py`'s own
simulator. Training and test split the same generator, so those numbers
describe how well the models fit the simulator. They are not evidence about
people.

That distinction is the whole reason this file exists. A fintech jury is
entitled to ask "how well does it work?", and the honest answer today is
"unmeasured against real users, by construction".

## What is in place

The measurement pipeline is built and runnable. What is missing is the data.

| Piece | State |
|---|---|
| `backend/record_session.py` | Freezes a labelled real session out of Postgres into `data/real/{label}/{id}.json` |
| `tools/bot_session.py` | Drives the demo checkout with Playwright, in a naive and a human-mimicking variant |
| `backend/evaluate.py` | Replays recorded sessions through the real serving path and reports accuracy, false-positive rate, recall, precision and ROC-AUC |
| `data/real/` | Empty. Recordings are personal data and are gitignored |

`evaluate.py` replays through `scorer.compute_risk()` with each flush's real
predecessors as LSTM history, and applies the same median smoothing
`/api/analyze` does, so a replayed score is the score the API would have
produced — not a re-derivation that quietly skips half the pipeline.

## How to produce the numbers

```bash
docker-compose up --build            # demo at http://localhost:3000/demo

# Humans: 30+ sessions, different people, mice, trackpads, machines.
cd backend && python record_session.py --list
python record_session.py --label human <session-id> [<session-id> ...]

# Bots: 30+ sessions, both variants.
python tools/bot_session.py --variant naive  --runs 15
python tools/bot_session.py --variant jitter --runs 15
cd backend && python record_session.py --label bot --since <start-time>

# Measure, and overwrite this file with the result.
python evaluate.py --markdown ../docs/evaluation.md
```

The sample-size floor matters. Thirty sessions per class is enough to expose
an obviously broken detector and nowhere near enough to quote a false-positive
rate to three decimal places; report the count alongside every number, which
is what `evaluate.py` does.

## What to watch for

- **Collection bias.** Sessions recorded from one person on one machine
  measure that person and that machine. Vary the input device above all: the
  mouse-acceleration feature is the most hardware-sensitive of the six.
- **The false-positive rate is the number that matters.** A blocked customer
  is a lost sale and a support call; a missed bot costs one fraudulent
  attempt. They are not symmetric, and accuracy alone hides the difference.
- **Label discipline.** The directory name is the ground truth. A human
  session filed under `bot/` does not produce a slightly worse number, it
  produces a misleading one.
