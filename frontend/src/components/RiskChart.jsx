import * as d3 from "d3";
import { useEffect, useRef } from "react";

const MARGIN = { top: 20, right: 20, bottom: 30, left: 40 };
const TRANSITION_MS = 500;

export default function RiskChart({ history = [], width = 600, height = 260 }) {
  const svgRef = useRef(null);
  const stateRef = useRef(null);

  // Build the static skeleton once (or when the chart is resized)
  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const innerWidth = width - MARGIN.left - MARGIN.right;
    const innerHeight = height - MARGIN.top - MARGIN.bottom;

    svg.attr("width", width).attr("height", height);

    const g = svg.append("g").attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    const y = d3.scaleLinear().domain([0, 100]).range([innerHeight, 0]);

    const bands = [
      { from: 0, to: 40, color: "#10b981" },
      { from: 40, to: 60, color: "#f59e0b" },
      { from: 60, to: 80, color: "#f97316" },
      { from: 80, to: 100, color: "#f43f5e" },
    ];
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
      .attr("opacity", 0.08);

    const xAxisG = g.append("g").attr("class", "x-axis").attr("transform", `translate(0,${innerHeight})`);
    const yAxisG = g.append("g").attr("class", "y-axis");

    const path = g
      .append("path")
      .attr("class", "line-path")
      .attr("fill", "none")
      .attr("stroke", "#f4f4f5")
      .attr("stroke-width", 2);

    const dotsGroup = g.append("g").attr("class", "dots");

    stateRef.current = { svg, xAxisG, yAxisG, path, dotsGroup, innerWidth, innerHeight, y };
  }, [width, height]);

  // Update data with smooth transitions instead of a full redraw
  useEffect(() => {
    const state = stateRef.current;
    if (!state || history.length === 0) return;
    const { svg, xAxisG, yAxisG, path, dotsGroup, innerWidth, y } = state;

    const data = history.map((d) => ({ time: new Date(d.timestamp), score: d.risk_score }));

    const x = d3
      .scaleTime()
      .domain(d3.extent(data, (d) => d.time))
      .range([0, innerWidth]);

    const t = svg.transition().duration(TRANSITION_MS).ease(d3.easeCubicOut);

    xAxisG.transition(t).call(d3.axisBottom(x).ticks(5).tickFormat(d3.timeFormat("%H:%M:%S")));
    yAxisG.transition(t).call(d3.axisLeft(y).ticks(5));
    xAxisG.selectAll("text").attr("fill", "#a1a1aa").style("font-family", "monospace").style("font-size", "11px");
    yAxisG.selectAll("text").attr("fill", "#a1a1aa").style("font-family", "monospace").style("font-size", "11px");
    svg.selectAll(".domain, .tick line").attr("stroke", "#27272a");

    const line = d3
      .line()
      .x((d) => x(d.time))
      .y((d) => y(d.score))
      .curve(d3.curveMonotoneX);

    path.datum(data).transition(t).attr("d", line);

    const dots = dotsGroup.selectAll("circle").data(data, (d) => d.time.getTime());

    dots.exit().transition(t).attr("r", 0).remove();

    dots
      .enter()
      .append("circle")
      .attr("r", 0)
      .attr("fill", "#10b981")
      .attr("cx", (d) => x(d.time))
      .attr("cy", (d) => y(d.score))
      .merge(dots)
      .transition(t)
      .attr("cx", (d) => x(d.time))
      .attr("cy", (d) => y(d.score))
      .attr("r", 3);
  }, [history]);

  return <svg ref={svgRef} className="w-full" />;
}

export function ShapBarChart({ shapExplanation = [], width = 400, height = 160 }) {
  const svgRef = useRef(null);
  const stateRef = useRef(null);

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const margin = { top: 10, right: 30, bottom: 10, left: 130 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    svg.attr("width", width).attr("height", height);
    const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    const yAxisG = g.append("g").attr("class", "y-axis");
    const barsGroup = g.append("g").attr("class", "bars");
    const labelsGroup = g.append("g").attr("class", "labels");

    stateRef.current = { svg, yAxisG, barsGroup, labelsGroup, innerWidth, innerHeight };
  }, [width, height]);

  useEffect(() => {
    const state = stateRef.current;
    if (!state || shapExplanation.length === 0) return;
    const { svg, yAxisG, barsGroup, labelsGroup, innerWidth, innerHeight } = state;

    const data = [...shapExplanation].sort((a, b) => b.impact - a.impact);

    const x = d3
      .scaleLinear()
      .domain([0, d3.max(data, (d) => d.impact) || 1])
      .range([0, innerWidth]);

    const y = d3
      .scaleBand()
      .domain(data.map((d) => d.feature))
      .range([0, innerHeight])
      .padding(0.3);

    const t = svg.transition().duration(TRANSITION_MS).ease(d3.easeCubicOut);

    yAxisG.transition(t).call(d3.axisLeft(y));
    yAxisG.selectAll("text").attr("fill", "#a1a1aa").style("font-family", "monospace").style("font-size", "11px");
    svg.selectAll(".domain, .tick line").attr("stroke", "#27272a");

    const bars = barsGroup.selectAll("rect").data(data, (d) => d.feature);

    bars.exit().transition(t).attr("width", 0).remove();

    bars
      .enter()
      .append("rect")
      .attr("x", 0)
      .attr("width", 0)
      .attr("rx", 3)
      .attr("fill", "#f59e0b")
      .attr("y", (d) => y(d.feature))
      .attr("height", y.bandwidth())
      .merge(bars)
      .transition(t)
      .attr("y", (d) => y(d.feature))
      .attr("height", y.bandwidth())
      .attr("width", (d) => Math.max(x(d.impact), 1));

    const labels = labelsGroup.selectAll("text").data(data, (d) => d.feature);

    labels.exit().transition(t).style("opacity", 0).remove();

    labels
      .enter()
      .append("text")
      .attr("fill", "#e4e4e7")
      .style("font-family", "monospace")
      .style("font-size", "11px")
      .style("opacity", 0)
      .attr("y", (d) => y(d.feature) + y.bandwidth() / 2)
      .attr("dy", "0.35em")
      .merge(labels)
      .transition(t)
      .style("opacity", 1)
      .attr("x", (d) => x(d.impact) + 6)
      .attr("y", (d) => y(d.feature) + y.bandwidth() / 2)
      .textTween(function (d) {
        const previous = parseFloat(this.textContent) || 0;
        const interpolator = d3.interpolateNumber(previous, d.impact);
        return (tt) => `${interpolator(tt).toFixed(1)}%`;
      });
  }, [shapExplanation]);

  return <svg ref={svgRef} className="w-full" />;
}
