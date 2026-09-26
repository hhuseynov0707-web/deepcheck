// The single source of the design system's raw values.
//
// Tailwind's theme (tailwind.config.js) is built from this file, and the D3
// charts import the same object, so a colour exists exactly once: there is no
// second place where a hex can drift out of step with the utility class that
// is supposed to mean the same thing.
//
// Contrast: every foreground/background pair used by the UI was measured
// against WCAG 2.1 (scratch script, sRGB relative luminance). The lowest
// text pair in the system is `ink.faint` on `panel.raised` at 5.28:1 and the
// lowest risk colour is `blocked` on its own 10% tint at 6.09:1 -- all above
// the 4.5:1 AA threshold for body text. `line.control`, the border that makes
// an input identifiable as an input, is >= 3.14:1 against every surface it is
// drawn on (WCAG 1.4.11 non-text contrast). `line` and `line.strong` are
// decorative separators only; nothing is identified by them alone.

// Dark surface scale, darkest to lightest. `canvas` is the page, `panel` is a
// card on it, `inset`/`sunken` are wells inside a card (inputs, code, totals),
// `raised` is hover and selected state.
export const surface = {
  sunken: "#06080B",
  canvas: "#0A0C10",
  panel: "#101319",
  inset: "#161A22",
  raised: "#1C212B",
};

export const line = {
  DEFAULT: "#222835", // decorative: card edges, dividers
  strong: "#323A48", // decorative: hover edges, selected panels
  control: "#5E697C", // >= 3:1 on every surface: input and button boundaries
};

export const ink = {
  DEFAULT: "#EAEEF5", // headings, primary values
  muted: "#AEB7C6", // body copy, labels
  faint: "#8A94A6", // small print, captions, axis ticks
  inverse: "#04121F", // text on a solid accent or risk fill
};

// One accent, used for every interactive affordance (links, focus rings,
// primary actions, explanatory bars). It is deliberately blue: the risk ladder
// owns green/amber/orange/red, so nothing interactive may borrow those.
export const accent = {
  DEFAULT: "#4CA6FF",
  strong: "#7CC0FF",
  ink: "#04121F",
};

// The four risk states, and only the risk states. A colour from this group on
// screen always means "the score is in this band" -- never "this button is
// primary" or "this chart series is the second one".
export const risk = {
  safe: "#34D399", // 0-40   Gerçek Kullanıcı
  suspect: "#FBBF24", // 40-60  Şüpheli
  high: "#FB923C", // 60-80  Yüksek Risk
  blocked: "#FB7185", // 80-100 Bot Tespit Edildi
};

// Synthetic demo data gets its own hue, outside both the risk ladder and the
// accent, because "this number was produced by a simulator" is not a risk
// state and not an action. See components/SyntheticBadge.jsx.
export const synthetic = {
  DEFAULT: "#C4B5FD",
  line: "#8B5CF6",
};

export const colors = { surface, line, ink, accent, risk, synthetic };

export default colors;
