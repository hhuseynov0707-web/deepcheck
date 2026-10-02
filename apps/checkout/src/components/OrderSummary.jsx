import { formatTry, vatPercent } from "../lib/money.js";
import Button from "./Button.jsx";
import { RefreshIcon } from "./icons.jsx";

// "Sipariş özeti": every figure comes from GET /api/cart, the store server's
// own cart. Nothing here is computed from anything the payer typed. Only the
// amounts are shown: the cart's product line was decoration that told the
// payer nothing the amount does not.
function Line({ label, value, strong = false }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className={strong ? "text-base font-semibold text-ink" : "text-sm text-ink-muted"}>{label}</dt>
      <dd className={`num ${strong ? "text-lg font-semibold tracking-tight text-ink" : "text-sm text-ink"}`}>{value}</dd>
    </div>
  );
}

function Skeleton() {
  return (
    <div aria-hidden="true" className="animate-pulse space-y-3 motion-reduce:animate-none">
      <div className="h-4 rounded bg-canvas" />
      <div className="h-4 rounded bg-canvas" />
      <div className="h-6 rounded bg-canvas" />
    </div>
  );
}

// The amount, large, above the order: the first thing a hosted checkout
// answers is "how much, to whom". It is the page's h1, so a screen-reader user
// navigating by headings lands on it first. While the cart loads, a block of
// the same height holds the place, so the layout does not jump when it lands.
export function AmountHero({ cart, failed = false, paid = false }) {
  return (
    <div>
      <h1 className="text-sm font-medium text-ink-muted">{paid ? "Ödenen tutar" : "Ödenecek tutar"}</h1>
      {cart ? (
        <p className="num mt-1 text-[2.25rem] font-semibold leading-[1.15] tracking-tight text-ink sm:text-[2.75rem]">
          {formatTry(cart.total)}
        </p>
      ) : failed ? (
        // No figure rather than a stale or guessed one; the summary below
        // says what went wrong and offers the retry.
        <p className="mt-1 text-[2.25rem] font-semibold leading-[1.15] text-line-control sm:text-[2.75rem]">
          <span aria-hidden="true">-</span>
          <span className="sr-only">Tutar yüklenemedi</span>
        </p>
      ) : (
        <div
          aria-hidden="true"
          className="mt-2 h-10 w-52 animate-pulse rounded-field bg-line motion-reduce:animate-none sm:h-12"
        />
      )}
      <p className="mt-1.5 text-sm text-ink-muted">KDV dahil · TechStore siparişi</p>
    </div>
  );
}

export default function OrderSummary({ cart, failed, onRetry }) {
  const vatRate = cart ? vatPercent(cart.subtotal, cart.vat) : null;

  return (
    <section aria-labelledby="ozet-basligi" className="rounded-card border border-line bg-card p-5 shadow-card sm:p-6">
      <h2 id="ozet-basligi" className="text-base font-semibold text-ink">
        Sipariş özeti
      </h2>

      <div className="mt-5" aria-busy={!cart && !failed ? "true" : undefined}>
        {failed ? (
          <div className="space-y-3">
            <p className="text-sm text-ink">Sipariş bilgileri yüklenemedi.</p>
            <Button variant="secondary" onClick={onRetry}>
              <RefreshIcon className="h-4 w-4" />
              Tekrar dene
            </Button>
          </div>
        ) : !cart ? (
          <>
            <Skeleton />
            <p className="sr-only">Sipariş bilgileri yükleniyor…</p>
          </>
        ) : (
          <>
            <dl className="space-y-2.5">
              <Line label="Ara toplam" value={formatTry(cart.subtotal)} />
              <Line label={vatRate === null ? "KDV" : `KDV (%${vatRate})`} value={formatTry(cart.vat)} />
              <div className="border-t border-line pt-3">
                <Line label="Toplam" value={formatTry(cart.total)} strong />
              </div>
            </dl>
          </>
        )}
      </div>
    </section>
  );
}
