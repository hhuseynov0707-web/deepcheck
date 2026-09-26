import { AlertCircleIcon, AlertTriangleIcon, BanIcon, CheckIcon, InfoIcon } from "./icons.jsx";

// An inline message attached to what it is about. Tone sets the colour AND the
// glyph, and the message's own words say the rest -- nothing here is carried
// by colour alone.
//
// `role` is the caller's decision, not this component's: an outcome the user
// caused ("Ödeme alındı") belongs in an existing role="status" live region,
// while a refusal that interrupts them is role="alert". Passing the wrong one
// either shouts over a screen-reader user or leaves them unaware, so there is
// no default.
const TONES = {
  neutral: { box: "border-line-strong bg-panel-raised text-ink-muted", icon: "text-ink-faint", Icon: InfoIcon },
  info: { box: "border-accent/30 bg-accent/10 text-ink", icon: "text-accent", Icon: InfoIcon },
  safe: { box: "border-risk-safe/30 bg-risk-safe/10 text-risk-safe", icon: "text-risk-safe", Icon: CheckIcon },
  suspect: {
    box: "border-risk-suspect/30 bg-risk-suspect/10 text-risk-suspect",
    icon: "text-risk-suspect",
    Icon: AlertCircleIcon,
  },
  high: {
    box: "border-risk-high/30 bg-risk-high/10 text-risk-high",
    icon: "text-risk-high",
    Icon: AlertTriangleIcon,
  },
  blocked: {
    box: "border-risk-blocked/30 bg-risk-blocked/10 text-risk-blocked",
    icon: "text-risk-blocked",
    Icon: BanIcon,
  },
  synthetic: {
    box: "border-synthetic-line/40 bg-synthetic/10 text-synthetic",
    icon: "text-synthetic",
    Icon: InfoIcon,
  },
};

export default function Alert({
  tone = "neutral",
  title,
  icon,
  actions,
  role,
  className = "",
  children,
  ...rest
}) {
  const spec = TONES[tone] ?? TONES.neutral;
  const Icon = icon ?? spec.Icon;

  return (
    <div
      role={role}
      className={`flex gap-3 rounded-field border px-3.5 py-3 text-caption ${spec.box} ${className}`}
      {...rest}
    >
      <Icon className={`mt-px h-4 w-4 shrink-0 ${spec.icon}`} />
      <div className="min-w-0 flex-1 space-y-1.5">
        {title && <p className="text-body font-semibold leading-snug">{title}</p>}
        {children && <div className="leading-relaxed">{children}</div>}
        {actions && <div className="flex flex-wrap items-center gap-2 pt-1">{actions}</div>}
      </div>
    </div>
  );
}
