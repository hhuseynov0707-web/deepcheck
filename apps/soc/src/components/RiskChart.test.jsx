import { render, waitFor } from "@testing-library/react";
import * as d3 from "d3";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RiskChart from "./RiskChart.jsx";

// The SOC panel re-polls the selected session every few seconds, so the chart
// is mostly redrawn in place: same SVG, one more window, a wider time axis.
// These tests drive that path with real timers and let D3's transitions run.
const T0 = Date.UTC(2026, 8, 16, 10, 7, 0);
const rows = (count) =>
  Array.from({ length: count }, (_, i) => ({
    timestamp: new Date(T0 + i * 2000).toISOString(),
    risk_score: 30 + i * 10,
  }));

const translateX = (tick) => Number(/translate\(([^,)]+)/.exec(tick.getAttribute("transform"))?.[1]);

// The plot itself, not one of the badge or legend icons.
const plot = (container) => container.querySelector('[role="group"] > svg');

// The durations of the transitions d3 has scheduled on a node (d3 keeps them
// on the node as __transition, keyed by transition id).
const scheduledDurations = (node) => Object.values(node.__transition ?? {}).map((schedule) => schedule.duration);

function stubMatchMedia(matches) {
  vi.stubGlobal("matchMedia", (query) => ({
    matches: matches(query),
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  }));
}

describe("RiskChart", () => {
  let uncaught;
  const onError = (event) => uncaught.push(event.error ?? event.message);

  beforeEach(() => {
    uncaught = [];
    window.addEventListener("error", onError);
  });

  afterEach(() => {
    window.removeEventListener("error", onError);
    vi.unstubAllGlobals();
  });

  it("moves the time axis to the new window range when a window arrives", async () => {
    const { container, rerender } = render(<RiskChart history={rows(2)} />);
    rerender(<RiskChart history={rows(3)} />);

    const innerWidth = Number(container.querySelector(".overlay").getAttribute("width"));
    const x = d3
      .scaleTime()
      .domain([new Date(T0), new Date(T0 + 4000)])
      .range([0, innerWidth]);

    // Every tick ends where the three-window scale puts it, i.e. the move was
    // interpolated to the end rather than abandoned at the old positions.
    await waitFor(
      () => {
        const ticks = [...container.querySelectorAll(".x-axis .tick")];
        expect(ticks.length).toBeGreaterThan(1);
        for (const tick of ticks) {
          expect(Math.abs(translateX(tick) - x(tick.__data__))).toBeLessThanOrEqual(0.5);
        }
      },
      { timeout: 3000 },
    );
    expect(container.querySelectorAll(".dots circle")).toHaveLength(3);
    expect(uncaught).toEqual([]);
  });

  it("stops its transitions when it unmounts mid-animation", () => {
    const { container, rerender, unmount } = render(<RiskChart history={rows(2)} />);
    rerender(<RiskChart history={rows(3)} />);
    const svg = plot(container);
    // d3 keeps scheduled transitions on the node as __transition.
    expect(svg.__transition).toBeDefined();

    unmount();

    // Live follow remounts the chart on every switch; nothing may keep
    // animating the detached nodes.
    const animating = [svg, ...svg.querySelectorAll("*")].filter((node) => node.__transition);
    expect(animating).toHaveLength(0);
  });

  it("redraws without animation for someone who prefers reduced motion", async () => {
    // index.css's reduced-motion rule cannot stop d3-transition, which runs on
    // timers: the chart has to ask for itself, like useAnimatedNumber does.
    stubMatchMedia((query) => query === "(prefers-reduced-motion: reduce)");
    const { container, rerender } = render(<RiskChart history={rows(2)} />);
    rerender(<RiskChart history={rows(3)} />);

    const durations = scheduledDurations(plot(container));
    expect(durations.length).toBeGreaterThan(0);
    expect(durations.every((duration) => duration === 0)).toBe(true);
    // Still drawn: no motion is not no update.
    await waitFor(() => expect(container.querySelectorAll(".dots circle")).toHaveLength(3));
    expect(uncaught).toEqual([]);
  });

  it("keeps the 500 ms redraw when no preference is expressed, or matchMedia throws", () => {
    stubMatchMedia(() => false);
    const quiet = render(<RiskChart history={rows(2)} />);
    quiet.rerender(<RiskChart history={rows(3)} />);
    expect(scheduledDurations(plot(quiet.container))).toContain(500);
    expect(scheduledDurations(plot(quiet.container))).not.toContain(0);
    quiet.unmount();

    // A hardened browser can throw from matchMedia: that is "no preference",
    // not a crash.
    vi.stubGlobal("matchMedia", () => {
      throw new Error("blocked");
    });
    const hardened = render(<RiskChart history={rows(2)} />);
    hardened.rerender(<RiskChart history={rows(3)} />);
    expect(scheduledDurations(plot(hardened.container))).toContain(500);
  });
});
