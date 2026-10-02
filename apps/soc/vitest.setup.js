import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest does not auto-clean between tests the way some runners do; without
// this, mounted trees leak into the next test and duplicate-element queries
// start failing for reasons that have nothing to do with the test.
afterEach(cleanup);

// jsdom implements no SVG transform lists: an SVG <g> has no `.transform`,
// where every browser has one. D3 reads it to interpolate a "transform"
// attribute (d3-interpolate parseSvg calls node.transform.baseVal.consolidate()).
// RiskChart's time axis does exactly that whenever a new window moves its
// ticks, so any test that re-polls a mounted chart threw a TypeError from
// inside a requestAnimationFrame callback -- reported by vitest as an uncaught
// error, after the test itself had passed, and failing the run. The chart is
// right and jsdom is incomplete, so the gap is filled here and not in the
// component. Only translate(x[, y]) is understood because that is the only
// form d3-axis writes; anything else consolidates to null, which is what an
// empty list returns in a browser and which D3 reads as the identity.
const TRANSLATE = /^\s*translate\(\s*([-+]?[\d.]+(?:e[-+]?\d+)?)(?:[\s,]+([-+]?[\d.]+(?:e[-+]?\d+)?))?\s*\)\s*$/i;

if (typeof SVGElement !== "undefined" && !("transform" in SVGElement.prototype)) {
  Object.defineProperty(SVGElement.prototype, "transform", {
    configurable: true,
    get() {
      const node = this;
      return {
        baseVal: {
          consolidate() {
            const match = TRANSLATE.exec(node.getAttribute("transform") ?? "");
            if (!match) return null;
            const [, x, y = "0"] = match;
            return { matrix: { a: 1, b: 0, c: 0, d: 1, e: Number(x), f: Number(y) } };
          },
        },
      };
    },
  });
}
