// The product's icon set: inline SVG, stroked, 24x24 grid, currentColor.
//
// Copied from the former demo's frontend/src/components/icons.jsx (removed
// 2026-10-02; docs/architecture-two-apps.md, "Duplicated UI primitives");
// SOC-only icons are appended at the end.
//
// Inline rather than an icon package because the whole bundle has to work with
// no network, and drawn rather than typed because an emoji is a font the
// jury's machine may not have.
//
// Every icon here is decorative: it repeats something the neighbouring text
// already says. So they are aria-hidden by default and the accessible name
// always comes from the text or from the control's own label -- an icon is
// never the only carrier of meaning, and neither is a colour.

function Svg({ children, className = "h-4 w-4", strokeWidth = 1.75, title, ...rest }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : "true"}
      role={title ? "img" : undefined}
      focusable="false"
      {...rest}
    >
      {title && <title>{title}</title>}
      {children}
    </svg>
  );
}

export function ShieldIcon(props) {
  return (
    <Svg {...props}>
      <path d="M12 3 4.5 6v5.6c0 4.3 3 8.2 7.5 9.4 4.5-1.2 7.5-5.1 7.5-9.4V6L12 3Z" />
      <path d="m9 12 2.2 2.2L15.2 10" />
    </Svg>
  );
}

export function CardIcon(props) {
  return (
    <Svg {...props}>
      <rect x="2.75" y="5.25" width="18.5" height="13.5" rx="2.5" />
      <path d="M2.75 9.75h18.5M6.5 14.75h3" />
    </Svg>
  );
}

export function RadarIcon(props) {
  return (
    <Svg {...props}>
      <path d="M12 3.25a8.75 8.75 0 1 0 8.75 8.75" />
      <path d="M12 7a5 5 0 1 0 5 5" />
      <path d="M12 12 20 5" />
      <circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function CheckIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.75" />
      <path d="m8.4 12.2 2.4 2.4 4.8-5" />
    </Svg>
  );
}

export function AlertCircleIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.75" />
      <path d="M12 7.75v5" />
      <circle cx="12" cy="16" r="0.9" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function AlertTriangleIcon(props) {
  return (
    <Svg {...props}>
      <path d="M12 4.2 2.9 19.3h18.2L12 4.2Z" />
      <path d="M12 10v3.6" />
      <circle cx="12" cy="16.6" r="0.9" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function BanIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.75" />
      <path d="m6.2 6.2 11.6 11.6" />
    </Svg>
  );
}

export function InfoIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.75" />
      <path d="M12 11.2v5" />
      <circle cx="12" cy="8.1" r="0.9" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function LockIcon(props) {
  return (
    <Svg {...props}>
      <rect x="4.75" y="10.25" width="14.5" height="9.5" rx="2.2" />
      <path d="M8.25 10.25V7.9a3.75 3.75 0 0 1 7.5 0v2.35" />
    </Svg>
  );
}

export function ChevronDownIcon(props) {
  return (
    <Svg {...props}>
      <path d="m6.5 9.5 5.5 5.5 5.5-5.5" />
    </Svg>
  );
}

export function RefreshIcon(props) {
  return (
    <Svg {...props}>
      <path d="M20 11.2A8 8 0 0 0 6.3 6.6L4 8.9" />
      <path d="M4 4.6v4.3h4.3" />
      <path d="M4 12.8a8 8 0 0 0 13.7 4.6l2.3-2.3" />
      <path d="M20 19.4v-4.3h-4.3" />
    </Svg>
  );
}

export function PulseIcon(props) {
  return (
    <Svg {...props}>
      <path d="M3 12.5h3.8l2.1-5.4 3.4 9.9 2.3-5.6h6.4" />
    </Svg>
  );
}

// The only animated icon. Given role="status" by its caller, never a name of
// its own: the button's label already says what is happening.
export function SpinnerIcon({ className = "h-4 w-4" }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={`animate-spin ${className}`} aria-hidden="true" focusable="false">
      <circle className="opacity-25" cx="12" cy="12" r="9.5" stroke="currentColor" strokeWidth="2.5" />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M12 2.5a9.5 9.5 0 0 0-9.5 9.5h2.6A6.9 6.9 0 0 1 12 5.1V2.5Z"
      />
    </svg>
  );
}

// Added for the SOC app: the login form's show/hide control and the top bar.

export function EyeIcon(props) {
  return (
    <Svg {...props}>
      <path d="M2.75 12S6.25 5.75 12 5.75 21.25 12 21.25 12 17.75 18.25 12 18.25 2.75 12 2.75 12Z" />
      <circle cx="12" cy="12" r="2.75" />
    </Svg>
  );
}

export function EyeOffIcon(props) {
  return (
    <Svg {...props}>
      <path d="M9.9 6.05A9.6 9.6 0 0 1 12 5.75c5.75 0 9.25 6.25 9.25 6.25a16 16 0 0 1-2.4 3.1" />
      <path d="M6.3 7.6A15.8 15.8 0 0 0 2.75 12S6.25 18.25 12 18.25a9.3 9.3 0 0 0 4.6-1.2" />
      <path d="M10.1 10.1a2.75 2.75 0 0 0 3.8 3.8" />
      <path d="m3.5 3.5 17 17" />
    </Svg>
  );
}

export function LogOutIcon(props) {
  return (
    <Svg {...props}>
      <path d="M14.25 4.75h3.5a1.5 1.5 0 0 1 1.5 1.5v11.5a1.5 1.5 0 0 1-1.5 1.5h-3.5" />
      <path d="M10.25 8.25 6.5 12l3.75 3.75" />
      <path d="M6.75 12h9" />
    </Svg>
  );
}

export function FilterIcon(props) {
  return (
    <Svg {...props}>
      <path d="M4 5.75h16l-6.25 7.1v5.4l-3.5 1.5v-6.9L4 5.75Z" />
    </Svg>
  );
}

export function ClockIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.75" />
      <path d="M12 7.5V12l3 2" />
    </Svg>
  );
}

export function UsersIcon(props) {
  return (
    <Svg {...props}>
      <circle cx="9" cy="8.5" r="3.25" />
      <path d="M3.25 19c.6-3.1 2.9-5 5.75-5s5.15 1.9 5.75 5" />
      <path d="M15.5 5.6a3.25 3.25 0 0 1 0 5.8M17.6 14.3c1.6.6 2.75 2.2 3.15 4.7" />
    </Svg>
  );
}

export function GaugeIcon(props) {
  return (
    <Svg {...props}>
      <path d="M4.2 17.5a8.75 8.75 0 1 1 15.6 0" />
      <path d="m12 13.2 3.6-4.4" />
      <circle cx="12" cy="13.6" r="1.2" fill="currentColor" stroke="none" />
    </Svg>
  );
}
