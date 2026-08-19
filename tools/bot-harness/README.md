# Bot trace harness

Captures **real** bot behaviour by driving the real DeepCheck demo page with
real browser automation, and records exactly what the real SDK sends.

Nothing here models behaviour statistically. The traces are whatever Chromium
and Playwright genuinely produce — which is the point, because those are the
tools an attacker uses. "Our bot class was captured from live Selenium and
Playwright automation against our own SDK" is a defensible answer; "we sampled
from a distribution we wrote" is not.

## Run it

Two installs are needed, not one. The harness has its own dependencies, and it
serves the demo page from `frontend/`, which has its own:

```bash
cd frontend && npm install
cd ../tools/bot-harness && npm install
npx playwright install chromium
npm run capture -- --sessions 25 --duration 12000
```

The `playwright` npm package does not ship the browser binaries — they are a
separate ~190MB download. Ask for `chromium` specifically: a bare
`npx playwright install` also pulls Firefox and WebKit, which this harness
never launches. Machines with a pre-baked browser under
`PLAYWRIGHT_BROWSERS_PATH` can skip the step; the harness finds those and says
which binary it picked on startup.

If you would rather run the demo yourself, skip the frontend install here and
point the harness at a page you already have running:

```bash
npm run capture -- --url http://localhost:3000/demo
```

On Windows the same commands work in both `cmd.exe` and PowerShell. Two things
to watch for:

- `#` is **not** a comment in `cmd.exe`. Anything after it on the line is passed
  through as arguments, so leave trailing notes off the command.
- Use `python` rather than `python3` for the loader; `python3` is usually
  unavailable on Windows.

```
cd C:\path\to\deepcheck\tools\bot-harness
npm install
npm run capture -- --sessions 25 --duration 12000
```

It starts the Vite dev server itself, so nothing else needs to be running.
The backend is **not** required: the harness intercepts `POST /api/analyze`,
records the body, and answers with a low score of its own. A real backend
would start blocking the bot partway through and change the behaviour being
measured.

| Flag | Meaning | Default |
|---|---|---|
| `--sessions N` | sessions per profile | 20 |
| `--duration MS` | minimum activity per session | 12000 |
| `--profiles a,b` | subset to run | all |
| `--url URL` | use a demo page already running | starts Vite |
| `--seed N` | PRNG seed, for reproducibility | 42 |
| `--out DIR` | output directory | `data/bot-traces` |
| `--headed` | watch the browser work | off |

Each SDK flush becomes one JSONL row, so a 12s session yields roughly 5-6 rows.
`--sessions 25` across four profiles produces something like 500-600 rows.

## Profiles

Ordered from trivially detectable to genuinely hard.

| Profile | What it does |
|---|---|
| `naive_script` | No cursor at all. Fields filled by synthetic keystrokes at machine speed, button clicked via `element.click()` so the coordinates come through as `(0,0)`. If the model misses this one, something is wrong. |
| `linear_mover` | Moves the cursor, but the way a computer does: straight lines, constant velocity, identical delays, scroll in equal fixed steps. |
| `jittered` | The common "make it look human" attempt — straight paths with gaussian noise on position, timing and scroll. The noise is *stationary*: it has no acceleration structure, which is what `ivme_degisimi` and `scroll_hizi_varyansi` exist to expose. |
| `bezier_mimic` | The hard case. Curved paths with an ease-in-out velocity profile, log-normal delays, scroll delivered in bursts with decaying momentum, a real reading pause, and a genuine tab-away that fires `visibilitychange`. |

Keep `bezier_mimic` honest. If your model cannot separate it from real humans,
that is worth knowing **before** a jury asks, not after. It is the profile that
tells you whether the system works or merely looks like it does.

## A finding worth keeping

Headless Chromium **never fires `visibilitychange`**. I probed both a second
tab with `bringToFront()` and CDP `Page.setWebLifecycleState`; `document.hidden`
stayed false through both, so `window.hides` never incremented.

That means captured bot traces carry **zero focus changes**, and it is not a
gap in this harness — it is a property of headless automation. A headless bot
structurally cannot tab away from your page. So `odak_degisimi` is a real
discriminator, and the honest way to describe it to a jury is: "headless
automation cannot produce this signal at all."

The flip side matters too: a genuine human who never leaves the tab produces
the same zero. That is what `NEUTRAL_DEFAULTS` in `scorer.py` is for, and it is
worth checking that this feature is not carrying more weight than it has earned
once you have real human traces to compare against.

## Output

`data/bot-traces/bot-traces-<timestamp>.jsonl`, one record per flush:

```json
{"label": "bot", "profile": "linear_mover", "session_index": 0,
 "passes": 3, "captured_at": "...", "payload": { ...SDK body... }}
```

`payload` is the untouched SDK request body, so it feeds straight into
`scorer.extract_features()` — the same function the API calls at request time.
No train/serve skew.

## The human half

This harness only produces the bot class. Human traces have to come from real
people, and are the scarce half of the problem — the reverse of most ML tasks.

Collect them through the SDK and write JSONL in the same shape with
`"label": "human"` into the same directory. Then:

```bash
cd backend && python load_traces.py
```

which reports row counts per profile and per-feature means for each class, so
you can confirm the classes actually separate before spending time training.

If you collect from real people, that is personal data under KVKK. The SDK's
design helps here — it records keystroke *timing* only, never key content,
never field values — but the collection page still needs a consent notice.
Worth saying out loud to a jury: it turns a compliance obligation into a
design claim.

## Note on Chromium

`browser.js` prefers whichever Chromium is actually present under
`PLAYWRIGHT_BROWSERS_PATH`, because pre-baked images often ship a build number
that does not match what the installed `playwright` package expects, and its
default lookup then fails on a path that was never downloaded. With nothing
there, it falls back to Playwright's own resolution.
