// The one label the SOC screens put on synthetic demo data: simulated sessions
// (backend/demo_seed.py --simulate) and decisions made against a seeded
// synthetic customer. The jury prototype runs on synthetic customers because
// the team has no customer base; that is acceptable only while every screen
// that shows such data says so, in the same words, every time.
//
// Violet on purpose: the risk ladder already uses green, yellow, orange and red,
// and the profile card uses amber ("Ek doğrulama istendi") and sky ("Gölge
// modu"). A synthetic marker that shared a colour with any of them would read
// as one of their meanings.
export const SYNTHETIC_LABEL = "Sentetik demo verisi";

export default function SyntheticBadge({ title, size = "md" }) {
  const sizing = size === "sm" ? "px-1.5 py-px text-[10px]" : "px-2.5 py-0.5 text-xs";
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-full border border-violet-500/40 bg-violet-500/10 font-medium text-violet-300 ${sizing}`}
    >
      {SYNTHETIC_LABEL}
    </span>
  );
}
