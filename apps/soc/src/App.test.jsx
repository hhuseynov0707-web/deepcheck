import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.jsx";

// The SOC login. The legacy dashboard asked for DASHBOARD_KEY and kept it in
// sessionStorage, sending it as a header on every poll; here soc-api holds the
// key and the browser holds only an httpOnly cookie. These tests pin down the
// browser's half of that: the key goes out once, in the login body, and
// nowhere else -- not in storage, not in a header, not on screen afterwards.

function preferReducedMotion() {
  vi.stubGlobal("matchMedia", (query) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  }));
}

const reply = (status, body = {}) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

// A soc-api whose answers a test can change. `session` is whether the cookie
// would be accepted.
function socApi({ session = false, loginStatus = 204, logoutStatus = 204 } = {}) {
  const server = { session, loginStatus, logoutStatus, sessions: [] };
  const fetchMock = vi.fn(async (url, options = {}) => {
    const method = options.method ?? "GET";
    if (url === "/api/me") return reply(server.session ? 204 : 401);
    if (url === "/api/login" && method === "POST") {
      if (server.loginStatus === 204) server.session = true;
      return reply(server.loginStatus);
    }
    if (url === "/api/logout" && method === "POST") {
      if (server.logoutStatus === 204) server.session = false;
      return reply(server.logoutStatus);
    }
    if (!server.session) return reply(401, { detail: "Oturum açılmamış ya da oturumun süresi dolmuş" });
    if (url === "/api/sessions") return reply(200, server.sessions);
    return reply(404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { server, fetchMock };
}

const calls = (fetchMock, url) => fetchMock.mock.calls.filter(([u]) => u === url);
const keyField = () => screen.getByLabelText(/Pano Erişim Anahtarı/i);

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  preferReducedMotion();
});

afterEach(async () => {
  await vi.advanceTimersByTimeAsync(1000);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SOC login", () => {
  it("asks soc-api whether the session is valid before anything else, and shows the login form when it is not", async () => {
    const { fetchMock } = socApi();
    render(<App />);

    expect(await screen.findByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
    // Nothing but the session check may be requested until a login.
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/me"]);
    expect(fetchMock.mock.calls[0][1].credentials).toBe("same-origin");
    // A labelled field and a real heading, not a placeholder-only prompt.
    expect(screen.getByRole("heading", { name: "SOC Panosu" })).toBeInTheDocument();
    expect(keyField()).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: /Panoya Gir/ })).toBeDisabled();
  });

  it("goes straight to the dashboard when the cookie is already valid", async () => {
    const { fetchMock } = socApi({ session: true });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Canlı Oturum İzleme" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Pano Erişim Anahtarı/i)).not.toBeInTheDocument();
    await waitFor(() => expect(calls(fetchMock, "/api/sessions").length).toBeGreaterThan(0));
  });

  it("sends the key once, in the login body, and never stores it or sends it as a header", async () => {
    const { fetchMock } = socApi();
    render(<App />);
    await userEvent.type(await screen.findByLabelText(/Pano Erişim Anahtarı/i), "gizli-anahtar");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    expect(await screen.findByRole("heading", { name: "Canlı Oturum İzleme" })).toBeInTheDocument();
    const [[, options]] = calls(fetchMock, "/api/login");
    expect(options.method).toBe("POST");
    expect(options.credentials).toBe("same-origin");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(options.body)).toEqual({ key: "gizli-anahtar" });

    await waitFor(() => expect(calls(fetchMock, "/api/sessions").length).toBeGreaterThan(0));
    for (const [url, init = {}] of fetchMock.mock.calls) {
      if (url === "/api/login") continue;
      // Every other request carries the cookie (same-origin) and no key.
      expect(init.credentials).toBe("same-origin");
      expect(JSON.stringify(init)).not.toContain("gizli-anahtar");
    }
    // Neither storage holds it; nor does the page.
    for (const store of [window.sessionStorage, window.localStorage]) {
      for (let i = 0; i < store.length; i += 1) expect(store.getItem(store.key(i))).not.toContain("gizli-anahtar");
    }
    expect(document.body.innerHTML).not.toContain("gizli-anahtar");
  });

  it("keeps a rejected key on the login screen with the error next to the field", async () => {
    const { fetchMock } = socApi({ loginStatus: 401 });
    render(<App />);
    await userEvent.type(await screen.findByLabelText(/Pano Erişim Anahtarı/i), "yanlis");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    const error = await screen.findByText(/Yetkisiz erişim/i);
    expect(error).toHaveAttribute("role", "alert");
    expect(keyField()).toHaveAttribute("aria-invalid", "true");
    expect(keyField().getAttribute("aria-describedby")).toContain(error.id);
    // A rejected login must not start the dashboard's polls.
    expect(calls(fetchMock, "/api/sessions")).toHaveLength(0);
  });

  it("says when login attempts are rate limited", async () => {
    socApi({ loginStatus: 429 });
    render(<App />);
    await userEvent.type(await screen.findByLabelText(/Pano Erişim Anahtarı/i), "deneme");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    expect(await screen.findByText(/Çok fazla giriş denemesi/)).toBeInTheDocument();
  });

  it("reports an unreachable server in Turkish, never in the browser's own words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);

    // The session check itself failed: said above the form.
    expect(await screen.findByText(/Sunucuya ulaşılamadı/)).toBeInTheDocument();
    await userEvent.type(keyField(), "gizli-anahtar");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));
    await waitFor(() => expect(screen.getAllByText(/Sunucuya ulaşılamadı/)).toHaveLength(2));
    expect(screen.queryByText(/Failed to fetch/i)).not.toBeInTheDocument();
  });

  it("returns to the login screen with an explanation when a poll is answered 401", async () => {
    const { server, fetchMock } = socApi({ session: true });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Canlı Oturum İzleme" })).toBeInTheDocument();

    // The cookie expires (or is logged out in another tab).
    server.session = false;
    await act(() => vi.advanceTimersByTimeAsync(3000));

    expect(await screen.findByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
    expect(screen.getByText(/SOC oturumunuz sona erdi/)).toBeInTheDocument();
    // And it stops polling.
    const polls = calls(fetchMock, "/api/sessions").length;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(calls(fetchMock, "/api/sessions")).toHaveLength(polls);
  });

  it("logs out through soc-api from the top bar", async () => {
    const { fetchMock } = socApi({ session: true });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Canlı Oturum İzleme" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Oturumu Kapat" }));

    expect(await screen.findByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
    expect(screen.getByText("Oturum kapatıldı.")).toBeInTheDocument();
    const [[, options]] = calls(fetchMock, "/api/logout");
    expect(options.method).toBe("POST");
    expect(options.credentials).toBe("same-origin");
  });

  it("says so when the logout did not reach the server, instead of claiming it worked", async () => {
    socApi({ session: true, logoutStatus: 502 });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Canlı Oturum İzleme" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Oturumu Kapat" }));

    expect(await screen.findByText(/Çıkış isteği sunucuya ulaşmadı/)).toBeInTheDocument();
    expect(screen.queryByText("Oturum kapatıldı.")).not.toBeInTheDocument();
  });

  it("lets the analyst check the typed key, with a labelled toggle", async () => {
    socApi();
    render(<App />);
    await userEvent.type(await screen.findByLabelText(/Pano Erişim Anahtarı/i), "abc");

    const toggle = screen.getByRole("button", { name: "Anahtarı göster" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    expect(keyField()).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Anahtarı gizle" })).toHaveAttribute("aria-pressed", "true");
  });
});
