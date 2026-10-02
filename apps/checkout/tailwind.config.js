/** @type {import('tailwindcss').Config} */

// The store's palette. The ui-ux-pro-max skill had no verified match for a
// LIGHT fintech checkout (its fintech entries are dark), so this is a general
// fintech fallback, chosen for trust and contrast rather than taken from the
// skill's data:
//   brand  #1D4ED8 on white  ~6.7:1   (button text, links)
//   ink    #0B1F3A on canvas ~15:1
//   muted  #5B6B82 on white  ~5.4:1, on canvas ~5.0:1 (secondary text, AA)
//   danger #B91C1C on white  ~6.5:1, success #15803D ~5.0:1
//   control border #7C8AA0 on white ~3.5:1 (WCAG 1.4.11 needs 3:1 for a
//   field's boundary; the #E3E8EF hairline is for decoration only)
// Ratios are computed from the sRGB values, not measured on a screen.
const palette = {
  brand: { DEFAULT: "#1D4ED8", hover: "#1E40AF", ring: "#2563EB", soft: "#EEF3FF", line: "#C7D5FB" },
  ink: { DEFAULT: "#0B1F3A", muted: "#5B6B82" },
  canvas: "#F5F7FB",
  card: "#FFFFFF",
  line: { DEFAULT: "#E3E8EF", control: "#7C8AA0" },
  success: { DEFAULT: "#15803D", soft: "#ECFDF3" },
  danger: { DEFAULT: "#B91C1C", soft: "#FEF2F2", line: "#F5C2C2" },
};

export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: palette,
      fontFamily: {
        // Bundled with @fontsource (src/main.jsx): the demo must render the
        // same on a laptop with no network, so no font CDN.
        sans: ['"IBM Plex Sans"', "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Arial", "sans-serif"],
      },
      borderRadius: { field: "0.625rem", card: "1rem" },
      boxShadow: {
        card: "0 1px 2px rgb(11 31 58 / 0.04), 0 8px 24px -12px rgb(11 31 58 / 0.12)",
        dialog: "0 24px 64px -16px rgb(11 31 58 / 0.35)",
        button: "0 1px 2px rgb(11 31 58 / 0.18), inset 0 1px 0 rgb(255 255 255 / 0.14)",
      },
      transitionDuration: { DEFAULT: "200ms" },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(6px) scale(0.99)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
      },
      animation: {
        "fade-in": "fade-in 200ms ease-out both",
        "rise-in": "rise-in 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};
