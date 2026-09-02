import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

/**
 * Resolve the Chromium binary to drive.
 *
 * Images that pre-install Playwright browsers often ship a build number that
 * does not match what the installed playwright package expects, and its
 * default lookup then fails on a path that was never downloaded. Prefer the
 * newest chromium actually present under PLAYWRIGHT_BROWSERS_PATH, and fall
 * back to playwright's own resolution when there is nothing there.
 */
export function resolveChromium() {
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (!root || !existsSync(root)) return undefined;

  const candidates = readdirSync(root)
    .filter((entry) => entry.startsWith("chromium-"))
    .sort()
    .reverse();

  for (const entry of candidates) {
    const binary = path.join(root, entry, "chrome-linux", "chrome");
    if (existsSync(binary)) return binary;
  }
  return undefined;
}
