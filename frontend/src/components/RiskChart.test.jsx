import { render, waitFor } from "@testing-library/react";
import * as d3 from "d3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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

describe("RiskChart", () => {
  let uncaught;
  const onError = (event) => uncaught.push(event.error ?? event.message);

  beforeEach(() => {
    uncaught = [];
    window.addEventListener("error", onError);
  });

  afterEach(() => {
    window.removeEventListener("error", onError);
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
    // The plot itself, not one of the badge or legend icons.
    const svg = container.querySelector('[role="group"] > svg');
    // d3 keeps scheduled transitions on the node as __transition.
    expect(svg.__transition).toBeDefined();

    unmount();

    // Live follow remounts the chart on every switch; nothing may keep
    // animating the detached nodes.
    const animating = [svg, ...svg.querySelectorAll("*")].filter((node) => node.__transition);
    expect(animating).toHaveLength(0);
  });
});
