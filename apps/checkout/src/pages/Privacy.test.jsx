import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Privacy, { parseNotice } from "./Privacy.jsx";

describe("Privacy (/gizlilik)", () => {
  it("renders the repository's notice, retention table included", () => {
    // The default markdown is docs/kvkk-aydinlatma.md itself, imported at
    // build time: the page is the document, not a copy of it.
    render(<Privacy />);

    expect(screen.getByRole("heading", { level: 1, name: /KVKK Aydınlatma Metni/ })).toBeInTheDocument();
    expect(screen.getByText(/hukuki incelemeden geçmemiştir/)).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getAllByRole("row").length).toBeGreaterThan(1);
    expect(screen.getByText("yalnızca tuşa basılma anı").tagName).toBe("STRONG");
  });

  it("says what this store page itself sends, and links back to the checkout", () => {
    render(<Privacy />);

    const store = screen.getByRole("region", { name: "Bu ödeme sayfası hakkında" });
    expect(within(store).getByText(/Tam kart numarası ve CVV hiçbir sunucuya gönderilmez/)).toBeInTheDocument();
    expect(within(store).getByText(/misafir ödemesidir: e-posta adresi, hesap ya da başka bir kimlik bilgisi istenmez/)).toBeInTheDocument();
    expect(within(store).getByText(/yalnızca bu ödeme oturumuna özel bir müşteri referansı/)).toBeInTheDocument();
    expect(within(store).queryByText(/e-posta adresinizi/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ödeme sayfasına dön" })).toHaveAttribute("href", "/");
  });

  it("builds elements from the text and never markup", () => {
    const markdown = "# Başlık\n\nParagraf <img src=x onerror=alert(1)> ve `kod`.\n\n- bir\n- iki\n";
    const { container } = render(<Privacy markdown={markdown} />);

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(screen.getByText("kod").tagName).toBe("CODE");
  });

  it("drops the table's separator row and keeps its header", () => {
    const [table] = parseNotice("| a | b |\n|---|---|\n| 1 | 2 |\n");
    expect(table).toEqual({ type: "table", header: ["a", "b"], rows: [["1", "2"]] });
  });
});
