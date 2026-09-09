import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SessionTable from "./SessionTable.jsx";

// Cards render session_id.slice(0, 13), so the first 13 characters have to be
// distinct for a test to tell them apart.
function makeSessions(count, riskScore, label) {
  return Array.from({ length: count }, (_, i) => ({
    session_id: `s${String(i).padStart(2, "0")}aaaaaa-bbbb-cccc`,
    risk_score: riskScore,
    label,
    last_seen_at: new Date(Date.UTC(2026, 7, 19, 10, 0, i)).toISOString(),
  }));
}

const cardPrefixes = () =>
  screen
    .getAllByRole("button")
    .map((c) => c.textContent.match(/s\d\daaaaaa-bbb/)?.[0])
    .filter(Boolean);

describe("SessionTable", () => {
  it("marks the selected session as pressed and the others as not", () => {
    const sessions = makeSessions(2, 91, "Bot Tespit Edildi");
    render(
      <SessionTable sessions={sessions} selectedId={sessions[1].session_id} onSelect={vi.fn()} />,
    );

    const cards = screen.getAllByRole("button");
    expect(cards.filter((c) => c.getAttribute("aria-pressed") === "true")).toHaveLength(1);
    expect(cards.filter((c) => c.getAttribute("aria-pressed") === "false")).toHaveLength(1);
  });

  it("reports expanded state on the show-all toggle", () => {
    render(<SessionTable sessions={makeSessions(8, 91, "Bot Tespit Edildi")} selectedId={null} onSelect={vi.fn()} />);

    const toggle = screen.getByRole("button", { name: /tümünü göster/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: /daralt/i })).toHaveAttribute("aria-expanded", "true");
  });

  it("sorts each category with the most recently seen session first", () => {
    render(<SessionTable sessions={makeSessions(3, 91, "Bot Tespit Edildi")} selectedId={null} onSelect={vi.fn()} />);

    // last_seen_at increases with index, so the highest index leads.
    expect(cardPrefixes()).toEqual(["s02aaaaaa-bbb", "s01aaaaaa-bbb", "s00aaaaaa-bbb"]);
  });
});
