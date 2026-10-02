// The single source of the SOC's raw colour values.
//
// Tailwind's theme (tailwind.config.js) is built from this file and the D3
// chart imports the same object, so a colour exists exactly once.
//
// Where the values come from. The surfaces, borders and text greys are the
// dark "security operations" palette the ui-ux-pro-max design search returned
// for this product (background #0F172A, card #1B2336, muted #272F42, border
// #334155 / #475569, foreground #F8FAFC, muted text #94A3B8, status green
// #22C55E, destructive #EF4444). The four risk colours and the synthetic
// violet are NOT from that search: they are the former demo's
// (frontend/src/designTokens.js, removed 2026-10-02), kept unchanged so a band
// reads in the same colour on every screen this project has shown the jury.
//
// Contrast, WCAG 2.1 relative luminance, measured with a scratch script on
// these exact values (not estimated): the lowest text pair is `ink.faint` on
// `panel.raised` at 5.21:1; `accent` text on its own 10% tint over
// `panel.raised` -- a "Canlı" mark on a selected card -- is 4.89:1; the
// lowest risk colour is `blocked` on `panel.raised` at 4.96:1; `danger` text is
// 4.83:1 on `panel.raised`. All clear 4.5:1. `line.control`, the boundary that
// makes a field identifiable as a field, is 3.06:1 on `panel.raised` and
// 3.59:1 on `panel` (WCAG 1.4.11 asks 3:1). `line` and `line.strong` are
// decorative separators: nothing is identified by them alone.

// Dark surface scale. `canvas` is the page, `panel` a card on it,
// `inset`/`sunken` wells inside a card, `raised` hover and selected state.
export const surface = {
  sunken: "#0B1222",
  canvas: "#0F172A",
  panel: "#1B2336",
  inset: "#151D2E",
  raised: "#272F42",
};

export const line = {
  DEFAULT: "#334155", // decorative: card edges, dividers
  strong: "#475569", // decorative: hover edges, strong separators
  control: "#6B7A90", // >= 3:1 on every surface: field and button boundaries
};

export const ink = {
  DEFAULT: "#F8FAFC", // headings, primary values
  muted: "#CBD5E1", // body copy, labels
  faint: "#94A3B8", // small print, captions, axis ticks
  inverse: "#0B1222", // text on a solid accent fill
};

// One accent for every interactive or informational affordance: focus rings,
// the primary button, the "Canlı" and "Yeni" marks, explanatory bars. Blue,
// deliberately, as in the former demo: green, amber, orange and rose belong to
// the risk ladder, and a green control beside a green "Gerçek Kullanıcı" would
// make one colour mean two things. Lightened from the search's suggestion so
// its text clears 4.5:1 on a selected card's tint (see above).
export const accent = {
  DEFAULT: "#6CB0FB",
  strong: "#93C5FD",
  ink: "#0B1222",
};

// The SOC's own health, never a session's: the header pill that says whether
// the data on screen is current. Green from the design search; it sits only in
// the top bar, always beside words, and is a different green from the "safe"
// band's emerald so the two are not one signal.
export const status = {
  live: "#22C55E",
};

// The page could not get its data (connection lost, server error). Kept apart
// from the "blocked" risk rose on purpose: "the panel failed" is not "a bot
// was detected". #EF4444 is the design search's destructive red and is used
// for borders and the status dot; its text is 4.16:1 on a card, short of 4.5,
// so words in this tone are set in the lighter red.
export const danger = {
  DEFAULT: "#F87171",
  strong: "#EF4444",
};

// The four risk states, and only the risk states. A colour from this group on
// screen always means "the score is in this band".
export const risk = {
  safe: "#34D399", // 0-40   Gerçek Kullanıcı
  suspect: "#FBBF24", // 40-60  Şüpheli
  high: "#FB923C", // 60-80  Yüksek Risk
  blocked: "#FB7185", // 80-100 Bot Tespit Edildi
};

// Synthetic demo data gets its own hue, outside the risk ladder and the
// accent, because "a simulator produced this" is not a risk state. See
// components/SyntheticBadge.jsx.
export const synthetic = {
  DEFAULT: "#C4B5FD",
  line: "#8B5CF6",
};

// The chart draws its axis labels in SVG, where Tailwind's font utilities do
// not reach; it reads the stack from here.
export const monoFontStack =
  '"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';

export const colors = { surface, line, ink, accent, status, danger, risk, synthetic };

export default colors;
