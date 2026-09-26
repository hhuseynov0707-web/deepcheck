// "There is nothing here" as a designed state: what is missing, why it is
// missing, and what would make it appear. Never a bare dash, and never a
// placeholder figure standing in for a number nobody measured.
export default function EmptyState({ icon: Icon, title, description, action, className = "" }) {
  return (
    <div
      className={`flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-line-strong
                  bg-canvas-sunken/40 px-6 py-10 text-center ${className}`}
    >
      {Icon && (
        <span className="mb-1 flex h-9 w-9 items-center justify-center rounded-full border border-line bg-panel text-ink-faint">
          <Icon className="h-4 w-4" />
        </span>
      )}
      <p className="text-body font-medium text-ink">{title}</p>
      {description && <p className="max-w-[46ch] text-caption leading-relaxed text-ink-faint">{description}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}
