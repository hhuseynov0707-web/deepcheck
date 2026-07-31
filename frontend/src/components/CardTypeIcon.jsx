export default function CardTypeIcon({ type = "generic", className = "" }) {
  if (type === "visa") {
    return (
      <svg viewBox="0 0 48 30" className={className} aria-label="Visa">
        <rect width="48" height="30" rx="4" fill="#1a1f71" />
        <text
          x="24"
          y="20"
          textAnchor="middle"
          fontFamily="Georgia, serif"
          fontStyle="italic"
          fontWeight="700"
          fontSize="13"
          fill="#ffffff"
        >
          VISA
        </text>
      </svg>
    );
  }

  if (type === "mastercard") {
    return (
      <svg viewBox="0 0 48 30" className={className} aria-label="Mastercard">
        <rect width="48" height="30" rx="4" fill="#16213a" />
        <circle cx="20" cy="15" r="9" fill="#eb001b" fillOpacity="0.9" />
        <circle cx="28" cy="15" r="9" fill="#f79e1b" fillOpacity="0.9" />
      </svg>
    );
  }

  if (type === "amex") {
    return (
      <svg viewBox="0 0 48 30" className={className} aria-label="American Express">
        <rect width="48" height="30" rx="4" fill="#2e77bc" />
        <text
          x="24"
          y="19"
          textAnchor="middle"
          fontFamily="Arial, sans-serif"
          fontWeight="700"
          fontSize="9"
          fill="#ffffff"
        >
          AMEX
        </text>
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 48 30" className={className} aria-label="Kart">
      <rect width="48" height="30" rx="4" fill="none" stroke="#3f3f46" strokeWidth="1.5" />
      <rect x="6" y="9" width="36" height="4" rx="1" fill="#3f3f46" />
    </svg>
  );
}
