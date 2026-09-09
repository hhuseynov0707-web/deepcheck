import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import VerificationModal from "./VerificationModal.jsx";

// The modal cannot decide anything: it submits a code and renders the answer.
// Whether verification succeeded is settled by the server and read back by the
// charge endpoint, so these tests pin the modal to that role.
function setup({ verify, onVerified = vi.fn(), onClose = vi.fn() } = {}) {
  render(
    <VerificationModal
      verify={verify}
      onVerified={onVerified}
      onClose={onClose}
      demoCode="482913"
    />,
  );
  return { onVerified, onClose };
}

describe("VerificationModal", () => {
  it("is a labelled modal dialog", () => {
    setup({ verify: vi.fn() });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // Without the label a screen reader announces "dialog" and nothing else.
    expect(dialog).toHaveAccessibleName(/Ek Doğrulama Gerekli/);
    expect(screen.getByLabelText(/doğrulama kodu/i)).toBeInTheDocument();
  });

  it("shows the demo code, so it reads as a demo value rather than a bypass", () => {
    setup({ verify: vi.fn() });
    expect(screen.getByText("482913")).toBeInTheDocument();
  });

  it("reports a rejected code and does not let the payment proceed", async () => {
    const verify = vi.fn().mockResolvedValue({ ok: false, message: "Doğrulama kodu hatalı" });
    const { onVerified } = setup({ verify });

    await userEvent.type(screen.getByLabelText(/doğrulama kodu/i), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Doğrula" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/hatalı/);
    expect(onVerified).not.toHaveBeenCalled();
  });

  it("continues only after the server accepts the code", async () => {
    const verify = vi.fn().mockResolvedValue({ ok: true });
    const { onVerified } = setup({ verify });

    await userEvent.type(screen.getByLabelText(/doğrulama kodu/i), "482913");
    await userEvent.click(screen.getByRole("button", { name: "Doğrula" }));

    expect(verify).toHaveBeenCalledWith("482913");
    await vi.waitFor(() => expect(onVerified).toHaveBeenCalled());
  });

  it("refuses to submit an incomplete code", async () => {
    const verify = vi.fn();
    setup({ verify });

    await userEvent.type(screen.getByLabelText(/doğrulama kodu/i), "4829");
    expect(screen.getByRole("button", { name: "Doğrula" })).toBeDisabled();
    expect(verify).not.toHaveBeenCalled();
  });
});
