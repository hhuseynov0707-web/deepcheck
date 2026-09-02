/**
 * Bot behaviour profiles.
 *
 * Each profile drives the real payment form through the real SDK. What
 * separates them is HOW they actuate: whether the cursor moves at all, whether
 * its velocity has structure, whether delays are drawn from a distribution or
 * a constant. Those differences are the signal the model has to learn, so they
 * are written explicitly here rather than hidden behind a shared helper.
 *
 * Ordered roughly from trivially detectable to genuinely hard.
 */

const CARD = {
  number: "4242424242424242",
  name: "AHMET YILMAZ",
  expiry: "1229",
  cvv: "123",
};

const FIELDS = ["#card-number", "#card-name", "#card-expiry", "#card-cvv"];
// The verification modal carries its own submit button, so this selector
// goes ambiguous as soon as the modal opens. The payment form's button is
// first in DOM order.
const SUBMIT = 'button[type="submit"]';
const VALUES = [CARD.number, CARD.name, CARD.expiry, CARD.cvv];

export const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

/** Seeded PRNG so a capture run can be reproduced exactly. */
export function makeRng(seed) {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rng, mean, sd) {
  const u = Math.max(rng(), 1e-9);
  const v = rng();
  return mean + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function logNormal(rng, median, sigma) {
  return median * Math.exp(gauss(rng, 0, sigma));
}

async function centreOf(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`no bounding box for ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Cubic bezier with control points pushed off the straight line. */
function bezierPath(from, to, rng, samples) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy) || 1;
  // Perpendicular offset, scaled by distance: humans arc, they do not rule.
  const bow = (rng() - 0.5) * Math.min(dist * 0.4, 180);
  const nx = -dy / dist;
  const ny = dx / dist;

  const c1 = { x: from.x + dx * 0.3 + nx * bow, y: from.y + dy * 0.3 + ny * bow };
  const c2 = { x: from.x + dx * 0.7 + nx * bow, y: from.y + dy * 0.7 + ny * bow };

  const points = [];
  for (let i = 1; i <= samples; i++) {
    // Ease in-out: slow at both ends, fast through the middle. A constant-rate
    // sample would trace the same curve with a machine's velocity profile.
    const raw = i / samples;
    const t = raw < 0.5 ? 4 * raw ** 3 : 1 - (-2 * raw + 2) ** 3 / 2;
    const u = 1 - t;
    points.push({
      x: u ** 3 * from.x + 3 * u ** 2 * t * c1.x + 3 * u * t ** 2 * c2.x + t ** 3 * to.x,
      y: u ** 3 * from.y + 3 * u ** 2 * t * c1.y + 3 * u * t ** 2 * c2.y + t ** 3 * to.y,
    });
  }
  return points;
}

export const PROFILES = {
  /**
   * The dumbest thing that works: no cursor at all. Fields are filled by
   * synthetic keystrokes at machine speed and the button is clicked with
   * element.click(), which reports coordinates of (0,0). Should be trivial
   * to catch -- if the model misses this one, something is wrong.
   */
  naive_script: {
    description: "No mouse, no scroll, zero-delay typing, dispatched click",
    async run(page) {
      for (let i = 0; i < FIELDS.length; i++) {
        await page.locator(FIELDS[i]).pressSequentially(VALUES[i], { delay: 0 });
      }
      await page.locator(SUBMIT).first().dispatchEvent("click");
      await sleep(300);
    },
  },

  /**
   * Automation that knows it should move the cursor, but moves it the way a
   * computer does: straight lines, constant velocity, identical delays
   * between every action, scroll in equal fixed steps.
   */
  linear_mover: {
    description: "Straight-line cursor at constant velocity, uniform delays and scroll steps",
    async run(page) {
      const STEP_MS = 8;
      const GAP_MS = 120;
      let cursor = { x: 10, y: 10 };

      for (let i = 0; i < FIELDS.length; i++) {
        const target = await centreOf(page, FIELDS[i]);
        const steps = 25;
        for (let s = 1; s <= steps; s++) {
          cursor = {
            x: cursor.x + (target.x - cursor.x) / (steps - s + 1),
            y: cursor.y + (target.y - cursor.y) / (steps - s + 1),
          };
          await page.mouse.move(cursor.x, cursor.y);
          await sleep(STEP_MS);
        }
        await page.mouse.click(cursor.x, cursor.y);
        await page.locator(FIELDS[i]).pressSequentially(VALUES[i], { delay: 50 });
        await sleep(GAP_MS);

        // Uniform scroll: same delta, same cadence, every time.
        for (let w = 0; w < 3; w++) {
          await page.mouse.wheel(0, 100);
          await sleep(GAP_MS);
        }
      }

      const submit = await centreOf(page, SUBMIT);
      await page.mouse.move(submit.x, submit.y);
      await page.mouse.click(submit.x, submit.y);
      await sleep(300);
    },
  },

  /**
   * The common "make it look human" attempt: straight paths with gaussian
   * noise sprinkled on positions and delays. The noise is stationary -- it
   * has no acceleration structure -- which is exactly what ivme_degisimi and
   * scroll_hizi_varyansi are meant to expose.
   */
  jittered: {
    description: "Linear paths plus stationary gaussian noise on position, timing and scroll",
    async run(page, { rng }) {
      let cursor = { x: 20, y: 20 };

      for (let i = 0; i < FIELDS.length; i++) {
        const target = await centreOf(page, FIELDS[i]);
        const steps = 20 + Math.floor(rng() * 10);
        for (let s = 1; s <= steps; s++) {
          const t = s / steps;
          await page.mouse.move(
            cursor.x + (target.x - cursor.x) * t + gauss(rng, 0, 3),
            cursor.y + (target.y - cursor.y) * t + gauss(rng, 0, 3),
          );
          await sleep(Math.max(1, gauss(rng, 10, 3)));
        }
        cursor = target;
        await page.mouse.click(cursor.x, cursor.y);
        await page.locator(FIELDS[i]).pressSequentially(VALUES[i], {
          delay: Math.max(10, gauss(rng, 60, 15)),
        });
        await sleep(Math.max(20, gauss(rng, 150, 40)));

        for (let w = 0; w < 3; w++) {
          await page.mouse.wheel(0, 90 + gauss(rng, 0, 25));
          await sleep(Math.max(20, gauss(rng, 130, 35)));
        }
      }

      const submit = await centreOf(page, SUBMIT);
      await page.mouse.move(submit.x, submit.y);
      await page.mouse.click(submit.x, submit.y);
      await sleep(300);
    },
  },

  /**
   * The hard case, and the one worth measuring against. Curved cursor paths
   * with an ease-in-out velocity profile, log-normal delays, scroll delivered
   * in bursts with decaying momentum, a genuine reading pause, and one tab
   * away from the page. If the model separates this from real humans, the
   * result means something; if it cannot, that is worth knowing before a jury
   * asks.
   */
  bezier_mimic: {
    description: "Curved paths with eased velocity, log-normal delays, burst scrolling, real tab-away",
    async run(page, { rng, tabAway }) {
      let cursor = { x: 40 + rng() * 60, y: 40 + rng() * 60 };

      for (let i = 0; i < FIELDS.length; i++) {
        const target = await centreOf(page, FIELDS[i]);
        const points = bezierPath(cursor, target, rng, 28 + Math.floor(rng() * 12));
        for (const p of points) {
          await page.mouse.move(p.x, p.y);
          await sleep(logNormal(rng, 9, 0.45));
        }
        cursor = target;

        await sleep(logNormal(rng, 180, 0.5));
        await page.mouse.click(cursor.x, cursor.y);

        // Humans do not type at a fixed rate; per-character delay varies.
        for (const ch of VALUES[i]) {
          await page.locator(FIELDS[i]).pressSequentially(ch, { delay: 0 });
          await sleep(logNormal(rng, 110, 0.55));
        }

        // Reading pause on the first field, long enough to register as
        // hesitation (the SDK threshold is 400ms).
        if (i === 0) await sleep(700 + rng() * 900);

        // Burst scroll with decaying momentum, the way a trackpad flick reads.
        let delta = 120 + rng() * 80;
        for (let w = 0; w < 4 + Math.floor(rng() * 3); w++) {
          await page.mouse.wheel(0, delta);
          delta *= 0.6 + rng() * 0.2;
          await sleep(logNormal(rng, 55, 0.5));
        }
        await sleep(logNormal(rng, 250, 0.6));

        // Check a notification, come back. Under headless Chromium this
        // records nothing -- document.hidden never flips -- so bot traces
        // carry zero focus changes. See makeTabAway in capture.js.
        if (i === 2) await tabAway(600 + rng() * 700);
      }

      const submit = await centreOf(page, SUBMIT);
      for (const p of bezierPath(cursor, submit, rng, 30)) {
        await page.mouse.move(p.x, p.y);
        await sleep(logNormal(rng, 9, 0.45));
      }
      await sleep(logNormal(rng, 300, 0.5));
      await page.mouse.click(submit.x, submit.y);
      await sleep(500);
    },
  },
};
