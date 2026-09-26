// A figure and what it is. The label is small, uppercase and quiet; the number
// is large, mono and tabular so a row of stats lines up digit for digit.
//
// The label element and the value element are siblings, in that order, on
// purpose -- that relationship is what makes the pair readable as a definition
// and is what the dashboard tests assert against.
const SIZES = {
  sm: "text-h2",
  md: "text-metric",
  lg: "text-metric-lg",
};

const TONES = {
  default: "text-ink",
  accent: "text-accent",
  safe: "text-risk-safe",
  suspect: "text-risk-suspect",
  high: "text-risk-high",
  blocked: "text-risk-blocked",
  muted: "text-ink-muted",
};

export default function Stat({
  label,
  value,
  suffix = "",
  hint,
  tone = "default",
  size = "md",
  valueClassName = "",
  className = "",
}) {
  // tone: null means "the caller is supplying its own colour class", so no
  // token colour is emitted and the two cannot fight over which wins.
  const toneClass = tone === null ? "" : (TONES[tone] ?? TONES.default);
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <p className="eyebrow">{label}</p>
      <p className={`num font-semibold leading-none ${SIZES[size] ?? SIZES.md} ${toneClass} ${valueClassName}`}>
        {value}
        {suffix && <span className="ml-0.5 text-h3 font-medium text-ink-faint">{suffix}</span>}
      </p>
      {hint && <p className="text-caption text-ink-faint">{hint}</p>}
    </div>
  );
}
