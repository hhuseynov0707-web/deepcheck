// Inline SVG icons, one stroke style (1.75, round joins). Decorative by
// default: every icon sits next to text that already says the same thing.
function Svg({ className = "h-4 w-4", children, ...rest }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
      {...rest}
    >
      {children}
    </svg>
  );
}

// No padlock in this set on purpose: see SiteHeader in App.jsx.

export function CheckIcon(props) {
  return (
    <Svg {...props}>
      <path d="m5 12.5 4.5 4.5L19 7.5" />
    </Svg>
  );
}

export function AlertIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.75v5" />
      <path d="M12 16.25h.01" />
    </Svg>
  );
}

export function InfoIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.25" />
      <path d="M12 7.75h.01" />
    </Svg>
  );
}

export function RefreshIcon(props) {
  return (
    <Svg {...props}>
      <path d="M20 11a8 8 0 1 0-2.34 5.66" />
      <path d="M20 4.5V11h-6.5" />
    </Svg>
  );
}

export function CloseIcon(props) {
  return (
    <Svg {...props}>
      <path d="m6.5 6.5 11 11M17.5 6.5l-11 11" />
    </Svg>
  );
}

export function ArrowLeftIcon(props) {
  return (
    <Svg {...props}>
      <path d="M19 12H5" />
      <path d="m11 6-6 6 6 6" />
    </Svg>
  );
}

export function CardIcon(props) {
  return (
    <Svg {...props}>
      <rect x="3" y="5.5" width="18" height="13" rx="2.25" />
      <path d="M3 10h18" />
      <path d="M7 15h3" />
    </Svg>
  );
}

export function PhoneIcon(props) {
  return (
    <Svg {...props}>
      <rect x="7" y="3" width="10" height="18" rx="2.25" />
      <path d="M11 17.5h2" />
    </Svg>
  );
}

// Not a spinning icon under reduced motion: the button's "İşleniyor…" text
// already says it is working, and a static ring says nothing false.
export function Spinner({ className = "h-4 w-4" }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={`${className} motion-safe:animate-spin`}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.3" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
