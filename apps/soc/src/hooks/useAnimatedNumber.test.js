import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import useAnimatedNumber from "./useAnimatedNumber.js";

describe("useAnimatedNumber", () => {
  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("continues from the value on screen when the target changes mid-animation", () => {
    const { result, rerender } = renderHook(({ target }) => useAnimatedNumber(target, 1000), {
      initialProps: { target: 0 },
    });

    rerender({ target: 100 });
    act(() => {
      vi.advanceTimersByTime(500);
    });

    const onScreen = result.current;
    expect(onScreen).toBeGreaterThan(0);
    expect(onScreen).toBeLessThan(100);

    // A new score arrives before the first animation finished. The dashboard
    // polls every 3s and animates for 600ms, so this is the common case, not
    // an edge case.
    rerender({ target: 50 });
    act(() => {
      vi.advanceTimersByTime(16);
    });

    // Moving toward 50 from 87.5 means going down. Any value above where we
    // already were is a visible jump backwards.
    expect(result.current).toBeLessThanOrEqual(onScreen);
  });
});
