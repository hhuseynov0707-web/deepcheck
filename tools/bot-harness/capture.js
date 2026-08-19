/**
 * Captures real bot behaviour traces for training.
 *
 * Drives the actual demo page with actual browser automation and records what
 * the actual SDK sends. Nothing here models behaviour statistically -- the
 * traces are whatever Chromium and Playwright genuinely produce, which is the
 * point: these are the tools an attacker uses.
 *
 * Each intercepted POST /api/analyze body is one training row, in the exact
 * shape scorer.extract_features() consumes, so there is no train/serve skew.
 *
 *   node capture.js --sessions 25 --duration 12000
 */

import { spawn } from "node:child_process";
import { mkdirSync, createWriteStream } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

import { resolveChromium } from "./browser.js";
import { PROFILES, makeRng, sleep } from "./profiles.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");

function parseArgs(argv) {
  const args = {
    sessions: 20,
    duration: 12000,
    profiles: Object.keys(PROFILES),
    url: null,
    headed: false,
    seed: 42,
    out: path.join(REPO, "data", "bot-traces"),
    viewport: { width: 900, height: 640 },
  };
  for (let i = 2; i < argv.length; i++) {
    const [flag, inlineValue] = argv[i].split("=");
    const value = inlineValue ?? argv[i + 1];
    const consume = () => {
      if (inlineValue === undefined) i++;
    };
    switch (flag) {
      case "--sessions": args.sessions = Number(value); consume(); break;
      case "--duration": args.duration = Number(value); consume(); break;
      case "--profiles": args.profiles = value.split(","); consume(); break;
      case "--url": args.url = value; consume(); break;
      case "--seed": args.seed = Number(value); consume(); break;
      case "--out": args.out = value; consume(); break;
      case "--headed": args.headed = true; break;
      case "--help":
        console.log(HELP);
        process.exit(0);
        break;
      default:
        console.error(`unknown flag: ${flag}\n`);
        console.log(HELP);
        process.exit(1);
    }
  }
  const unknown = args.profiles.filter((p) => !PROFILES[p]);
  if (unknown.length) {
    console.error(`unknown profile(s): ${unknown.join(", ")}`);
    console.error(`available: ${Object.keys(PROFILES).join(", ")}`);
    process.exit(1);
  }
  return args;
}

const HELP = `
Capture bot behaviour traces by driving the demo page with real automation.

  --sessions N    sessions per profile           (default 20)
  --duration MS   minimum activity per session   (default 12000)
  --profiles a,b  subset to run                  (default: all)
  --url URL       demo page; starts Vite if omitted
  --seed N        PRNG seed, for reproducibility (default 42)
  --out DIR       output directory               (default data/bot-traces)
  --headed        show the browser

Profiles:
${Object.entries(PROFILES).map(([k, v]) => `  ${k.padEnd(15)} ${v.description}`).join("\n")}
`;

async function waitForServer(url, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return true;
    } catch {
      // not up yet
    }
    await sleep(400);
  }
  return false;
}

async function startVite() {
  console.log("starting Vite dev server...");
  // detached puts Vite in its own process group so the whole tree can be taken
  // down at once. Vite spawns children that survive a SIGTERM aimed only at the
  // parent, and any survivor keeps this process's event loop alive forever --
  // a finished capture then looks like a hang.
  const proc = spawn("npm", ["run", "dev", "--", "--port", "5199", "--host", "127.0.0.1"], {
    cwd: path.join(REPO, "frontend"),
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });
  proc.stdout.on("data", () => {});
  proc.stderr.on("data", () => {});

  const stop = () => {
    try {
      process.kill(-proc.pid, "SIGTERM");
    } catch {
      try {
        proc.kill("SIGTERM");
      } catch {
        // already gone
      }
    }
    proc.stdout?.destroy();
    proc.stderr?.destroy();
    proc.unref();
  };

  const url = "http://127.0.0.1:5199/demo";
  if (!(await waitForServer(url))) {
    stop();
    throw new Error("Vite did not come up on port 5199 within 90s");
  }
  console.log(`Vite ready at ${url}`);
  return { url, stop };
}

/**
 * Switch away from the page and back.
 *
 * The SDK only records a focus change when the tab actually goes hidden, and
 * headless Chromium never reports that: document.hidden stays false through
 * bringToFront() and through CDP Page.setWebLifecycleState alike (probed, both
 * produce zero visibilitychange events). So under --headless this is a no-op.
 *
 * That is worth understanding rather than working around. A headless bot
 * structurally cannot produce a focus change, which is exactly why
 * odak_degisimi discriminates. The call stays because it does fire under a
 * real display, and because a bot that CAN tab away is the harder adversary
 * worth being able to model later.
 */
