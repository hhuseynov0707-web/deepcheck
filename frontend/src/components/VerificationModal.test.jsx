import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VerificationModal from "./VerificationModal.jsx";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

function renderModal(props = {}) {
  return render(
    <VerificationModal onVerified={props.onVerified ?? vi.fn()} onClose={props.onClose ?? vi.fn()} />,
  );
}

describe("VerificationModal", () => {
  it("clears pending verification timers when unmounted mid-verify", () => {
    const { container, unmount } = renderModal();

    // Mounting the modal already schedules one timer of its own (the input's
    // autoFocus), so measure against that baseline rather than against zero.
    const baseline = vi.getTimerCount();

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "123456" } });
    fireEvent.submit(container.querySelector("form"));
    expect(vi.getTimerCount()).toBeGreaterThan(baseline);

    unmount();
    expect(vi.getTimerCount()).toBe(baseline);
  });

  it("exposes itself as a modal dialog with an accessible name", () => {
    renderModal();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("Ek Doğrulama Gerekli");
    expect(dialog).toHaveAccessibleName("Ek Doğrulama Gerekli");
  });

  it("gives the code input an accessible label", () => {
    renderModal();
    expect(screen.getByLabelText(/6 haneli doğrulama kodu/i)).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    renderModal({ onClose });

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("wraps Tab focus back into the dialog instead of letting it escape", () => {
    renderModal();
    const dialog = screen.getByRole("dialog");
    const focusables = Array.from(
      dialog.querySelectorAll("button:not([disabled]), input:not([disabled])"),
    );
    expect(focusables.length).toBeGreaterThan(1);

    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});
