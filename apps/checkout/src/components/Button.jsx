import { forwardRef } from "react";

import { Spinner } from "./icons.jsx";

// Two variants: `primary` is the one action the screen exists for, and
// `secondary` is a real but lesser one. Both clear the 44 px touch target.
//
// A button that is WORKING keeps its colour: "İşleniyor…" on a greyed-out
// button reads as "you cannot press this" when it means "we are on it", and
// grey text on grey is hard to read at the one moment the payer is watching.
// Only a button that cannot be used yet (no cart loaded) is drawn dead.
const LOOKS = {
  primary: {
    live: "bg-brand text-white hover:bg-brand-hover active:translate-y-px border border-transparent font-semibold shadow-button",
    busy: "bg-brand text-white border border-transparent font-semibold shadow-button cursor-progress",
    dead: "bg-[#C3CEE6] text-ink-muted border border-transparent font-semibold cursor-not-allowed",
  },
  secondary: {
    live: "bg-white text-ink border border-line-control hover:border-ink-muted hover:bg-canvas active:translate-y-px font-medium",
    busy: "bg-white text-ink border border-line-control font-medium cursor-progress",
    dead: "bg-white text-ink-muted border border-line font-medium cursor-not-allowed",
  },
};

const SIZES = {
  md: "min-h-[2.75rem] px-4 text-[15px] gap-2",
  lg: "min-h-[3.25rem] px-5 text-base gap-2.5",
};

const Button = forwardRef(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    fullWidth = false,
    className = "",
    type = "button",
    disabled,
    children,
    ...rest
  },
  ref,
) {
  const looks = LOOKS[variant] ?? LOOKS.secondary;
  const look = loading ? looks.busy : disabled ? looks.dead : `${looks.live} cursor-pointer`;
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center rounded-field transition-colors duration-150 ${look} ${
        SIZES[size] ?? SIZES.md
      } ${fullWidth ? "w-full" : ""} ${className}`}
      {...rest}
    >
      {loading && <Spinner className="h-[18px] w-[18px] shrink-0" />}
      {children}
    </button>
  );
});

export default Button;
