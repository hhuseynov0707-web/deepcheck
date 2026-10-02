// The one panel in the system. Everything that sits on the canvas as a block
// of content is this: same radius, same hairline, same single elevation
// treatment. A second card style would be a second meaning nobody defined.
const TONES = {
  default: "bg-panel border-line",
  inset: "bg-panel-inset border-line",
  raised: "bg-panel-raised border-line-strong",
  // For a panel whose subject is synthetic demo data. The tint is faint; the
  // words and the badge do the telling, this only groups them.
  synthetic: "bg-panel border-synthetic-line/40",
};

// Denser than the store's cards (p-5 / p-6, as the former demo's were): the
// SOC is read on one screen.
const PADDING = {
  none: "",
  sm: "p-3.5",
  md: "p-4 sm:p-5",
  lg: "p-6 sm:p-7",
};

export default function Card({
  as: Tag = "div",
  tone = "default",
  padding = "md",
  className = "",
  children,
  ...rest
}) {
  return (
    <Tag
      className={`rounded-card border shadow-panel ${TONES[tone] ?? TONES.default} ${
        PADDING[padding] ?? PADDING.md
      } ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  );
}

// A well inside a card: totals, code, a read-only value. Darker than its
// parent, never lighter, so depth always reads the same way down the page.
export function CardWell({ className = "", children, ...rest }) {
  return (
    <div className={`rounded-field border border-line bg-canvas-sunken ${className}`} {...rest}>
      {children}
    </div>
  );
}
