import { useId } from "react";

// A form field with a visible label, optional hint, optional legal small print
// and an error that appears next to the control rather than in a summary at the
// top of the page. The hint and error ids are wired into aria-describedby for
// the caller, because that is the part everyone forgets.
//
// `children` is a function: the caller still owns its own <input>, <select> or
// composed control (the card field has an issuer icon inside it), and gets the
// id and ARIA wiring handed to it.
export default function Field({
  id: providedId,
  label,
  hint,
  error,
  note,
  describedBy: extraDescribedBy,
  labelSuffix,
  className = "",
  children,
}) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const hintId = `${id}-hint`;
  const noteId = `${id}-note`;
  const errorId = `${id}-error`;

  const describedBy =
    [hint ? hintId : null, note ? noteId : null, error ? errorId : null, extraDescribedBy]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-eyebrow font-semibold uppercase tracking-[0.08em] text-ink-muted">
          {label}
        </label>
        {labelSuffix}
      </div>
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? "true" : undefined,
      })}
      {hint && (
        <p id={hintId} className="text-caption text-ink-faint">
          {hint}
        </p>
      )}
      {note && (
        <p id={noteId} className="text-caption text-ink-faint">
          {note}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-caption font-medium text-risk-blocked">
          {error}
        </p>
      )}
    </div>
  );
}

// The shared control surface: a well inside the card, a boundary that meets
// WCAG 1.4.11 against every surface it is drawn on (line.control, >= 3:1), and
// the accent on focus. `invalid` tints the boundary -- never alone: the error
// text under the field says the same thing in words.
export function controlClass({ invalid = false, mono = false, extra = "" } = {}) {
  return [
    "w-full rounded-field bg-canvas-sunken px-3.5 py-2.5 text-body text-ink",
    "border transition-colors placeholder:text-ink-faint/70",
    mono ? "num tracking-[0.08em]" : "",
    invalid ? "border-risk-blocked/70" : "border-line-control hover:border-ink-faint",
    // No outline-none here: the global :focus-visible ring in index.css is
    // the only focus indicator, and a utility would out-specify and erase it.
    "focus:border-accent",
    "disabled:opacity-60 disabled:cursor-not-allowed",
    extra,
  ]
    .filter(Boolean)
    .join(" ");
}

export function Input({ invalid = false, mono = false, className = "", ...rest }) {
  return <input className={controlClass({ invalid, mono, extra: className })} {...rest} />;
}

export function Select({ invalid = false, className = "", children, ...rest }) {
  return (
    <select className={controlClass({ invalid, extra: `pr-9 ${className}` })} {...rest}>
      {children}
    </select>
  );
}
