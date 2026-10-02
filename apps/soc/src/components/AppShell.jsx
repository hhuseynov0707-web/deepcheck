import { ShieldIcon } from "./icons.jsx";

// The SOC's frame: a slim top bar, the page, a one-line footer.
//
// The bar is the only glass in the app -- a translucent canvas with a light
// blur and a 1px edge, sticky from lg up so the live controls stay in reach
// while the analyst scrolls a long session. Below lg it scrolls away with the
// page: on a phone, three rows of sticky controls would eat the screen.

export function Wordmark() {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span
        aria-hidden="true"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-field border border-accent/35 bg-accent/10 text-accent"
      >
        <ShieldIcon className="h-5 w-5" />
      </span>
      <span className="min-w-0 leading-tight">
        <span className="flex items-center gap-1.5 text-lead font-semibold tracking-tight text-ink">
          DeepCheck
          <span className="rounded-md border border-line-strong bg-panel-raised px-1.5 text-eyebrow font-semibold uppercase tracking-[0.08em] text-ink-muted">
            SOC
          </span>
        </span>
        <span className="block truncate text-eyebrow uppercase tracking-[0.08em] text-ink-faint">
          Güvenlik Operasyon Merkezi
        </span>
      </span>
    </div>
  );
}

export default function AppShell({ status, actions, children }) {
  return (
    <div className="flex min-h-screen flex-col bg-canvas text-ink">
      <a className="skip-link" href="#icerik">
        İçeriğe geç
      </a>
      <header className="z-40 border-b border-line/80 bg-canvas/80 backdrop-blur-md supports-[backdrop-filter]:bg-canvas/65 lg:sticky lg:top-0">
        <div className="mx-auto flex max-w-[100rem] flex-wrap items-center gap-x-5 gap-y-3 px-4 py-3 sm:px-6">
          <Wordmark />
          {/* Plain text, not a link: the store is its own application on its
              own address, and the SOC has no business navigating to it. */}
          <p className="hidden border-l border-line pl-5 text-caption text-ink-faint md:block">
            Ödeme tarafı: TechStore (ayrı uygulama)
          </p>
          {status && <div className="min-w-0">{status}</div>}
          {actions && <div className="flex flex-wrap items-center gap-2 lg:ml-auto">{actions}</div>}
        </div>
      </header>
      {/* tabIndex -1 so "İçeriğe geç" moves focus into the page, not just the
          URL hash; programmatic focus draws no :focus-visible ring. */}
      <main id="icerik" tabIndex={-1} className="flex-1 animate-fade-rise">
        {children}
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto max-w-[100rem] px-4 py-4 text-caption text-ink-faint sm:px-6">
          Demo ortamı: gerçek müşteri verisi ve gerçek ödeme yoktur.
        </div>
      </footer>
    </div>
  );
}
