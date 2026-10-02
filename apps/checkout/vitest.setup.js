import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest does not unmount between tests on its own; a tree left mounted leaks
// into the next test and duplicate-element queries fail for unrelated reasons.
afterEach(cleanup);
