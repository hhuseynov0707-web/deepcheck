import { riskLevelFor } from "./riskLevels.js";
import useAnimatedNumber from "../hooks/useAnimatedNumber.js";

// The score, its band and the band's glyph in one pill.
//
// Display only. The same number is what POST /api/decision acts on, but the
// acting happens on the server; this badge changes nothing about the payment.
//
// Three channels say the same thing -- the Turkish label, the glyph and the
// colour -- so the badge still reads correctly in greyscale and to someone who
// cannot separate the 60-80 orange from the 80-100 rose.
// `animate` off for a badge whose number is driven by a pointer rather than by
// a poll: the count-up says "this figure just changed by itself", and sweeping
// the risk-history chart would leave it perpetually tweening a value the
// reader is trying to read.
export default function RiskBadge({ riskScore = 0, size = "md", live = true, animate = true }) {
  const level = riskLevelFor(riskScore);
  const animatedScore = useAnimatedNumber(riskScore, animate ? 500 : 0);
  const large = size === "lg";

  return (
    <span
      className={`inline-flex items-center rounded-full border transition-colors ${level.tint} ${level.border} ${
        level.text
      } ${large ? "gap-2.5 px-3.5 py-1.5" : "gap-2 px-2.5 py-1"}`}
    >
      {live && (
        <span className={`relative flex shrink-0 ${large ? "h-2 w-2" : "h-1.5 w-1.5"}`} aria-hidden="true">
          <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-current" />
          <span className="relative inline-flex h-full w-full rounded-full bg-current" />
        </span>
      )}
      <level.Icon className={`shrink-0 ${large ? "h-4 w-4" : "h-3.5 w-3.5"}`} />
      <span
        className={`font-semibold uppercase tracking-[0.07em] ${large ? "text-caption" : "text-eyebrow"}`}
      >
        {level.label}
      </span>
      <span aria-hidden="true" className="h-3.5 w-px shrink-0 bg-current opacity-30" />
      <span className={`num font-bold leading-none ${large ? "text-lead" : "text-caption"}`}>
        {animatedScore.toFixed(1)}
      </span>
    </span>
  );
}
