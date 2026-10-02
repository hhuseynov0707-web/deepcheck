import * as d3 from "d3";
import { useEffect, useMemo, useRef, useState } from "react";

import RiskBadge from "./RiskBadge.jsx";
import { ink, line as lineToken, monoFontStack, surface } from "../designTokens.js";
import { prefersReducedMotion } from "../hooks/useAnimatedNumber.js";
import { RISK_LEVELS, riskLevelFor } from "./riskLevels.js";

const MARGIN = { top: 14, right: 16, bottom: 28, left: 34 };
const TRANSITION_MS = 500;

// The redraw animation, 0 for someone who asked their system for less motion.
// index.css's prefers-reduced-motion rule only reaches CSS animations and
// transitions; d3-transition runs on timers, so without this the line and the
// dots kept sliding on every poll (every 2-3 s under live follow). Read at each
// redraw, so a preference changed mid-demo applies from the next window.
function redrawDuration() {
  return prefersReducedMotion() ? 0 : TRANSITION_MS;
}

// The y axis IS the decision ladder: 40, 60 and 80 are the numbers the server
// turns on in POST /api/decision, so they are the ticks, not a generic 0-20-40.
const Y_TICKS = [0, 40, 60, 80, 100];

// Opacity climbs with severity. At one flat value the amber, orange and rose
// bands wash into a single brown block and the top of the chart stops meaning
// anything; graded, the dangerous end is visibly the hot end.
const BAND_OPACITY = [0.07, 0.09, 0.11, 0.15];

// The charts used to be fixed at 600 x 260, which at 390px wide meant the
// right-hand third of the score history was simply off the card. They now draw
// at whatever width their container has.
function useMeasuredWidth(ref, fallback) {
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;

    const apply = () => {
      const next = Math.round(element.clientWidth);
      // jsdom and a hidden container both report 0; the fallback keeps the
      // chart drawable instead of collapsing it to nothing.
      if (next > 0) setWidth(next);
    };

    apply();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, fallback]);

  return width;
}

function formatClock(date) {
  return date.toLocaleTimeString("tr-TR");
}

