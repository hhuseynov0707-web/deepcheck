/**
 * Adversarial test: attack the demo with each bot profile against a REAL
 * backend and report whether the payment went through.
 *
 * This is the acceptance test for the whole product. capture.js stubs
 * /api/analyze because it wants clean traces; this does the opposite -- it
 * lets the real scorer see the bot and asks the only question that matters:
 * did the payment get blocked?
 *
 *   node attack.js --api http://localhost:8000
 *
 * Reports per profile: the scores the backend returned, and which of the four
 * outcomes the page reached (allowed / warned / verification / blocked).
 */

import { spawn, execFileSync } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

import { resolveChromium } from "./browser.js";
import { PROFILES, makeRng, sleep } from "./profiles.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
const PORT = 5199;

function parseArgs(argv) {
  const args = {
    api: "http://localhost:8000",
    profiles: Object.keys(PROFILES),
    seed: 7,
    duration: 14000,
    headed: false,
    out: path.join(REPO, "data", "attack-reports"),
  };
  for (let i = 2; i < argv.length; i++) {
    const [flag, inline] = argv[i].split("=");
    const value = inline ?? argv[i + 1];
    const consume = () => { if (inline === undefined) i++; };
    switch (flag) {
      case "--api": args.api = value; consume(); break;
      case "--profiles": args.profiles = value.split(","); consume(); break;
      case "--seed": args.seed = Number(value); consume(); break;
      case "--duration": args.duration = Number(value); consume(); break;
      case "--out": args.out = value; consume(); break;
      case "--headed": args.headed = true; break;
      default: console.error(`unknown flag: ${flag}`); process.exit(1);
    }
  }
  return args;
}

function userError(message) {
  const err = new Error(message);
  err.userFacing = true;
  return err;
}

async function reachable(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return true;
    } catch { /* not up */ }
    await sleep(400);
  }
  return false;
}

async function startVite(apiUrl) {
  const frontend = path.join(REPO, "frontend");
  const viteBin = path.join(frontend, "node_modules", "vite", "bin", "vite.js");
  if (!existsSync(viteBin)) {
    throw userError(`Frontend dependencies are not installed.\n\n  cd ${frontend}\n  npm install`);
  }

  const isWindows = process.platform === "win32";
  const proc = spawn(process.execPath, [viteBin, "--port", String(PORT), "--host", "127.0.0.1"], {
    cwd: frontend,
    stdio: ["ignore", "pipe", "pipe"],
    detached: !isWindows,
    // The page reads the backend location from this at build/serve time.
    env: { ...process.env, VITE_API_URL: apiUrl },
  });
  const log = [];
  const remember = (c) => { log.push(c.toString()); if (log.length > 40) log.shift(); };
  proc.stdout.on("data", remember);
  proc.stderr.on("data", remember);

  const stop = () => {
    try {
      if (isWindows) execFileSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
      else process.kill(-proc.pid, "SIGTERM");
    } catch { try { proc.kill("SIGTERM"); } catch { /* gone */ } }
    proc.stdout?.destroy();
    proc.stderr?.destroy();
    proc.unref();
  };

  const url = `http://127.0.0.1:${PORT}/demo`;
  if (!(await reachable(url, 90000))) {
    stop();
    throw userError(`Vite did not start.\n\n${log.join("").trim()}`);
  }
  return { url, stop };
}

/** What the page ended up showing the attacker. */
async function readOutcome(page) {
  return page.evaluate(() => {
    const text = document.body.innerText;
    const submit = document.querySelector('button[type="submit"]');
    return {
      blocked: text.includes("İşlem Reddedildi"),
      verification: text.includes("Ek Doğrulama Gerekli"),
      succeeded: text.includes("başarıyla alındı"),
      warned: text.includes("normal dışı görünüyor"),
      submitDisabled: submit ? submit.disabled : null,
      badge: (text.match(/GERÇEK KULLANICI|ŞÜPHELI|ŞÜPHELİ|YÜKSEK RİSK|BOT TESPIT EDİLDİ|BOT TESPİT EDİLDİ/i) || [null])[0],
    };
  });
}

