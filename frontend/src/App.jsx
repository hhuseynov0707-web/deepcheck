import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import Badge from "./components/Badge.jsx";
import { CardIcon, RadarIcon, ShieldIcon } from "./components/icons.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Demo from "./pages/Demo.jsx";
import KvkkNotice from "./pages/KvkkNotice.jsx";

const VIEWS = [
  { to: "/demo", label: "Ödeme Demo", Icon: CardIcon },
  { to: "/dashboard", label: "SOC Dashboard", Icon: RadarIcon },
];

// A segmented control rather than two text links: the product is two views of
// one system, and a segment makes "you are here, and there is exactly one
// other place" readable at a glance.
function ViewSwitch() {
  return (
    <nav aria-label="Görünümler" className="min-w-0">
      <ul className="flex items-center gap-1 rounded-full border border-line bg-panel p-1">
        {VIEWS.map(({ to, label, Icon }) => (
          <li key={to} className="min-w-0 flex-1 sm:flex-none">
            <NavLink
              to={to}
              className={({ isActive }) =>
                `flex min-h-[2.25rem] items-center justify-center gap-2 rounded-full px-3 text-caption font-medium
                 transition-colors sm:px-4 ${
                   isActive
                     ? "bg-panel-raised text-ink shadow-panel ring-1 ring-line-strong"
                     : "text-ink-muted hover:bg-panel-raised/70 hover:text-ink"
                 }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={`h-4 w-4 shrink-0 ${isActive ? "text-accent" : ""}`} />
                  <span className="truncate">{label}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-canvas/85 backdrop-blur supports-[backdrop-filter]:bg-canvas/70">
      <div className="mx-auto flex max-w-[92rem] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-field border border-accent/35 bg-accent/10 text-accent"
          >
            <ShieldIcon className="h-5 w-5" />
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block text-lead font-semibold tracking-tight text-ink">DeepCheck</span>
            <span className="block text-eyebrow uppercase tracking-[0.09em] text-ink-faint">
              Davranışsal Bot Tespiti
            </span>
          </span>
        </div>

        {/* Honest marker, and it says what "demo" means here rather than just
            labelling the page. Nothing on this deployment takes money and no
            customer history in it belongs to a person. */}
        <Badge
          tone="neutral"
          size="sm"
          dot
          className="order-last ml-auto sm:order-last sm:ml-0"
          title="Bu ortamda gerçek ödeme alınmaz ve gerçek müşteri verisi yoktur."
        >
          Demo ortamı
        </Badge>

        <div className="order-last w-full sm:order-none sm:ml-auto sm:w-auto">
          <ViewSwitch />
        </div>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="mt-auto border-t border-line bg-canvas">
      <div className="mx-auto flex max-w-[92rem] flex-col gap-2 px-4 py-5 text-caption text-ink-faint sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p className="max-w-[70ch]">
          Gerçek müşteri verisi yoktur; demo müşteri geçmişleri sentetiktir ve gerçek ödeme alınmaz.
        </p>
        <a
          href="/kvkk"
          className="self-start rounded-field text-ink-muted underline underline-offset-4 transition-colors hover:text-ink sm:self-auto"
        >
          KVKK Aydınlatma Metni
        </a>
      </div>
    </footer>
  );
}

export default function App() {
  const location = useLocation();

  return (
    <div className="flex min-h-screen flex-col bg-canvas text-ink">
      <a className="skip-link" href="#icerik">
        İçeriğe geç
      </a>
      <Header />
      {/* Keyed on the path so each view fades in on arrival: the only motion in
          the shell, and it is there to say "this is a different screen". */}
      {/* tabIndex -1 so "İçeriğe geç" actually moves focus into the page and
          not just the URL hash. Programmatic focus does not match
          :focus-visible, so this draws no ring of its own. */}
      <main id="icerik" tabIndex={-1} key={location.pathname} className="flex-1 animate-fade-rise">
        <Routes>
          <Route path="/" element={<Navigate to="/demo" replace />} />
          <Route path="/demo" element={<Demo />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/kvkk" element={<KvkkNotice />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
