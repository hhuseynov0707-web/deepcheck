import { accent, danger, ink, line, risk, status, surface, synthetic } from "./src/designTokens.js";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    // Extends the default theme, so the stock palette stays available while
    // the semantic tokens are the ones the SOC is supposed to use.
    extend: {
      colors: {
        canvas: { DEFAULT: surface.canvas, sunken: surface.sunken },
        panel: { DEFAULT: surface.panel, inset: surface.inset, raised: surface.raised },
        line,
        ink,
        accent,
        status,
        danger,
        risk,
        synthetic,
      },

      fontFamily: {
        // IBM Plex, bundled from @fontsource (src/main.jsx): the SOC must
        // render the same on a projector laptop with no network, so nothing
        // is fetched from a font CDN. The system stack behind it only covers
        // the moment before the bundled files are read.
        sans: [
          '"IBM Plex Sans"',
          "ui-sans-serif",
          "system-ui",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          '"IBM Plex Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },

      // A named scale, so a heading is chosen by role. Denser than the legacy
      // demo's: the SOC is read on one screen, at a glance, by an analyst.
      fontSize: {
        eyebrow: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.08em" }],
        caption: ["0.75rem", { lineHeight: "1.125rem" }],
        body: ["0.875rem", { lineHeight: "1.375rem" }],
        lead: ["1rem", { lineHeight: "1.5rem" }],
        h3: ["1rem", { lineHeight: "1.5rem", letterSpacing: "-0.005em" }],
        h2: ["1.1875rem", { lineHeight: "1.625rem", letterSpacing: "-0.01em" }],
        h1: ["1.5rem", { lineHeight: "2rem", letterSpacing: "-0.015em" }],
        metric: ["1.875rem", { lineHeight: "2.125rem", letterSpacing: "-0.02em" }],
        "metric-lg": ["2.5rem", { lineHeight: "2.75rem", letterSpacing: "-0.025em" }],
      },

      borderRadius: {
        field: "0.5rem",
        card: "0.75rem",
        panel: "1rem",
      },

      boxShadow: {
        // One elevation for the whole SOC: a hairline of light on the top edge
        // and a soft drop. Depth is never signalled by a second shadow.
        panel: "inset 0 1px 0 0 rgb(255 255 255 / 0.035), 0 12px 32px -20px rgb(2 6 23 / 0.85)",
        overlay: "inset 0 1px 0 0 rgb(255 255 255 / 0.05), 0 24px 56px -24px rgb(2 6 23 / 0.95)",
      },

      ringWidth: { DEFAULT: "2px" },
      ringColor: { DEFAULT: accent.DEFAULT },
      ringOffsetColor: { DEFAULT: surface.canvas },

      // 150-250 ms: motion that explains a change, never decoration.
      transitionDuration: { DEFAULT: "180ms" },

      keyframes: {
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
