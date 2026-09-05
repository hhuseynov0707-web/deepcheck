import { useEffect, useState } from "react";

import CardTypeIcon from "../components/CardTypeIcon.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import VerificationModal from "../components/VerificationModal.jsx";
import { detectCardType, formatCardNumber, formatCvv, formatExpiry } from "../utils/cardFormat.js";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const ORDER = {
  merchant: "TechStore",
  product: "Mekanik Klavye — RGB Aydınlatmalı",
  subtotal: 1699.0,
  taxRate: 0.2,
};

function formatCurrency(value) {
  return new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

export default function Demo() {
  const [cardNumber, setCardNumber] = useState("");
  const [cardName, setCardName] = useState("");
  const [expiry, setExpiry] = useState("");
  const [cvv, setCvv] = useState("");
  const [risk, setRisk] = useState(null);
  const [scoreUnavailable, setScoreUnavailable] = useState(false);
  const [status, setStatus] = useState("idle"); // idle | loading | success
  const [showVerifyModal, setShowVerifyModal] = useState(false);
  const [blockMessage, setBlockMessage] = useState(null);
  const [warnMessage, setWarnMessage] = useState(null);

  useEffect(() => {
    if (!window.DeepCheck) {
      console.error("[Demo] DeepCheck SDK yüklenemedi.");
      // A missing SDK is not a clean session -- it is the absence of any
      // evidence at all, which is precisely how an automated client presents.
      setScoreUnavailable(true);
      return;
    }

    window.DeepCheck.init({
      apiUrl: API_URL,
      intervalMs: 2000,
      onUpdate: (result) => {
        setRisk(result);
        setScoreUnavailable(false);
      },
      onError: () => setScoreUnavailable(true),
    });

    return () => window.DeepCheck.stop();
  }, []);

  const tax = ORDER.subtotal * ORDER.taxRate;
  const total = ORDER.subtotal + tax;
  const cardType = detectCardType(cardNumber);

  // The badge is DISPLAY ONLY. Nothing on this page decides whether the
  // payment goes through any more: the 40/60/80 ladder lives behind
  // POST /api/decision, where a page the attacker controls cannot edit it
  // away. What used to be here was a client-side gate that an attacker could
  // simply delete, and that defaulted to "allow" whenever the score was
  // missing -- which is exactly the state a client that never runs the SDK
  // is in.
  const hasScore =
    !scoreUnavailable && typeof risk?.risk_score === "number" && Number.isFinite(risk.risk_score);
  const riskScore = hasScore ? risk.risk_score : null;

  function processPayment() {
    setStatus("loading");
    window.setTimeout(() => {
      setStatus("success");
      window.setTimeout(() => setStatus("idle"), 2500);
    }, 1000);
  }

  function applyDecision(decision) {
    const action = decision?.action;
    if (action === "block") {
      setStatus("idle");
      setBlockMessage(decision.message || "İşlem Reddedildi — Şüpheli Davranış Tespit Edildi");
      return;
    }
    if (action === "verify") {
      setStatus("idle");
      setShowVerifyModal(true);
      return;
    }
    if (action === "warn") {
      setWarnMessage(decision.message || null);
    }
    processPayment();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (status === "loading") return;

    setBlockMessage(null);
    setWarnMessage(null);
    setStatus("loading");

    try {
      const sessionId = window.DeepCheck?.getSessionId?.();
      const token = window.DeepCheck?.getToken?.();
      if (!sessionId || !token) throw new Error("Oturum jetonu yok");

      const res = await fetch(`${API_URL}/api/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-DeepCheck-Token": token },
        body: JSON.stringify({ session_id: sessionId }),
      });
      if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
      applyDecision(await res.json());
    } catch (err) {
      // Fail closed. A decision we could not obtain is not an approval, so
      // the payment goes to step-up verification rather than through.
      console.error("[Demo] karar alınamadı:", err);
      setStatus("idle");
      setShowVerifyModal(true);
    }
  }

  function handleVerified() {
    setShowVerifyModal(false);
    processPayment();
  }

  const inputClass =
    "w-full bg-[#09090b] text-zinc-100 border border-zinc-800 rounded-md p-3 font-mono text-sm tracking-widest focus:outline-none focus:border-zinc-700 transition-colors duration-200 ease-out placeholder-zinc-600";

  return (
    <div className="min-h-[calc(100vh-64px)] px-4 py-10">
      <div className="max-w-4xl mx-auto flex items-center justify-end mb-4">
        {hasScore ? (
          <RiskBadge riskScore={riskScore} size="lg" />
        ) : scoreUnavailable ? (
          <div className="rounded-full border border-zinc-700 bg-zinc-800/60 px-4 py-2 text-sm text-zinc-300">
            Risk skoru alınamadı — ek doğrulama uygulanacak
          </div>
        ) : (
          <div className="text-sm text-zinc-400">Risk skoru hesaplanıyor...</div>
        )}
      </div>

      {warnMessage && (
        <div className="max-w-4xl mx-auto mb-4 rounded-lg border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-400">
          {warnMessage}
        </div>
      )}

      <div className="max-w-4xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Order summary */}
        <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-6 shadow-xl shadow-black/50 flex flex-col gap-4">
          <p className="text-xs font-medium text-zinc-400 uppercase tracking-wider">{ORDER.merchant}</p>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-50">Ödeme Özeti</h1>

          <div className="rounded-md bg-[#09090b] border border-zinc-800 p-4">
            <p className="text-sm text-zinc-300">{ORDER.product}</p>
          </div>

          <div className="space-y-2 text-sm text-zinc-400">
            <div className="flex justify-between">
              <span>Ara Toplam</span>
              <span className="font-mono text-zinc-300">₺{formatCurrency(ORDER.subtotal)}</span>
            </div>
            <div className="flex justify-between">
              <span>KDV (%{ORDER.taxRate * 100})</span>
              <span className="font-mono text-zinc-300">₺{formatCurrency(tax)}</span>
            </div>
          </div>

          <div className="border-t border-zinc-800 mt-2 pt-4 flex justify-between items-baseline">
            <span className="text-zinc-300 font-medium">Toplam</span>
            <span className="text-2xl font-semibold font-mono text-zinc-50">₺{formatCurrency(total)}</span>
          </div>
        </div>

        {/* Card form */}
        <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-6 shadow-xl shadow-black/50 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold tracking-tight text-zinc-50 uppercase">Kart Bilgileri</h3>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Kart Numarası</label>
              <div className="relative">
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="1234 5678 9012 3456"
                  value={cardNumber}
                  onChange={(e) => setCardNumber(formatCardNumber(e.target.value))}
                  className={`${inputClass} pr-14`}
                  required
                />
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  <CardTypeIcon type={cardType} className="h-6 w-10" />
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Kart Üzerindeki İsim</label>
              <input
                type="text"
                placeholder="AD SOYAD"
                value={cardName}
                onChange={(e) => setCardName(e.target.value.toUpperCase())}
                className={inputClass}
                required
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Son Kullanma Tarihi</label>
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="AA/YY"
                  value={expiry}
                  onChange={(e) => setExpiry(formatExpiry(e.target.value))}
                  className={inputClass}
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">CVV</label>
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="123"
                  value={cvv}
                  onChange={(e) => setCvv(formatCvv(e.target.value))}
                  className={inputClass}
                  required
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={status === "loading"}
              className="w-full bg-zinc-100 hover:bg-zinc-200 text-zinc-900 font-medium py-3 px-4 rounded-md transition-colors duration-200 cursor-pointer text-sm tracking-wide shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 mt-2"
            >
              {status === "loading" && (
                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
              )}
              {status === "loading" ? "İşleniyor..." : `₺${formatCurrency(total)} Onayla`}
            </button>

            {blockMessage && (
              <p className="text-center rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
                {blockMessage}
              </p>
            )}

            {status === "success" && (
              <p className="text-center rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
                Ödeme başarıyla alındı (demo).
              </p>
            )}
          </form>

          {hasScore && (
            <p className="font-mono text-xs text-zinc-500 text-center">
              Yanıt süresi: {risk.response_time_ms} ms · Güven: {(risk.confidence * 100).toFixed(0)}%
            </p>
          )}

          <div className="pt-4 border-t border-zinc-800 flex items-center justify-between text-xs text-zinc-500">
            <div className="flex items-center gap-1.5">
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="5" y="11" width="14" height="9" rx="2" />
                <path d="M8 11V7a4 4 0 018 0v4" />
              </svg>
              <span>256-bit SSL ile şifrelenmiştir</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span>DeepCheck ile korunuyor</span>
            </div>
          </div>
        </div>
      </div>

      {showVerifyModal && (
        <VerificationModal onVerified={handleVerified} onClose={() => setShowVerifyModal(false)} />
      )}
    </div>
  );
}
