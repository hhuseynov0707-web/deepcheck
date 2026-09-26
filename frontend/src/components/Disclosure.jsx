import { ChevronDownIcon } from "./icons.jsx";

// Legally required small print, folded away.
//
// A KVKK warning set in grey in the middle of a payment form is not read by
// anyone -- it only makes the form look longer and the product look unfinished.
// Folded behind a summary that says what is inside, the text is one keystroke
// away, still in the page, still in the accessibility tree, still found by
// ctrl-F, and the form is readable again.
//
// Built on <details>/<summary> deliberately: it is keyboard operable, exposed
// as a disclosure to screen readers and open to find-in-page without a line of
// JavaScript.
export default function Disclosure({ summary, defaultOpen = false, className = "", children }) {
  return (
    <details
      open={defaultOpen}
      className={`group rounded-field border border-line bg-canvas-sunken/60 ${className}`}
    >
      <summary
        className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-caption font-medium text-ink-muted
                   transition-colors hover:text-ink [&::-webkit-details-marker]:hidden"
      >
        <ChevronDownIcon className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180" />
        <span className="min-w-0">{summary}</span>
      </summary>
      <div className="space-y-2 border-t border-line px-3 py-3 text-caption leading-relaxed text-ink-faint">
        {children}
      </div>
    </details>
  );
}
