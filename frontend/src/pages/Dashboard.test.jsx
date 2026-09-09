import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard.jsx";

// The SOC endpoints expose every customer's live session, so the dashboard
// asks the analyst for the key rather than shipping one in the bundle. These
// tests pin that down: a key compiled into the bundle is served to every
// visitor and makes the header check decoration.
beforeEach(() => {
  window.sessionStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("Dashboard", () => {
  it("asks for the access key before fetching anything", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<Dashboard />);

    expect(screen.getByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
    // Nothing may be requested until a key exists.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the typed key as a header and never persists it beyond the tab", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);

    render(<Dashboard />);
    await userEvent.type(screen.getByLabelText(/Pano Erişim Anahtarı/i), "gizli-anahtar");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers["X-Dashboard-Key"]).toBe("gizli-anahtar");
    // sessionStorage, not localStorage: it dies with the tab.
    expect(window.localStorage.getItem("deepcheck.dashboardKey")).toBeNull();
  });

  it("drops back to the prompt when the key is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
    );

    render(<Dashboard />);
    await userEvent.type(screen.getByLabelText(/Pano Erişim Anahtarı/i), "yanlis");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    // A rejected key must not leave a dead page polling every three seconds.
    expect(await screen.findByText(/Yetkisiz erişim/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
  });
});
