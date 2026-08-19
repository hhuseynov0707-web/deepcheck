import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard.jsx";

const SESSIONS = [
  {
    session_id: "aaaaaaaa-1111-2222-3333-444444444444",
    risk_score: 12.5,
    label: "Gerçek Kullanıcı",
    last_seen_at: "2026-08-19T10:00:00Z",
    response_time_ms: 40,
  },
  {
    session_id: "bbbbbbbb-1111-2222-3333-444444444444",
    risk_score: 91,
    label: "Bot Tespit Edildi",
    last_seen_at: "2026-08-19T10:00:05Z",
    response_time_ms: 45,
  },
];

function detailFor(id) {
  return {
    session_id: id,
    risk_score: 50,
    confidence: 0.9,
    response_time_ms: 42,
    shap_explanation: [],
    history: [],
  };
}

let calls;

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url) => {
      calls.push(url);
      const body = url.includes("/api/sessions")
        ? SESSIONS
        : detailFor(url.split("/api/score/")[1]);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const sessionCalls = () => calls.filter((u) => u.includes("/api/sessions")).length;

describe("Dashboard", () => {
  it("does not restart the session poll when a different session is selected", async () => {
    render(<Dashboard />);

    await waitFor(() => expect(screen.getByText(/^bbbbbbbb-1111/)).toBeInTheDocument());
    expect(sessionCalls()).toBe(1);

    // The first session auto-selects on load; pick the other one.
    fireEvent.click(screen.getByText(/^bbbbbbbb-1111/));

    await waitFor(() =>
      expect(calls.some((u) => u.includes("/api/score/bbbbbbbb-1111"))).toBe(true),
    );

    // Selecting a session must not tear down and re-run the session poll --
    // that resets the 3s refresh clock every time an operator clicks a card.
    expect(sessionCalls()).toBe(1);
  });

  it("announces a fetch failure to assistive technology", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false })));

    render(<Dashboard />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Oturumlar alınamadı"),
    );
  });
});
