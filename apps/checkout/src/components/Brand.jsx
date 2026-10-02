// The two marks on this page, both original and both fictional:
//  - TechStore, the shop selling the keyboard;
//  - DemoPay, the payment brand of its hosted checkout. A neutral stand-in
//    for a real payment provider -- deliberately not PayPal's, Stripe's or
//    anyone's name, logo or colours.
import { BRANDS } from "../lib/card.js";

export function TechStoreLogo({ className = "" }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <svg viewBox="0 0 32 32" className="h-8 w-8 shrink-0" aria-hidden="true" focusable="false">
        <rect width="32" height="32" rx="8" fill="#0B1F3A" />
        <path d="M9 10h14v3.2h-5.4V23h-3.2v-9.8H9z" fill="#FFFFFF" />
        <circle cx="23" cy="21.5" r="2" fill="#3B82F6" />
      </svg>
      <span className="text-[17px] leading-none tracking-tight text-ink">
        <span className="font-semibold">Tech</span>
        <span className="font-normal">Store</span>
      </span>
    </span>
  );
}

export function DemoPayMark({ className = "h-6 w-6" }) {
  return (
    <svg viewBox="0 0 24 24" className={`shrink-0 ${className}`} aria-hidden="true" focusable="false">
      <rect width="24" height="24" rx="6.5" fill="#1D4ED8" />
      <path d="M8 6.75h3.6a5.25 5.25 0 0 1 0 10.5H8z" fill="none" stroke="#FFFFFF" strokeWidth="2" strokeLinejoin="round" />
      <circle cx="11.6" cy="12" r="1.6" fill="#FFFFFF" />
    </svg>
  );
}

export function DemoPayWordmark({ className = "" }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <DemoPayMark />
      <span className="text-[15px] font-semibold leading-none tracking-tight">
        <span className="text-ink">Demo</span>
        <span className="text-brand">Pay</span>
      </span>
    </span>
  );
}

// Text chips, not card-network logos: a logo is someone's trademark, and the
// name in plain capitals is all the payer needs to see which card was read.
// `active` dims the other brands once the number names one, which doubles as
// quiet confirmation that the number was read.
export function BrandChip({ brand, active = true, className = "" }) {
  const meta = BRANDS[brand];
  if (!meta) return null;
  return (
    <span
      className={`inline-flex h-6 items-center rounded-md border px-1.5 text-[10px] font-semibold leading-none tracking-[0.08em] transition-colors duration-200 ${
        active ? "border-ink/20 bg-white text-ink" : "border-line bg-canvas text-ink-muted"
      } ${className}`}
    >
      {meta.label}
    </span>
  );
}
