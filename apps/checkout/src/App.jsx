import { DemoPayWordmark, TechStoreLogo } from "./components/Brand.jsx";
import { PRIVACY_PATH } from "./lib/routes.js";
import Checkout from "./pages/Checkout.jsx";
import Privacy from "./pages/Privacy.jsx";

// Two pages and no router: "/" is the checkout, "/gizlilik" the notice. Every
// other path falls back to the checkout (nginx serves index.html for it).

// No padlock and no "güvenli" anywhere on this page. The LAN demo serves it
// over plain http (docs/canli-demo.md), where the browser's own address bar
// says "Güvenli değil" as soon as a card field is typed into; a page that
// claims the opposite next to it is making a security claim its transport
// contradicts. A real DemoPay page would be https-only.
function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-white/90 backdrop-blur supports-[backdrop-filter]:bg-white/80">
      <div className="mx-auto flex h-16 max-w-5xl items-center justify-between gap-3 px-4 sm:px-6">
        <TechStoreLogo />
        <p className="flex items-center gap-1.5 text-[13px] text-ink-muted">
          {/* The space is real text, not only the flex gap: without it the
              lockup is read (and copied) as "DemoPayile". */}
          <DemoPayWordmark />{" "}
          <span className="whitespace-nowrap">ile ödeme</span>
        </p>
      </div>
    </header>
  );
}

function SiteFooter() {
  return (
    <footer className="border-t border-line bg-white">
      <div className="mx-auto max-w-5xl space-y-2 px-4 py-6 text-[13px] leading-5 text-ink-muted sm:px-6">
        <p className="font-medium text-ink">Demo ortamı - gerçek ödeme alınmaz.</p>
        <p>
          Güvenliğiniz için bu sayfada fare/kaydırma hareketi ve tuşlara basılma zamanları ölçülür; ne yazdığınız
          ölçülmez.{" "}
          <a
            href={PRIVACY_PATH}
            target="_blank"
            rel="noopener noreferrer"
            className="cursor-pointer rounded-sm font-medium text-brand underline underline-offset-2 transition-colors duration-150 hover:text-brand-hover"
          >
            Aydınlatma Metni
          </a>
        </p>
        <p>TechStore kurgusal bir mağaza, DemoPay kurgusal bir ödeme markasıdır.</p>
      </div>
    </footer>
  );
}

export default function App({ path = window.location.pathname }) {
  const isPrivacy = path.replace(/\/+$/, "") === PRIVACY_PATH;

  return (
    <div className="flex min-h-screen flex-col">
      <a className="skip-link" href="#icerik">
        İçeriğe geç
      </a>
      <SiteHeader />
      {/* tabIndex -1 so "İçeriğe geç" moves focus, not just the URL hash. */}
      <main id="icerik" tabIndex={-1} className="flex-1 focus:outline-none">
        {isPrivacy ? <Privacy /> : <Checkout />}
      </main>
      <SiteFooter />
    </div>
  );
}
