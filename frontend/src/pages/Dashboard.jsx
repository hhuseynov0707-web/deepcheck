import { useEffect, useMemo, useState } from "react";

import MetricCard from "../components/MetricCard.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import RiskChart, { ShapBarChart } from "../components/RiskChart.jsx";
import SessionTable from "../components/SessionTable.jsx";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const REFRESH_MS = 3000;

// The SOC endpoints expose every customer's live session id and score, so
// they sit behind DASHBOARD_KEY. That key is NOT built into this bundle: a
// key compiled into frontend JavaScript is served to everyone who opens the
// page, so `view-source` defeats the header check and what looks like
// authentication is decoration. The analyst types it, and it is kept in
// sessionStorage -- cleared when the tab closes, not shared with other tabs,
// and never written to disk.
//
// A typed key in a browser is still a shared secret rather than a user
// identity. The real answer is an operator login with per-user sessions and
// an audit trail; this is the smallest change that stops the key being
// public, and it is honest about what it is.
const KEY_STORAGE = "deepcheck.dashboardKey";
const UNAUTHORIZED = "Yetkisiz erişim — panoya erişim anahtarı geçersiz";

function readStoredKey() {
  try {
    return window.sessionStorage.getItem(KEY_STORAGE) || "";
  } catch {
    // Private mode or blocked storage: the analyst just retypes the key.
    return "";
  }
}

function storeKey(value) {
  try {
    if (value) window.sessionStorage.setItem(KEY_STORAGE, value);
    else window.sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* not fatal: the key stays in memory for this page view */
  }
}

class UnauthorizedError extends Error {
  constructor() {
    super(UNAUTHORIZED);
    this.name = "UnauthorizedError";
  }
}