async function attack({ context, url, profile, profileName, rng, duration }) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 900, height: 640 });

  const verdicts = [];
  page.on("response", async (res) => {
    if (!res.url().includes("/api/analyze")) return;
    try {
      const body = await res.json();
      if (typeof body?.risk_score === "number") {
        verdicts.push({ at: Date.now(), score: body.risk_score, label: body.label });
      }
    } catch { /* non-JSON or aborted */ }
  });

  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => Boolean(window.DeepCheck), null, { timeout: 15000 });
  await page.locator("#card-number").waitFor({ timeout: 15000 });

  const ctx = { rng, tabAway: async () => {}, viewport: { width: 900, height: 640 } };
  const started = Date.now();

  // Keep attacking until the window elapses, the way card testing hammers a
  // form, so the scorer accumulates evidence before the final submit.
  let passes = 0;
  while (Date.now() - started < duration) {
    await profile.run(page, ctx);
    passes++;
    const outcome = await readOutcome(page);
    // Any intervention is the answer; continuing past one measures nothing.
    if (outcome.blocked || outcome.verification) break;
    for (const sel of ["#card-number", "#card-name", "#card-expiry", "#card-cvv"]) {
      await page.locator(sel).fill("");
    }
  }

  await sleep(2600); // let a final flush land and the badge settle
  const outcome = await readOutcome(page);
  await page.close();

  const scores = verdicts.map((v) => v.score);
  return {
    profile: profileName,
    passes,
    verdicts: verdicts.length,
    first: scores.length ? scores[0] : null,
    peak: scores.length ? Math.max(...scores) : null,
    last: scores.length ? scores[scores.length - 1] : null,
    finalLabel: verdicts.length ? verdicts[verdicts.length - 1].label : null,
    outcome,
  };
}

function verdictOf(r) {
  if (r.outcome.blocked) return "BLOCKED";
  if (r.outcome.verification) return "VERIFICATION";
  if (r.outcome.succeeded) return "PAYMENT ALLOWED";
  if (r.outcome.warned) return "warned only";
  return "no intervention";
}

async function main() {
  const args = parseArgs(process.argv);

  if (!(await reachable(`${args.api}/api/health`, 8000))) {
    throw userError(
      `No backend answering at ${args.api}/api/health\n\n` +
        `This test needs the real scorer -- a stub would prove nothing.\n` +
        `Start it, then retry:\n\n` +
        `  cd ${path.join(REPO, "backend")}\n` +
        `  python train_model.py          (once, to create model.pkl)\n` +
        `  uvicorn main:app --port 8000`,
    );
  }
  console.log(`backend: ${args.api}`);

  const server = await startVite(args.api);
  const executablePath = resolveChromium();
  const browser = await chromium.launch({ headless: !args.headed, executablePath });

  const results = [];
  try {
    for (const name of args.profiles) {
      process.stdout.write(`attacking with ${name}... `);
      const context = await browser.newContext({ viewport: { width: 900, height: 640 } });
      try {
        const r = await attack({
          context,
          url: server.url,
          profile: PROFILES[name],
          profileName: name,
          rng: makeRng(args.seed + name.length * 104729),
          duration: args.duration,
        });
        results.push(r);
        console.log(verdictOf(r));
      } catch (err) {
        console.log(`FAILED: ${err.message.split("\n")[0]}`);
        results.push({ profile: name, error: err.message });
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
    server.stop();
  }

  console.log(`\n${"profile".padEnd(15)} ${"scores".padEnd(7)} ${"first".padEnd(7)} ${"peak".padEnd(7)} ${"last".padEnd(7)} ${"label".padEnd(20)} outcome`);
  for (const r of results) {
    if (r.error) { console.log(`${r.profile.padEnd(15)} ERROR`); continue; }
    const f = (v) => (v === null ? "-" : v.toFixed(1));
    console.log(
      `${r.profile.padEnd(15)} ${String(r.verdicts).padEnd(7)} ${f(r.first).padEnd(7)} ` +
        `${f(r.peak).padEnd(7)} ${f(r.last).padEnd(7)} ${String(r.finalLabel ?? "-").padEnd(20)} ${verdictOf(r)}`,
    );
  }

  const allowed = results.filter((r) => !r.error && r.outcome?.succeeded);
  console.log(
    allowed.length
      ? `\n${allowed.length} profile(s) got a payment through: ${allowed.map((r) => r.profile).join(", ")}`
      : `\nNo profile completed a payment.`,
  );

  mkdirSync(args.out, { recursive: true });
  const file = path.join(args.out, `attack-${new Date().toISOString().replace(/[:.]/g, "-")}.json`);
  const sink = createWriteStream(file);
  sink.end(JSON.stringify({ api: args.api, seed: args.seed, results }, null, 2));
  console.log(`report -> ${file}`);
}

main().then(
  () => process.exit(0),
  (err) => { console.error(err.userFacing ? `\n${err.message}\n` : err); process.exit(1); },
);
