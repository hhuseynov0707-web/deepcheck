import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Demo from "./Demo.jsx";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("DeepCheck", { init: vi.fn(), stop: vi.fn() });
});

afterEach(() => {
  // Unmount before removing the DeepCheck stub: Demo's cleanup calls
  // DeepCheck.stop(), and vitest runs this hook before the global cleanup.
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Demo", () => {
  it("clears pending payment timers when unmounted mid-payment", () => {
    const { container, unmount } = render(<Demo />);

    fireEvent.submit(container.querySelector("form"));
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    // Navigating to the dashboard while "İşleniyor..." is on screen must not
    // leave timers running that then set state on a dead component.
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("labels every card field and gives it the right autocomplete token", () => {
    render(<Demo />);

    const fields = [
      ["Kart Numarası", "cc-number"],
      ["Kart Üzerindeki İsim", "cc-name"],
      ["Son Kullanma Tarihi", "cc-exp"],
      ["CVV", "cc-csc"],
    ];

    for (const [label, token] of fields) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("autocomplete", token);
    }
  });

  it("announces a blocked payment to assistive technology", () => {
    let pushRisk;
    vi.stubGlobal("DeepCheck", {
      init: ({ onUpdate }) => {
        pushRisk = onUpdate;
      },
      stop: vi.fn(),
    });

    render(<Demo />);
    act(() => pushRisk({ risk_score: 91, confidence: 0.95, response_time_ms: 44 }));

    expect(screen.getByRole("status")).toHaveTextContent(/İşlem Reddedildi/);
  });

  it("does not let a previous payment's reset timer clobber a new payment", () => {
    const { container } = render(<Demo />);
    const form = container.querySelector("form");

    fireEvent.submit(form);
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole("status")).toHaveTextContent(/başarıyla alındı/);

    // Submit again while the first run's 2.5s "back to idle" timer is pending.
    fireEvent.submit(form);
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole("status")).toHaveTextContent(/başarıyla alındı/);

    // The orphaned timer from run 1 would fire here and blank the message
    // 1.1s early.
    act(() => vi.advanceTimersByTime(1600));
    expect(screen.getByRole("status")).toHaveTextContent(/başarıyla alındı/);
  });
});
