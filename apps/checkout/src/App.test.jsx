import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.jsx";

function stubBrowser() {
  vi.stubGlobal("DeepCheck", {
    init: vi.fn(),
    stop: vi.fn(),
    ready: vi.fn().mockResolvedValue(null),
    flush: vi.fn().mockResolvedValue(undefined),
    getSessionId: () => "s",
    getToken: () => "t",
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      status: 200,
      json: async () => ({
        items: [{ name: "Mekanik Klavye - RGB Aydınlatmalı", unit_price: 1699 }],
        subtotal: 1699,
        vat: 339.8,
        total: 2038.8,
        currency: "TRY",
      }),
    })),
  );
}

beforeEach(stubBrowser);
afterEach(() => vi.unstubAllGlobals());

describe("App shell", () => {
  it("shows the store, the payment brand and an honest footer", async () => {
    render(<App path="/" />);
    await screen.findByRole("button", { name: "₺2.038,80 Öde" });

    expect(screen.getByRole("banner")).toHaveTextContent("TechStore");
    expect(screen.getByRole("banner")).toHaveTextContent("DemoPay ile ödeme");
    expect(screen.getByRole("banner")).not.toHaveTextContent(/güvenli/i);
    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent("Demo ortamı - gerçek ödeme alınmaz.");
    expect(footer).toHaveTextContent(
      "Güvenliğiniz için bu sayfada fare/kaydırma hareketi ve tuşlara basılma zamanları ölçülür; ne yazdığınız ölçülmez.",
    );
    expect(screen.getByRole("link", { name: "Aydınlatma Metni" })).toHaveAttribute("href", "/gizlilik");
  });

  it("makes no security certification claim it cannot back", async () => {
    render(<App path="/" />);
    await screen.findByRole("button", { name: "₺2.038,80 Öde" });

    // Served over plain http on a LAN; no PCI assessment exists.
    expect(document.body.textContent).not.toMatch(/SSL|TLS|PCI|256-bit|256 bit|sertifika/i);
    // No real payment company's name.
    expect(document.body.textContent).not.toMatch(/paypal|stripe|iyzico|papara|mastercard secure|verified by visa/i);
  });

  it("routes /gizlilik to the notice, trailing slash included", () => {
    render(<App path="/gizlilik/" />);
    expect(screen.getByRole("heading", { level: 1, name: /KVKK Aydınlatma Metni/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Kart numarası")).not.toBeInTheDocument();
  });
});
