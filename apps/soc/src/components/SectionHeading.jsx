// Section headings, so hierarchy comes from a named role rather than from
// whatever font size was nearest to hand. `level` sets the heading element for
// the document outline; `size` sets how loud it looks. They are separate on
// purpose: a page can have an h2 that is visually quiet without lying to a
// screen reader about the structure.
const SIZES = {
  page: "text-h1 font-semibold text-ink",
  section: "text-h2 font-semibold text-ink",
  card: "text-h3 font-semibold text-ink",
  sub: "text-body font-semibold text-ink",
};

export default function SectionHeading({
  level = 2,
  size = "section",
  eyebrow,
  description,
  actions,
  id,
  className = "",
  children,
}) {
  const Tag = `h${level}`;
  return (
    <div className={`flex flex-wrap items-start justify-between gap-x-4 gap-y-2 ${className}`}>
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
        <Tag id={id} className={SIZES[size] ?? SIZES.section}>
          {children}
        </Tag>
        {description && <p className="mt-1.5 max-w-[62ch] text-caption text-ink-faint">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
