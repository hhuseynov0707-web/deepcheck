import { accent, ink, line, risk, surface, synthetic } from "./src/designTokens.js";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    // Everything below EXTENDS the default theme rather than replacing it, so
    // the stock palette and scale stay available while the semantic tokens are
    // the ones the product is supposed to use.
    extend: {
      colors: {
        canvas: { DEFAULT: surface.canvas, sunken: surface.sunken },
        panel: { DEFAULT: surface.panel, inset: surface.inset, raised: surface.raised },
        line,
        ink,
        accent,
        risk,
        synthetic,
      },

      fontFamily: {
        // No webfont: the jury demo has to render identically on a laptop with
        // no network, and a font that fails to load is a layout that shifts in
        // front of the audience.
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "JetBrains Mono",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },

      // A named scale, so a heading is chosen by role instead of by guessing a
      // number. Sizes are the ones actually used; the stock scale stays.
      fontSize: {
        eyebrow: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.09em" }],
        caption: ["0.75rem", { lineHeight: "1.125rem" }],
        body: ["0.875rem", { lineHeight: "1.375rem" }],
        lead: ["1rem", { lineHeight: "1.5rem" }],
        h3: ["1.0625rem", { lineHeight: "1.5rem", letterSpacing: "-0.01em" }],
        h2: ["1.25rem", { lineHeight: "1.625rem", letterSpacing: "-0.015em" }],
        h1: ["1.75rem", { lineHeight: "2.125rem", letterSpacing: "-0.022em" }],
        display: ["2.25rem", { lineHeight: "2.5rem", letterSpacing: "-0.028em" }],
        // Figures that carry the page: metric cards, the order total, a score.
        metric: ["2rem", { lineHeight: "2.25rem", letterSpacing: "-0.02em" }],
        "metric-lg": ["2.75rem", { lineHeight: "3rem", letterSpacing: "-0.025em" }],
      },

      borderRadius: {
        field: "0.625rem",
        card: "0.875rem",
        panel: "1.125rem",
      },

      boxShadow: {
        // One elevation treatment for the whole product: a hairline of light on
        // the top edge plus a soft drop. Depth is never signalled by a second,
        // different shadow.
        panel: "inset 0 1px 0 0 rgb(255 255 255 / 0.04), 0 16px 40px -24px rgb(0 0 0 / 0.9)",
        overlay: "inset 0 1px 0 0 rgb(255 255 255 / 0.06), 0 32px 64px -24px rgb(0 0 0 / 0.95)",
      },

      ringWidth: { DEFAULT: "2px" },
      ringColor: { DEFAULT: accent.DEFAULT },
      ringOffsetColor: { DEFAULT: surface.canvas },

      transitionDuration: { DEFAULT: "200ms" },

      keyframes: {
        // Motion that explains a state change, and nothing else.
        "fade-rise": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(1)", opacity: "0.55" },
          "70%, 100%": { transform: "scale(2.4)", opacity: "0" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-rise": "fade-rise 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "pulse-ring": "pulse-ring 2s cubic-bezier(0, 0, 0.2, 1) infinite",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
};
