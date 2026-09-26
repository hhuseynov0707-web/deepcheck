import Badge from "./Badge.jsx";

// The one label the SOC screens put on synthetic demo data: simulated sessions
// (backend/demo_seed.py --simulate) and decisions made against a seeded
// synthetic customer. The jury prototype runs on synthetic customers because
// the team has no customer base; that is acceptable only while every screen
// that shows such data says so, in the same words, every time.
//
// Violet on purpose, and reserved: the risk ladder owns green, amber, orange
// and rose, the accent owns blue, and the profile card's own states borrow from
// those. A synthetic marker sharing a hue with any of them would read as one of
// their meanings. It is also never only a colour -- the pill spells the words
// out, and every panel that shows one repeats them in a sentence.
export const SYNTHETIC_LABEL = "Sentetik demo verisi";

export default function SyntheticBadge({ title, size = "md" }) {
  return (
    <Badge tone="synthetic" size={size === "sm" ? "xs" : "sm"} title={title}>
      {SYNTHETIC_LABEL}
    </Badge>
  );
}