function makeTabAway(context, page) {
  return async (ms) => {
    const other = await context.newPage();
    await other.goto("about:blank");
    await other.bringToFront();
    await sleep(ms);
    await page.bringToFront();
    await other.close();
    await sleep(120);
  };
}

async function runSession({ context, url, profile, profileName, rng, duration, viewport }) {
  const page = await context.newPage();
  await page.setViewportSize(viewport);

  const captured = [];
  // Stub the scoring endpoint: the harness needs the full session, and a real
  // backend would start blocking the bot partway through and change its
  // behaviour. A low score keeps the page in its normal state.
  await page.route("**/api/analyze", async (route) => {
    const body = route.request().postDataJSON();
    if (body) captured.push(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body?.session_id ?? null,
        risk_score: 11.0,
        label: "Gerçek Kullanıcı",
        confidence: 0.9,
        shap_explanation: [],
        response_time_ms: 8,
      }),
    });
  });

  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => Boolean(window.DeepCheck), null, { timeout: 15000 });
  await page.locator("#card-number").waitFor({ timeout: 15000 });

  const ctx = { rng, tabAway: makeTabAway(context, page), viewport };
  const started = Date.now();

  // Repeat the profile until the session has run long enough to produce
  // several SDK flushes. Hammering one form repeatedly is also what card
  // testing actually looks like.
  let passes = 0;
  while (Date.now() - started < duration) {
    await profile.run(page, ctx);
    passes++;
    for (const sel of ["#card-number", "#card-name", "#card-expiry", "#card-cvv"]) {
      await page.locator(sel).fill("");
    }
  }

  // Let the final flush land (the SDK flushes on a 2s interval).
  await sleep(2400);
  await page.close();

  return captured.map((payload, index) => ({
    label: "bot",
    profile: profileName,
    session_index: index,
    passes,
    captured_at: new Date().toISOString(),
    payload,
  }));
}

async function main() {
  const args = parseArgs(process.argv);
  let server = null;
  let url = args.url;

  if (!url) {
    server = await startVite();
    url = server.url;
  } else if (!(await waitForServer(url, 10000))) {
    console.error(`cannot reach ${url}`);
    process.exit(1);
  }

  mkdirSync(args.out, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const outFile = path.join(args.out, `bot-traces-${stamp}.jsonl`);
  const sink = createWriteStream(outFile, { flags: "a" });

  const executablePath = resolveChromium();
  console.log(`chromium: ${executablePath ?? "(playwright default)"}`);
  const browser = await chromium.launch({ headless: !args.headed, executablePath });

  let rows = 0;
  const perProfile = {};

  try {
    for (const profileName of args.profiles) {
      const profile = PROFILES[profileName];
      perProfile[profileName] = 0;
      process.stdout.write(`\n${profileName}: `);

      for (let s = 0; s < args.sessions; s++) {
        const context = await browser.newContext({ viewport: args.viewport });
        try {
          const captured = await runSession({
            context,
            url,
            profile,
            profileName,
            // Seed per session so a rerun with the same --seed reproduces it.
            rng: makeRng(args.seed + s * 7919 + profileName.length * 104729),
            duration: args.duration,
            viewport: args.viewport,
          });
          for (const row of captured) sink.write(`${JSON.stringify(row)}\n`);
          rows += captured.length;
          perProfile[profileName] += captured.length;
          process.stdout.write(captured.length ? "." : "!");
        } catch (err) {
          process.stdout.write("x");
          console.error(`\n  session ${s} failed: ${err.message.split("\n")[0]}`);
        } finally {
          await context.close();
        }
      }
    }
  } finally {
    await browser.close();
    server?.stop();
    await new Promise((resolve) => sink.end(resolve));
  }

  console.log("\n\ncaptured rows by profile:");
  for (const [name, n] of Object.entries(perProfile)) {
    console.log(`  ${name.padEnd(15)} ${n}`);
  }
  console.log(`\ntotal ${rows} rows -> ${outFile}`);
  if (rows === 0) {
    console.error("no rows captured — the SDK never flushed. Is /demo loading the SDK?");
    process.exit(1);
  }
}

main().then(
  () => process.exit(0),
  (err) => {
    console.error(err);
    process.exit(1);
  },
);