// The four published bands, spelled out under the chart. The background wash
// alone says "something changes here" without saying what, and a juror should
// not have to infer the ladder from four shades of a dark panel.
export function RiskBandLegend({ className = "" }) {
  return (
    <ul className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 ${className}`}>
      {RISK_LEVELS.map((level, index) => {
        const from = index === 0 ? 0 : RISK_LEVELS[index - 1].upperBound;
        const to = Number.isFinite(level.upperBound) ? level.upperBound : 100;
        return (
          <li key={level.key} className="flex items-center gap-1.5">
            <level.Icon className={`h-3.5 w-3.5 shrink-0 ${level.text}`} />
            <span className={`text-caption font-medium ${level.text}`}>{level.label}</span>
            <span className="num text-caption text-ink-faint">
              {from}-{to}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// The session's score over time, as the server computed it for each behaviour
// window. Display only: nothing here recomputes a score or a threshold, it
// redraws the rows GET /api/score/{id} returned in `history`.
export default function RiskChart({ history = [], height: fixedHeight }) {
  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const stateRef = useRef(null);
  const width = useMeasuredWidth(containerRef, 640);
  const height = fixedHeight ?? (width < 560 ? 240 : 330);
  // Which point the pointer or the arrow keys are on. null = none, and the
  // readout falls back to the newest measurement.
  const [activeIndex, setActiveIndex] = useState(null);

  const data = useMemo(
    () =>
      history
        .map((row) => ({ time: new Date(row.timestamp), score: Number(row.risk_score) }))
        .filter((d) => Number.isFinite(d.score) && !Number.isNaN(d.time.getTime())),
    [history],
  );

  const hasData = data.length > 0;

  // Stop D3's transitions when the SVG goes away. Under the SOC panel's live
  // follow the chart unmounts on every switch to a new session, and D3 kept
  // animating the detached nodes for TRANSITION_MS -- frames for a chart nobody
  // can see. The node is captured here because React clears the ref before
  // this cleanup runs. `hasData` because the SVG only exists while it is true.
  useEffect(() => {
    const node = svgRef.current;
    if (!node) return undefined;
    return () => {
      const svg = d3.select(node);
      svg.interrupt();
      svg.selectAll("*").interrupt();
    };
  }, [hasData]);

  // Build the static skeleton once (or when the chart is resized). `hasData` is
  // a dependency because the SVG is not in the DOM at all while the session has
  // no history: without it the first history to arrive found a skeleton built
  // over a null node and drew nothing, for good.
  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const innerWidth = Math.max(width - MARGIN.left - MARGIN.right, 10);
    const innerHeight = Math.max(height - MARGIN.top - MARGIN.bottom, 10);

    svg.attr("width", width).attr("height", height);

    const g = svg.append("g").attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    const y = d3.scaleLinear().domain([0, 100]).range([innerHeight, 0]);

    // The four published score bands, straight from riskLevels.js, so the
    // background of this chart can never disagree with the badge above it.
    const bands = RISK_LEVELS.map((level, index) => ({
      from: index === 0 ? 0 : RISK_LEVELS[index - 1].upperBound,
      to: Number.isFinite(level.upperBound) ? level.upperBound : 100,
      color: level.hex,
      opacity: BAND_OPACITY[index],
    }));

    g.append("g")
      .attr("class", "bands")
      .selectAll("rect")
      .data(bands)
      .enter()
      .append("rect")
      .attr("x", 0)
      .attr("y", (d) => y(d.to))
      .attr("width", innerWidth)
      .attr("height", (d) => y(d.from) - y(d.to))
      .attr("fill", (d) => d.color)
      .attr("opacity", (d) => d.opacity);

    // The band edges themselves: 40, 60, 80 are the numbers the server's ladder
    // turns on, so they are drawn rather than left implicit in a colour change.
    g.append("g")
      .attr("class", "thresholds")
      .selectAll("line")
      .data([40, 60, 80])
      .enter()
      .append("line")
      .attr("x1", 0)
      .attr("x2", innerWidth)
      .attr("y1", (d) => y(d))
      .attr("y2", (d) => y(d))
      .attr("stroke", lineToken.strong)
      .attr("stroke-dasharray", "2 4");

    const xAxisG = g.append("g").attr("class", "x-axis").attr("transform", `translate(0,${innerHeight})`);
    const yAxisG = g.append("g").attr("class", "y-axis");

    const path = g
      .append("path")
      .attr("class", "line-path")
      .attr("fill", "none")
      .attr("stroke", ink.DEFAULT)
      .attr("stroke-width", 1.75)
      .attr("stroke-linecap", "round")
      .attr("stroke-linejoin", "round");

    const dotsGroup = g.append("g").attr("class", "dots");

    // Hover furniture, hidden until a pointer or an arrow key picks a point.
    const guide = g
      .append("line")
      .attr("class", "guide")
      .attr("stroke", ink.faint)
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "3 3")
      .attr("y1", 0)
      .attr("y2", innerHeight)
      .attr("opacity", 0);

    const focus = g
      .append("circle")
      .attr("class", "focus")
      .attr("r", 5)
      .attr("stroke", surface.panel)
      .attr("stroke-width", 2)
      .attr("opacity", 0);

    // Last, so it is on top of every mark and catches the pointer everywhere
    // inside the plot rather than only exactly on a 2.75px dot.
    const overlay = g
      .append("rect")
      .attr("class", "overlay")
      .attr("width", innerWidth)
      .attr("height", innerHeight)
      .attr("fill", "transparent")
      .style("cursor", "crosshair");

    stateRef.current = { svg, xAxisG, yAxisG, path, dotsGroup, guide, focus, overlay, innerWidth, innerHeight, y };
  }, [width, height, hasData]);

  // Draw the data. Depends on the geometry as well as on `history`: the static
  // effect above wipes the SVG whenever the container is re-measured, and
  // without these deps the bars and the line stayed blank until the next poll
  // happened to hand over a new array.
  useEffect(() => {
    const state = stateRef.current;
    if (!state || data.length === 0) return;
    const { svg, xAxisG, yAxisG, path, dotsGroup, overlay, innerWidth, y } = state;

    // A single window has no time span: d3 would map it to the middle of the
    // axis and print one tick. Give it a minute either side so the point sits
    // in a readable axis instead of on a degenerate one.
    const [first, last] = d3.extent(data, (d) => d.time);
    const domain =
      +first === +last ? [new Date(+first - 60_000), new Date(+last + 60_000)] : [first, last];

    const x = d3.scaleTime().domain(domain).range([0, innerWidth]);
    state.x = x;

    const t = svg.transition().duration(redrawDuration()).ease(d3.easeCubicOut);
    const xTicks = innerWidth < 380 ? 3 : innerWidth < 700 ? 5 : 7;

    xAxisG.transition(t).call(d3.axisBottom(x).ticks(xTicks).tickFormat(d3.timeFormat("%H:%M:%S")));
    yAxisG.transition(t).call(d3.axisLeft(y).tickValues(Y_TICKS).tickSize(0).tickPadding(8));
    svg
      .selectAll(".x-axis text, .y-axis text")
      .attr("fill", ink.faint)
      .style("font-family", monoFontStack)
      .style("font-size", "10px");
    svg.selectAll(".domain").attr("stroke", lineToken.DEFAULT);
    svg.selectAll(".tick line").attr("stroke", lineToken.DEFAULT);

    const lineGenerator = d3
      .line()
      .x((d) => x(d.time))
      .y((d) => y(d.score))
      .curve(d3.curveMonotoneX);

    path.datum(data).transition(t).attr("d", lineGenerator);

    const dots = dotsGroup.selectAll("circle").data(data, (d) => d.time.getTime());

    dots.exit().transition(t).attr("r", 0).remove();

    // Each point is coloured by the band its own score falls in, so the moment
    // a session crosses 40, 60 or 80 is visible on the point itself and not
    // only in the background wash behind it. Dense histories get smaller dots
    // so 200 windows read as a line rather than as a caterpillar.
    const radius = data.length > 90 ? 1.6 : data.length > 40 ? 2.2 : 3;
    dots
      .enter()
      .append("circle")
      .attr("r", 0)
      .attr("stroke", surface.panel)
      .attr("stroke-width", 1.25)
      .attr("cx", (d) => x(d.time))
      .attr("cy", (d) => y(d.score))
      .merge(dots)
      .attr("fill", (d) => riskLevelFor(d.score).hex)
      .transition(t)
      .attr("cx", (d) => x(d.time))
      .attr("cy", (d) => y(d.score))
      .attr("r", radius);

    const times = data.map((d) => +d.time);
    overlay
      .on("pointermove", (event) => {
        const [mx] = d3.pointer(event);
        setActiveIndex(d3.bisectCenter(times, +x.invert(mx)));
      })
      .on("pointerleave", () => setActiveIndex(null));
  }, [data, width, height]);

  // Move the guide and the focus dot to whatever the pointer or the keyboard
  // picked. Declared after the draw effect so `state.x` already exists.
  useEffect(() => {
    const state = stateRef.current;
    if (!state?.x) return;
    const { guide, focus, x, y } = state;
    const point = activeIndex == null ? null : data[activeIndex];

    if (!point) {
      guide.attr("opacity", 0);
      focus.attr("opacity", 0);
      return;
    }
    guide.attr("opacity", 1).attr("x1", x(point.time)).attr("x2", x(point.time));
    focus
      .attr("opacity", 1)
      .attr("cx", x(point.time))
      .attr("cy", y(point.score))
      .attr("fill", riskLevelFor(point.score).hex);
  }, [activeIndex, data, width, height]);

  if (data.length === 0) return null;

  const readoutIndex = activeIndex == null ? data.length - 1 : activeIndex;
  const readout = data[readoutIndex];
  const spoken = history.slice(-12);

  function onKeyDown(event) {
    const step = { ArrowRight: 1, ArrowLeft: -1, ArrowUp: 1, ArrowDown: -1 }[event.key];
    if (step) {
      event.preventDefault();
      setActiveIndex((prev) => {
        const next = (prev == null ? data.length - 1 : prev) + step;
        return Math.min(Math.max(next, 0), data.length - 1);
      });
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(data.length - 1);
    } else if (event.key === "Escape") {
      setActiveIndex(null);
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="eyebrow">{activeIndex == null ? "Son ölçüm" : "Seçili ölçüm"}</p>
        <div className="flex items-center gap-2.5">
          <span className="num text-caption text-ink-faint">{formatClock(readout.time)}</span>
          <RiskBadge riskScore={readout.score} live={false} animate={false} />
        </div>
      </div>

      {/* Focusable so the readout is reachable without a mouse: arrow keys walk
          the windows, Home/End jump to the ends, Escape lets go. The picture
          itself is aria-hidden -- a screen reader gets the list of values
          below, which is the same data in words. */}
      <div
        ref={containerRef}
        tabIndex={0}
        role="group"
        aria-label={`Risk skoru geçmişi, ${data.length} davranış penceresi. Ok tuşlarıyla ölçümler arasında gezinin.`}
        onKeyDown={onKeyDown}
        onBlur={() => setActiveIndex(null)}
        className="w-full rounded-field"
      >
        <svg ref={svgRef} className="block w-full overflow-visible" aria-hidden="true" focusable="false" />
      </div>

      <RiskBandLegend className="mt-3 border-t border-line pt-3" />

      {/* An SVG polyline says nothing to a screen reader. The same numbers, in
          words: only values the API actually sent, newest last. */}
      <ul className="sr-only">
        {spoken.map((point) => (
          <li key={point.timestamp}>
            {formatClock(new Date(point.timestamp))}: risk skoru {Number(point.risk_score).toFixed(1)}
          </li>
        ))}
      </ul>
    </div>
  );
}
