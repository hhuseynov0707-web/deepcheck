import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("marks new and live sessions without adding a control to any card", () => {
    const sessions = makeSessions(3, 91, "Bot Tespit Edildi");
    render(
      <SessionTable
        sessions={sessions}
        selectedId={sessions[0].session_id}
        onSelect={vi.fn()}
        newIds={new Set([sessions[1].session_id])}
        followedId={sessions[0].session_id}
        followedActive
      />,
    );

    // Still one button per session: the marks are spans inside the card.
    const cards = screen.getAllByRole("button");
    expect(cards).toHaveLength(3);
    const card = (i) => cards.find((c) => c.textContent.includes(sessions[i].session_id.slice(0, 13)));
    expect(within(card(1)).getByText("Yeni")).toBeInTheDocument();
    expect(within(card(0)).queryByText("Yeni")).not.toBeInTheDocument();
    expect(within(card(0)).getByText("Canlı")).toBeInTheDocument();
    expect(within(card(1)).queryByText("Canlı")).not.toBeInTheDocument();
  });

  it("calls a followed session that has gone quiet 'Takipte', never 'Canlı'", () => {
    // The teammate has paid and closed the tab; live follow is still on their
    // session. The selected-session badge says "Etkinlik yok" -- the card must
    // not say the opposite. followedActive is the caller's verdict, so leaving
    // it out reads as "not active", never as "live".
    const sessions = makeSessions(2, 12, "Gerçek Kullanıcı");
    const { rerender } = render(
      <SessionTable
        sessions={sessions}
        selectedId={sessions[0].session_id}
        onSelect={vi.fn()}
        followedId={sessions[0].session_id}
        followedActive={false}
      />,
    );
    const card = (i) =>
      screen.getAllByRole("button").find((c) => c.textContent.includes(sessions[i].session_id.slice(0, 13)));
    expect(within(card(0)).getByText("Takipte")).toBeInTheDocument();
    expect(within(card(0)).queryByText("Canlı")).not.toBeInTheDocument();
    expect(within(card(1)).queryByText("Takipte")).not.toBeInTheDocument();

    rerender(
      <SessionTable
        sessions={sessions}
        selectedId={sessions[0].session_id}
        onSelect={vi.fn()}
        followedId={sessions[0].session_id}
      />,
    );
    expect(within(card(0)).getByText("Takipte")).toBeInTheDocument();
    expect(within(card(0)).queryByText("Canlı")).not.toBeInTheDocument();

    // Live follow off: no followed session, no mark on any card.
    rerender(<SessionTable sessions={sessions} selectedId={sessions[0].session_id} onSelect={vi.fn()} followedActive />);
    expect(screen.queryByText("Takipte")).not.toBeInTheDocument();
    expect(screen.queryByText("Canlı")).not.toBeInTheDocument();
  });

  it("files a session the server cannot decide on yet apart from the bands, without its band label", () => {
    const sessions = makeSessions(3, 91, "Bot Tespit Edildi");
    render(
      <SessionTable
        sessions={sessions}
        selectedId={sessions[2].session_id}
        onSelect={vi.fn()}
        provisionalIds={new Set([sessions[2].session_id])}
      />,
    );

    const undecided = screen.getByRole("region", { name: "Değerlendiriliyor (1)" });
    const card = within(undecided).getByRole("button");
    expect(card).toHaveTextContent("s02aaaaaa-bbb");
    expect(within(card).getByText("Değerlendiriliyor")).toBeInTheDocument();
    expect(within(card).getByText("Ön skor")).toBeInTheDocument();
    expect(within(card).queryByText("Bot Tespit Edildi")).not.toBeInTheDocument();
    // The other two stay in their band, and the band counts only them.
    expect(screen.getByRole("region", { name: "Bot Tespit Edildi (2)" })).toBeInTheDocument();
    // Undecided first: with live follow on, that is the session just started.
    expect(cardPrefixes()).toEqual(["s02aaaaaa-bbb", "s01aaaaaa-bbb", "s00aaaaaa-bbb"]);
  });
});
