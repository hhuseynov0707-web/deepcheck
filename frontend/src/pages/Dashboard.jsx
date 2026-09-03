import { useEffect, useMemo, useState } from "react";

import MetricCard from "../components/MetricCard.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import RiskChart, { ShapBarChart } from "../components/RiskChart.jsx";
import SessionTable from "../components/SessionTable.jsx";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const REFRESH_MS = 3000;

// The SOC endpoints are operator-only now (they expose every session's verdict
// and feature history). This key is read from the build environment, which is
// honest about what it is: a deployment-level gate suitable for a demo, not
// per-user authentication. A production dashboard needs a real operator login
// and a server-side session -- anything shipped in a frontend bundle is
// readable by whoever loads the page.
const OPERATOR_KEY = import.meta.env.VITE_OPERATOR_KEY || "";

function operatorHeaders() {
  return { "X-Operator-Key": OPERATOR_KEY };
}

export default function Dashboard() {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchSessions() {
      try {
        const res = await fetch(`${API_URL}/api/sessions`, { headers: operatorHeaders() });
        if (res.status === 401) throw new Error("Operatör anahtarı geçersiz veya eksik");
        if (!res.ok) throw new Error("Sessionlar alınamadı");
        const data = await res.json();
        if (!cancelled) {
          setSessions(data);
          setError(null);
          if (!selectedId && data.length > 0) setSelectedId(data[0].session_id);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    fetchSessions();
    const timer = window.setInterval(fetchSessions, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;

    async function fetchDetail() {
      try {
        const res = await fetch(`${API_URL}/api/score/${selectedId}`, {
          headers: operatorHeaders(),
        });
        if (res.status === 401) throw new Error("Operatör anahtarı geçersiz veya eksik");
        if (!res.ok) throw new Error("Session detayı alınamadı");
        const data = await res.json();
        if (!cancelled) setSelectedDetail(data);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    fetchDetail();
    const timer = window.setInterval(fetchDetail, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId]);

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

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">SOC Dashboard</h1>
          <p className="text-zinc-400 text-sm mt-1">
            Tüm session'lar gerçek zamanlı izleniyor · her {REFRESH_MS / 1000} saniyede yenilenir
          </p>
        </div>
        {error && (
          <span className="rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-sm text-rose-400">
            {error}
          </span>
        )}
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
