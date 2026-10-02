import { afterEach, describe, expect, it, vi } from "vitest";

// The SDK as served: a plain script, not a module, so it is evaluated rather
// than imported. Same file nginx serves as /deepcheck.js (publicDir).
//
// These tests lived with the old combined demo (frontend/src/apiBase.test.js)
// until it was removed; this store is now the SDK's only page, so they live
// here, plus the acknowledgement-only reply the store's proxy asks for.
import sdkSource from "../../../../sdk/deepcheck.js?raw";

const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function loadSdk() {
  new Function("window", sdkSource)(window);
  return window.DeepCheck;
}

afterEach(() => {
  window.DeepCheck?.stop?.();
  delete window.DeepCheck;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// The SDK builds every request as `${apiUrl}/api/...` itself, so "" must stay
// same-origin inside the SDK too. The page passes "/deepcheck"; the rule is
// the same.
describe("DeepCheck.init apiUrl", () => {
  // Starts a fresh copy and returns the URL of its first request, the session
  // mint. The mock answers 503 so registration fails fast and nothing else runs.
  async function firstRequestUrl(options) {
    const fetchMock = vi.fn().mockResolvedValue(reply(503, null));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "error").mockImplementation(() => {});
    const sdk = loadSdk();
    sdk.init({ ...options, onError: () => {} });
    await sdk.ready();
    sdk.stop();
    return fetchMock.mock.calls[0][0];
  }

  it("treats an empty apiUrl as the page's own origin", async () => {
    expect(await firstRequestUrl({ apiUrl: "" })).toBe("/api/session");
  });

  it("keeps the store's prefix and strips trailing slashes", async () => {
    expect(await firstRequestUrl({ apiUrl: "/deepcheck" })).toBe("/deepcheck/api/session");
    expect(await firstRequestUrl({ apiUrl: "/deepcheck/" })).toBe("/deepcheck/api/session");
    expect(await firstRequestUrl({ apiUrl: "/" })).toBe("/api/session");
  });

  it("treats an explicit undefined or null apiUrl as same origin", async () => {
    expect(await firstRequestUrl({ apiUrl: undefined })).toBe("/api/session");
    expect(await firstRequestUrl({ apiUrl: null })).toBe("/api/session");
  });

  it("keeps the localhost default when apiUrl is omitted", async () => {
    expect(await firstRequestUrl({})).toBe("http://localhost:8000/api/session");
  });
});

// The store's nginx adds X-DeepCheck-Reply: ack, and the core then answers a
// behaviour window with {session_id, accepted} and no score, so the payer's
// browser never receives one. The SDK must take that as a success and say
// nothing about a score, to the page or to anyone listening on window.
describe("analyze replies", () => {
  async function sendOneWindow(analyzeBody) {
    const fetchMock = vi.fn(async (url, options) => {
      if (url.endsWith("/api/session")) return reply(201, { session_id: "s1", challenge: "c", difficulty_bits: 0 });
      if (url.endsWith("/api/session/attest")) return reply(201, { session_id: "s1", token: "t1" });
      return reply(200, typeof analyzeBody === "function" ? analyzeBody(JSON.parse(options.body)) : analyzeBody);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "error").mockImplementation(() => {});
    const updates = [];
    const events = [];
    const errors = [];
    const onEvent = (e) => events.push(e.detail);
    window.addEventListener("deepcheck:update", onEvent);
    const sdk = loadSdk();
    sdk.init({
      apiUrl: "/deepcheck",
      intervalMs: 1e9,
      onUpdate: (r) => updates.push(r),
      onError: (e) => errors.push(String(e.message)),
    });
    await sdk.ready();
    document.dispatchEvent(new KeyboardEvent("keydown"));
    await sdk.flush();
    window.removeEventListener("deepcheck:update", onEvent);
    const analyzeCalls = fetchMock.mock.calls.filter(([url]) => url.endsWith("/api/analyze"));
    return { updates, events, errors, analyzeCalls };
  }

  it("accepts an acknowledgement and passes no score on", async () => {
    const r = await sendOneWindow((body) => ({ session_id: body.session_id, accepted: true }));
    expect(r.analyzeCalls).toHaveLength(1);
    expect(r.analyzeCalls[0][0]).toBe("/deepcheck/api/analyze");
    expect(r.errors).toEqual([]);
    expect(r.updates).toEqual([]);
    expect(r.events).toEqual([]);
  });

  it("still reports a scored reply to onUpdate (any other integration)", async () => {
    const r = await sendOneWindow({ session_id: "s1", risk_score: 42.5, label: "Şüpheli" });
    expect(r.errors).toEqual([]);
    expect(r.updates).toHaveLength(1);
    expect(r.updates[0].risk_score).toBe(42.5);
  });

  it("reports a 2xx reply that is neither as an error", async () => {
    const r = await sendOneWindow({});
    expect(r.updates).toEqual([]);
    expect(r.events).toEqual([]);
    expect(r.errors.join(" ")).toMatch(/geçersiz yanıt/);
  });
});
