import { forwardRef } from "react";

// A form field: a visible label (never a placeholder standing in for one), an
// optional hint, and the error directly under the control it is about. The
// hint and error ids are wired into aria-describedby, and aria-invalid is set
// only while an error is shown.
//
// `adornment` sits inside the right edge of the control (the detected card
// brand, a card icon). It is decorative and never takes clicks; `adornmentPad`
// reserves room for it so typed text never runs underneath.
//
// The placeholder (the "0000 0000 0000 0000" and "AA/YY" format hints) is
// ink-muted at FULL opacity: #5B6B82 on the white field is 5.4:1 by the WCAG
// formula, the same AA contrast as every other secondary text here
// (tailwind.config.js). At /70 it blended to #8C97A8, 2.9:1 -- below AA, and
// the first thing a projector washes out.
export const TextField = forwardRef(function TextField(
  { id, label, hint, error, adornment, adornmentPad = "pr-28", className = "", inputClassName = "", ...inputProps },
  ref,
) {
  // An error replaces the hint rather than stacking under it: the error is
  // what the payer needs at that moment, and two lines of small text under a
  // half-width field push the row out of line with its neighbour.
  const errorId = error ? `${id}-error` : null;
  const hintId = hint && !error ? `${id}-hint` : null;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
      </label>
      <div className="relative">
        <input
          ref={ref}
          id={id}
          aria-invalid={error ? "true" : undefined}
          aria-describedby={describedBy}
          className={`field-control h-12 w-full rounded-field border bg-white px-3.5 text-[15px] text-ink shadow-[0_1px_1px_rgb(11_31_58/0.04)]
            transition-[border-color,box-shadow] duration-150 placeholder:text-ink-muted
            disabled:cursor-not-allowed disabled:bg-canvas disabled:text-ink-muted
            ${error ? "border-danger focus:shadow-[0_0_0_4px_rgb(185_28_28/0.12)]" : "border-line-control hover:border-ink-muted focus:border-brand focus:shadow-[0_0_0_4px_rgb(37_99_235/0.14)]"}
            ${adornment ? adornmentPad : ""} ${inputClassName}`}
          {...inputProps}
        />
        {adornment && (
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center">{adornment}</span>
        )}
      </div>
      {error && (
        <p id={errorId} className="flex items-start gap-1.5 text-[13px] font-medium leading-5 text-danger">
          <svg viewBox="0 0 16 16" className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" focusable="false">
            <circle cx="8" cy="8" r="7" fill="currentColor" />
            <path d="M8 4.5v4M8 11.25h.01" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <span>{error}</span>
        </p>
      )}
      {hintId && (
        <p id={hintId} className="text-[13px] leading-5 text-ink-muted">
          {hint}
        </p>
      )}
    </div>
  );
});
