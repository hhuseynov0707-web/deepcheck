import { useEffect, useRef } from "react";

import { BRANDS } from "../lib/card.js";
import { formatTry } from "../lib/money.js";
import Button from "./Button.jsx";
import { CheckIcon, RefreshIcon } from "./icons.jsx";

// The confirmation. Every figure is the store server's (order number, amount,
// card), echoed from its "paid" answer -- not the page's own idea of what was
// paid.
export default function Receipt({ receipt, onNewPayment }) {
  const headingRef = useRef(null);

  // The form this replaces held the focus; without this a screen-reader user
  // is left on a button that no longer exists.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const brandName = BRANDS[receipt.brand]?.name ?? "Kart";

  return (
    <div className="animate-rise-in text-center">
      <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-success-soft text-success">
        <CheckIcon className="h-7 w-7" />
      </span>
      <h2 ref={headingRef} tabIndex={-1} className="mt-4 text-xl font-semibold tracking-tight text-ink">
        Ödeme alındı
      </h2>
      <p className="mt-1 text-sm text-ink-muted">Siparişiniz onaylandı. Teşekkür ederiz.</p>

      <dl className="mx-auto mt-6 max-w-sm space-y-3 rounded-field border border-line bg-canvas p-4 text-left">
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-sm text-ink-muted">Sipariş no</dt>
          <dd className="num text-sm font-medium text-ink">{receipt.orderId}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-sm text-ink-muted">Tutar</dt>
          <dd className="num text-sm font-semibold text-ink">{formatTry(receipt.amount)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-sm text-ink-muted">Kart</dt>
          <dd className="num text-sm font-medium text-ink">
            {brandName} •••• {receipt.last4}
          </dd>
        </div>
      </dl>

      <p className="mt-4 text-[13px] text-ink-muted">Gerçek tahsilat yapılmadı (demo).</p>

      <Button variant="secondary" onClick={onNewPayment} className="mt-6">
        <RefreshIcon className="h-4 w-4" />
        Yeni ödeme
      </Button>
    </div>
  );
}
