import { SpinnerIcon } from "./icons.jsx";

// Buttons. Four variants, and each one means something: `primary` is the one
// action the screen exists for, `secondary` is a real but lesser action,
// `ghost` is navigation-ish or dismissive, `danger` destroys or refuses.
//
// The accent carries `primary` rather than a risk colour: green, amber, orange
// and rose are reserved for score bands, and a green "confirm" button beside a
// green "Gerçek Kullanıcı" badge would make the colour mean two things.
const VARIANTS = {
  // Disabled is drawn as a dead control rather than a faded live one: a 50%
  // accent still looks blue enough to invite a click, and the label on it goes
  // muddy. Greyed out, the state is unmistakable and the text stays legible.
  primary:
    "bg-accent text-accent-ink hover:bg-accent-strong border border-transparent font-semibold shadow-panel " +
    "disabled:bg-panel-raised disabled:text-ink-faint disabled:border-line disabled:shadow-none",
  secondary:
    "bg-panel-raised text-ink border border-line-control hover:border-ink-faint font-medium disabled:opacity-50",
  ghost:
    "bg-transparent text-ink-muted border border-transparent hover:bg-panel-raised hover:text-ink font-medium disabled:opacity-50",
  danger:
    "bg-transparent text-risk-blocked border border-risk-blocked/40 hover:bg-risk-blocked/10 font-medium disabled:opacity-50",
};

// Heights, not paddings: a row of buttons lines up by its box. In the SOC every
// size clears the 44px touch target -- the former demo's 32px "sm" did not, and
// the SOC's top bar is made of "sm" buttons that a presenter may tap on a
// touchscreen laptop.
const SIZES = {
  sm: "min-h-[2.75rem] px-3 text-caption gap-1.5",
  md: "min-h-[2.75rem] px-4 text-body gap-2",
  lg: "min-h-[3rem] px-5 text-body gap-2",
};

export default function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  fullWidth = false,
  className = "",
  type = "button",
  disabled,
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-field tracking-[0.01em] transition-colors duration-150
        cursor-pointer disabled:cursor-not-allowed
        ${VARIANTS[variant] ?? VARIANTS.secondary} ${SIZES[size] ?? SIZES.md} ${
          fullWidth ? "w-full" : ""
        } ${className}`}
      {...rest}
    >
      {loading && <SpinnerIcon className="h-4 w-4 shrink-0" />}
      {children}
    </button>
  );
}
