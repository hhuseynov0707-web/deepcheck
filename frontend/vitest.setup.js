import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest does not auto-clean between tests the way some runners do; without
// this, mounted trees leak into the next test and duplicate-element queries
// start failing for reasons that have nothing to do with the test.
afterEach(cleanup);
