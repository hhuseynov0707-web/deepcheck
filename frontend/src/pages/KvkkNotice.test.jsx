import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import KvkkNotice, { parseNotice } from "./KvkkNotice.jsx";

describe("KvkkNotice", () => {
  it("renders the repository's notice, retention table included", () => {
    // The default markdown is docs/kvkk-aydinlatma.md itself, imported at build
    // time: the page the Demo link opens is the document, not a copy of it.
    render(<KvkkNotice />);

    expect(screen.getByRole("heading", { level: 1, name: /KVKK Aydınlatma Metni/ })).toBeInTheDocument();
    expect(screen.getByText(/hukuki incelemeden geçmemiştir/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    const demoRow = within(table).getByText(/Demo ad alanındaki her şey/).closest("tr");
    expect(demoRow).toHaveTextContent("24 saat");
    // The key sentence on what never happens, rendered as emphasis.
    expect(screen.getByText("yalnızca tuşa basılma anı").tagName).toBe("STRONG");
  });

  it("builds elements from the text and never markup", () => {
    const markdown = "# Başlık\n\nParagraf <img src=x onerror=alert(1)> ve `kod`.\n\n- bir\n- iki\n";
    const { container } = render(<KvkkNotice markdown={markdown} />);

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(screen.getByText("kod").tagName).toBe("CODE");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("drops the table's separator row and keeps its header", () => {
    const [table] = parseNotice("| a | b |\n|---|---|\n| 1 | 2 |\n");
    expect(table).toEqual({ type: "table", header: ["a", "b"], rows: [["1", "2"]] });
  });
});
