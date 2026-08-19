import { Navigate, Route, Routes, Link, useLocation } from "react-router-dom";

import Dashboard from "./pages/Dashboard.jsx";
import Demo from "./pages/Demo.jsx";

function NavBar() {
  const location = useLocation();
  const linkClass = (path) =>
    `px-4 py-2 rounded-md text-sm font-medium tracking-wide transition-colors duration-200 ease-out ${
      location.pathname === path
        ? "bg-zinc-800 text-zinc-50"
        : "text-zinc-400 hover:text-zinc-50 hover:bg-zinc-900"
    }`;

  return (
    <nav className="flex items-center justify-between px-6 py-4 border-b border-zinc-800 bg-[#18181b]">
      <span className="text-lg font-semibold tracking-tight text-zinc-50">DeepCheck</span>
      <div className="flex gap-2">
        <Link className={linkClass("/demo")} to="/demo">
          Ödeme Demo
        </Link>
        <Link className={linkClass("/dashboard")} to="/dashboard">
          SOC Panosu
        </Link>
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-[#09090b]">
      <NavBar />
      <Routes>
        <Route path="/" element={<Navigate to="/demo" replace />} />
        <Route path="/demo" element={<Demo />} />
        <Route path="/dashboard" element={<Dashboard />} />
      </Routes>
    </div>
  );
}
