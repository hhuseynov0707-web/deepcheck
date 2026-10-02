// A pill that states a fact in one or two words. Tones are semantic, never
// decorative: the four risk tones mean exactly the score band they are named
// after, `synthetic` means simulator-produced data, `accent` means an
// interactive or informational state, and `neutral` means none of the above.
//
// A tone is never the only thing carrying the meaning -- the badge's own text
// says it, and the risk tones additionally take a glyph from riskLevels.js.
const TONES = {
  neutral: "border-line-strong bg-panel-raised text-ink-muted",
  accent: "border-accent/35 bg-accent/10 text-accent",
  safe: "border-risk-safe/30 bg-risk-safe/10 text-risk-safe",
  suspect: "border-risk-suspect/30 bg-risk-suspect/10 text-risk-suspect",
  high: "border-risk-high/30 bg-risk-high/10 text-risk-high",
  blocked: "border-risk-blocked/30 bg-risk-blocked/10 text-risk-blocked",
  synthetic: "border-synthetic-line/45 bg-synthetic/10 text-synthetic",
};

const SIZES = {
  xs: "gap-1 px-1.5 py-px text-[0.6875rem] leading-4",
  sm: "gap-1.5 px-2 py-0.5 text-eyebrow",
  md: "gap-1.5 px-2.5 py-1 text-caption",
  lg: "gap-2 px-3 py-1.5 text-body",
};

const ICON_SIZES = { xs: "h-3 w-3", sm: "h-3.5 w-3.5", md: "h-4 w-4", lg: "h-4 w-4" };

export default function Badge({
  tone = "neutral",
  size = "md",
  icon: Icon,
  dot = false,
  pulse = false,
  className = "",
  children,
  ...rest
}) {
  return (
    <span
      className={`inline-flex max-w-full items-center rounded-full border font-medium ${
        TONES[tone] ?? TONES.neutral
      } ${SIZES[size] ?? SIZES.md} ${className}`}
      {...rest}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden="true">
          {pulse && (
            <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-current" />
          )}
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {Icon && <Icon className={`${ICON_SIZES[size] ?? ICON_SIZES.md} shrink-0`} />}
      <span className="truncate">{children}</span>
    </span>
  );
}
