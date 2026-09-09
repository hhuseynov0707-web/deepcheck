import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Demo from "./Demo.jsx";

// The page holds no decision of its own: pressing Onayla asks
// POST /api/demo/charge and renders whatever came back. These tests pin that
// down, because the whole security argument rests on this file being unable to
// approve a payment by itself.
function stubSdk() {
  vi.stubGlobal("DeepCheck", {
    init: vi.fn(),
    stop: vi.fn(),
    getSessionId: () => "test-session",
    getToken: () => "test-token",
  });
}

function stubCharge(body) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(stubSdk);

afterEach(() => {
  // Unmount before removing the stub: Demo's effect cleanup calls
  // DeepCheck.stop(), and vitest runs this hook before the global cleanup
  // registered in vitest.setup.js.
  cleanup();
  vi.unstubAllGlobals();
});

describe("Demo", () => {
  it("labels every card field and gives it the right autocomplete token", () => {
    render(<Demo />);

    // Without these a password manager cannot fill the form and a screen
    // reader announces four unlabelled boxes.
    for (const [label, token] of [
      ["Kart Numarası", "cc-number"],
      ["Kart Üzerindeki İsim", "cc-name"],
      ["Son Kullanma Tarihi", "cc-exp"],
      ["CVV", "cc-csc"],
    ]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("autocomplete", token);
    }
  });

  it("announces a declined payment to assistive technology", async () => {
    stubCharge({
      status: "declined",
      decision: { action: "block", message: "İşlem Reddedildi — Şüpheli Davranış Tespit Edildi" },
    });

    const { container } = render(<Demo />);
    fireEvent.submit(container.querySelector("form"));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/İşlem Reddedildi/);
  });

  it("never reports success on its own when the server declines", async () => {
    // A "declined" body with no block action still must not produce a charge
    // message. The page renders the server's verdict, it does not derive one.
    stubCharge({ status: "declined", decision: { action: "verify", reason: "score" } });

    const { container } = render(<Demo />);
    fireEvent.submit(container.querySelector("form"));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("routes an unreachable backend to step-up rather than through", async () => {
    // Fail closed: a charge we could not complete is not a completed charge.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<Demo />);
    fireEvent.submit(container.querySelector("form"));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("shows a wait hint instead of an OTP prompt when evidence is thin", async () => {
    // Asking a real customer for a one-time code two seconds after they
    // arrived is a worse experience than telling them to carry on.
    stubCharge({
      status: "declined",
      decision: {
        action: "verify",
        reason: "insufficient_evidence",
        message: "Karar için yeterli davranış verisi yok",
      },
    });

    const { container } = render(<Demo />);
    fireEvent.submit(container.querySelector("form"));

    expect(await screen.findByText(/yeterli davranış verisi yok/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