export default function Dashboard() {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [error, setError] = useState(null);
  const [dashboardKey, setDashboardKey] = useState(readStoredKey);
  const [keyInput, setKeyInput] = useState("");

  // One place decides what a rejected key means, so a 401 from either poll
  // drops the analyst back to the key prompt instead of leaving a dead page
  // refreshing every 3 seconds.
  function clearKey(message) {
    storeKey("");
    setDashboardKey("");
    setSessions([]);
    setSelectedDetail(null);
    setSelectedId(null);
    setError(message);
  }

  const handleAuthFailure = () => clearKey(UNAUTHORIZED);
  const logout = () => clearKey(null);

  function submitKey(e) {
    e.preventDefault();
    const value = keyInput.trim();
    if (!value) return;
    storeKey(value);
    setDashboardKey(value);
    setKeyInput("");
    setError(null);
  }

  useEffect(() => {
    if (!dashboardKey) return;
    let cancelled = false;

    async function fetchSessions() {
      try {
        const res = await fetch(`${API_URL}/api/sessions`, {
          headers: { "X-Dashboard-Key": dashboardKey },
        });
        if (res.status === 401) throw new UnauthorizedError();
        if (!res.ok) throw new Error("Session'lar alınamadı");
        const data = await res.json();
        if (!cancelled) {
          setSessions(data);
          setError(null);
          // Functional update instead of reading `selectedId` from the
          // closure: depending on it forced this effect to tear down and
          // restart the 3s poll on every click in the session list, which
          // both dropped the current polling cycle and re-fetched immediately
          // on each selection.
          if (data.length > 0) setSelectedId((prev) => prev ?? data[0].session_id);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) handleAuthFailure();
        else setError(err.message);
      }
    }

    fetchSessions();
    const timer = window.setInterval(fetchSessions, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [dashboardKey]);

  useEffect(() => {
    if (!selectedId || !dashboardKey) return;
    let cancelled = false;

    async function fetchDetail() {
      try {
        const res = await fetch(`${API_URL}/api/score/${selectedId}`, {
          headers: { "X-Dashboard-Key": dashboardKey },
        });
        if (res.status === 401) throw new UnauthorizedError();
        if (!res.ok) throw new Error("Session detayı alınamadı");
        const data = await res.json();
        if (!cancelled) setSelectedDetail(data);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) handleAuthFailure();
        else setError(err.message);
      }
    }

    fetchDetail();
    const timer = window.setInterval(fetchDetail, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId, dashboardKey]);

  const metrics = useMemo(() => {
    const total = sessions.length;
    const avgRisk = total ? sessions.reduce((sum, s) => sum + (s.risk_score || 0), 0) / total : 0;
    const botCount = sessions.filter((s) => s.label === "Bot Tespit Edildi").length;
    const responseValues = sessions.filter((s) => typeof s.response_time_ms === "number");
    const avgResponse = responseValues.length
      ? responseValues.reduce((sum, s) => sum + s.response_time_ms, 0) / responseValues.length
      : 0;
    return { total, avgRisk, botCount, avgResponse };
  }, [sessions]);

  if (!dashboardKey) {
    return (
      <div className="max-w-md mx-auto px-6 py-20">
        <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-6 shadow-xl shadow-black/50 space-y-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-50">SOC Dashboard</h1>
            <p className="text-sm text-zinc-400 mt-1">
              Bu pano tüm oturumların canlı risk verisini gösterir. Devam etmek için erişim
              anahtarını girin.
            </p>
          </div>

          <form onSubmit={submitKey} className="space-y-3">
            <label
              htmlFor="dashboard-key"
              className="block text-xs font-medium text-zinc-400 uppercase tracking-wider"
            >
              Pano Erişim Anahtarı
            </label>
            <input
              id="dashboard-key"
              type="password"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder="••••••••••••"
              autoFocus
              className="w-full bg-[#09090b] text-zinc-100 border border-zinc-800 rounded-md p-3 font-mono text-sm focus:outline-none focus:border-zinc-700 transition-colors duration-200"
            />
            {error && (
              <p className="rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={!keyInput.trim()}
              className="w-full bg-zinc-100 hover:bg-zinc-200 text-zinc-900 font-medium py-3 px-4 rounded-md transition-colors duration-200 cursor-pointer text-sm tracking-wide disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Panoya Gir
            </button>
            <p className="text-xs text-zinc-500">
              Anahtar yalnızca bu sekmede saklanır (sessionStorage) ve sekme kapandığında silinir.
            </p>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">SOC Dashboard</h1>
          <p className="text-zinc-400 text-sm mt-1">
            Tüm session'lar gerçek zamanlı izleniyor · her {REFRESH_MS / 1000} saniyede yenilenir
          </p>
        </div>
        <div className="flex items-center gap-3">
          {error && (
            <span className="rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-sm text-rose-400">
              {error}
            </span>
          )}
          <button
            type="button"
            onClick={logout}
            className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 transition-colors duration-200 cursor-pointer"
          >
            Oturumu Kapat
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="Toplam Oturum" value={metrics.total} decimals={0} />
        <MetricCard
          label="Ortalama Risk Skoru"
          value={metrics.avgRisk}
          decimals={1}
          accent={metrics.avgRisk >= 60 ? "text-orange-400" : "text-emerald-400"}
        />
        <MetricCard label="Tespit Edilen Bot" value={metrics.botCount} decimals={0} accent="text-rose-400" />
        <MetricCard label="Ortalama Yanıt Süresi" value={metrics.avgResponse} decimals={1} suffix=" ms" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-3">
          <h2 className="text-lg font-semibold tracking-tight text-zinc-50">Session Listesi</h2>
          <SessionTable sessions={sessions} selectedId={selectedId} onSelect={setSelectedId} />
        </div>

        <div className="space-y-6">
          <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-5 shadow-xl shadow-black/50">
            <h2 className="text-lg font-semibold tracking-tight text-zinc-50 mb-3">Seçili Session</h2>
            {selectedDetail ? (
              <div className="space-y-3">
                <p className="font-mono text-xs text-zinc-500 break-all">
                  {selectedDetail.session_id}
                </p>
                <RiskBadge riskScore={selectedDetail.risk_score} size="lg" />
                <p className="text-sm text-zinc-400">
                  Güven: {(selectedDetail.confidence * 100).toFixed(0)}% · Yanıt süresi:{" "}
                  {selectedDetail.response_time_ms} ms
                </p>
              </div>
            ) : (
              <p className="text-zinc-400 text-sm">Bir session seçin.</p>
            )}
          </div>

          {selectedDetail?.shap_explanation?.length > 0 && (
            <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-5 shadow-xl shadow-black/50">
              <h2 className="text-lg font-semibold tracking-tight text-zinc-50 mb-3">
                En Etkili 3 Özellik (SHAP)
              </h2>
              <ShapBarChart shapExplanation={selectedDetail.shap_explanation} />
            </div>
          )}
        </div>
      </div>

      <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-5 shadow-xl shadow-black/50">
        <h2 className="text-lg font-semibold tracking-tight text-zinc-50 mb-3">Risk Skoru Geçmişi</h2>
        {selectedDetail?.history?.length > 0 ? (
          <RiskChart history={selectedDetail.history} />
        ) : (
          <p className="text-zinc-400 text-sm">Bu session için henüz veri yok.</p>
        )}
      </div>
    </div>
  );
}
