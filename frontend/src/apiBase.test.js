import { afterEach, describe, expect, it, vi } from "vitest";

// The SDK as served: a plain script, not a module, so it is evaluated rather
// than imported. Same file nginx serves as /deepcheck.js (publicDir: "../sdk").
import sdkSource from "../../sdk/deepcheck.js?raw";

// API_URL is computed once, when apiBase.js is first evaluated, from what Vite
// put in import.meta.env at build time. Each case therefore stubs the variable,
// drops the module cache and imports a fresh copy -- the same thing a rebuild
// with a different .env does.
async function apiUrlWith(value) {
  vi.stubEnv("VITE_API_URL", value);
  vi.resetModules();
  const { API_URL } = await import("./apiBase.js");
  return API_URL;
}

describe("API_URL", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  // The default every build gets unless .env says otherwise. Same origin is
  // what lets one bundle serve localhost, a LAN address and a hotspot address
  // alike; a compiled-in localhost made a second laptop talk to itself.
  it("is same-origin when the variable is unset", async () => {
    expect(await apiUrlWith(undefined)).toBe("");
  });

  // docker-compose passes `VITE_API_URL: ${VITE_API_URL:-}`, i.e. an empty
  // string. With `||` that empty string fell back to http://localhost:8000.
  it("is same-origin when the variable is set but empty", async () => {
    expect(await apiUrlWith("")).toBe("");
    expect(await apiUrlWith("   ")).toBe("");
  });

  it("keeps an explicit cross-origin API, without the trailing slash", async () => {
    expect(await apiUrlWith("http://x:8000/")).toBe("http://x:8000");
    expect(await apiUrlWith("http://x:8000")).toBe("http://x:8000");
  });

  // "/" + "/api/session" would be "//api/session": a protocol-relative URL for
  // a host called "api", not this server.
  it("never produces a protocol-relative //api URL from a lone slash", async () => {
    expect(await apiUrlWith("/")).toBe("");
    expect(await apiUrlWith("//")).toBe("");
    expect(`${await apiUrlWith("/")}/api/session`).toBe("/api/session");
  });
});

// Demo.jsx hands API_URL to DeepCheck.init({ apiUrl }), and the SDK builds
// every request as `${apiUrl}/api/...` itself, so it has to apply the same
// rule: the page's "" must stay same-origin inside the SDK too.
describe("DeepCheck.init apiUrl", () => {
  afterEach(() => {
    delete window.DeepCheck;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // Evaluates a fresh copy of the SDK (its state lives in a closure), starts
  // it, and returns the URL of the first request it makes -- the session mint.
  // The mock answers 503 so registration fails fast and nothing else runs.
  async function firstRequestUrl(options) {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => null });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "error").mockImplementation(() => {});
    new Function("window", sdkSource)(window);
    window.DeepCheck.init({ ...options, onError: () => {} });
    await window.DeepCheck.ready();
    window.DeepCheck.stop();
    return fetchMock.mock.calls[0][0];
  }

  it("treats an empty apiUrl as the page's own origin", async () => {
    expect(await firstRequestUrl({ apiUrl: "" })).toBe("/api/session");
  });

  it("strips trailing slashes instead of requesting //api/...", async () => {
    expect(await firstRequestUrl({ apiUrl: "/" })).toBe("/api/session");
    expect(await firstRequestUrl({ apiUrl: "http://x:8000/" })).toBe("http://x:8000/api/session");
  });

  // Without the normalisation these became the relative path
  // "undefined/api/session" / "null/api/session".
  it("treats an explicit undefined or null apiUrl as same origin", async () => {
    expect(await firstRequestUrl({ apiUrl: undefined })).toBe("/api/session");
    expect(await firstRequestUrl({ apiUrl: null })).toBe("/api/session");
  });

  // Third-party integrators who never pass apiUrl keep the documented default.
  it("keeps the localhost default when apiUrl is omitted", async () => {
    expect(await firstRequestUrl({})).toBe("http://localhost:8000/api/session");
  });
});
