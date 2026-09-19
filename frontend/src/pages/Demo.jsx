import { useEffect, useRef, useState } from "react";

import CardTypeIcon from "../components/CardTypeIcon.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import VerificationModal from "../components/VerificationModal.jsx";
import { detectCardType, formatCardNumber, formatCvv, formatExpiry } from "../utils/cardFormat.js";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
// Shown inside the step-up modal so the demo code reads as a deliberate demo
// value. Must match the backend's DEMO_VERIFY_CODE (both come from .env).
const DEMO_VERIFY_CODE = import.meta.env.VITE_DEMO_VERIFY_CODE || "482913";

// Upper bound on waiting for the SDK's pre-charge flush. The SDK's fetch has
// no deadline of its own, so a stalled request would leave the customer
// watching "İşleniyor..." for as long as the browser keeps it open. Past this
// the charge goes ahead and the server decides on the evidence it already
// holds. The wait serves honest customers; it is not a control, since a
// client that wants its last seconds unrecorded can simply not send them --
// which is why freshness ("stale") is enforced on the server.
const FLUSH_WAIT_MS = 4000;

// The server has no observed session to decide on or to attach a step-up
// verification to (/api/demo/verify answers 404 for it), so the modal cannot
// help. Only a new session can, and a reload is how the customer gets one.
const RELOAD_MESSAGE = "Bu oturum için davranış verisi toplanamadı. Lütfen sayfayı yenileyip tekrar deneyin.";
const VERIFIED_BUT_DECLINED_MESSAGE =
  "Doğrulama kaydedildi ancak işlem yine onaylanmadı. Lütfen sayfayı yenileyip tekrar deneyin.";

// Demo shortcut for the per-customer profile (spec 5.7 / 14.2). In a real
// integration the MERCHANT's server names the customer when it calls
// /api/decision with its own credential; a browser-supplied reference would let
// anyone claim to be anyone. Here it is sent to /api/demo/charge, which files it
// under the reserved "demo" merchant namespace, so it can never touch a real
// merchant's customer. Nothing about the profile comes back to this page: a
// profile escalation arrives as the same generic step-up as every other one.
const DEFAULT_CUSTOMER_REF = "demo-musteri-1";
const CUSTOMER_REF_MAX_LENGTH = 64;
// Printable ASCII, the server's own shape rule (_CUSTOMER_REF_RE). Checked here
// only so a customer who types "ayşe" gets a sentence instead of an OTP prompt:
// the server rejects the same value with a 400 either way, so this is input
// help, not a control.
const CUSTOMER_REF_SHAPE = /^[\x20-\x7e]+$/;
const CUSTOMER_REF_ERROR =
  "Geçersiz müşteri referansı — yalnızca Türkçe karakter içermeyen harf, rakam ve işaretler kullanılabilir.";
const CUSTOMER_REF_HELP =
  "Demo kısayolu: gerçek entegrasyonda bu değeri tarayıcı değil, satıcının sunucusu gönderir. Lütfen gerçek kişisel bilgi girmeyin.";
// Spec 5.7: the page has to say where the value goes. It is filed under the
// merchant id "demo", which _load_merchant_keys refuses to hand to any real
// merchant, and its profile row is is_demo=true. Worded so it says nothing
// about whether this customer has a profile or what state it is in.
const CUSTOMER_REF_NAMESPACE_WARNING =
  "Uyarı: bu referans yalnızca ayrılmış demo ad alanında tutulur; hiçbir gerçek satıcının müşterisiyle eşleşemez ve raporlanan ölçümlere katılmaz.";
// GitHub renders the Markdown; the frontend bundle has no Markdown renderer and
// the notice is a document, not part of the checkout.
const KVKK_NOTICE_URL = "https://github.com/hhuseynov0707-web/deepcheck/blob/main/docs/kvkk-aydinlatma.md";

// No session id or token (the SDK never registered), or the server rejected
// the token. Distinct from a network failure, which still routes to step-up.
class NoSessionError extends Error {}

// The server refused the customer reference's shape (400). Nothing was
// decided, so neither step-up nor a reload can help; the field has to change.
class CustomerRefError extends Error {}

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
  const [hintMessage, setHintMessage] = useState(null);
  const [reloadMessage, setReloadMessage] = useState(null);
  const [customerRef, setCustomerRef] = useState(DEFAULT_CUSTOMER_REF);
  const [customerRefError, setCustomerRefError] = useState(null);
  // Consecutive "insufficient_evidence" answers. A ref, not state: it is read
  // by the charge that handleVerified starts, whose closure predates a render.
  const insufficientStreak = useRef(0);

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
  // A provisional score is one the server measured too little to stand behind.
  // Showing a colour for it would tell a customer who has been on the page for
  // two seconds that they look like a bot, which the data does not support.
  const hasScore =
    !scoreUnavailable &&
    typeof risk?.risk_score === "number" &&
    Number.isFinite(risk.risk_score) &&
    risk.provisional !== true;
  const riskScore = hasScore ? risk.risk_score : null;

  function sessionHeaders() {
    const sessionId = window.DeepCheck?.getSessionId?.();
    const token = window.DeepCheck?.getToken?.();
    if (!sessionId || !token) throw new NoSessionError("Oturum jetonu yok");
    return { sessionId, headers: { "Content-Type": "application/json", "X-DeepCheck-Token": token } };
  }

  // Trimmed, so "demo-musteri-1 " and "demo-musteri-1" are one demo customer
  // rather than two profiles that each never mature. Empty means "no customer
  // named", and the field is then left out of the request altogether.
  function chargeCustomerRef() {
    const value = customerRef.trim();
    return value || null;
  }

  // The periodic flush runs every 2 s, so without this up to two seconds of
  // the customer's most recent input -- the moments right before "Onayla" --
  // have not reached the server when it decides. flush() never rejects by
  // contract; the catch is for an SDK that breaks it. Older SDKs have no
  // flush(), and the charge then proceeds as it always did.
  async function flushBehaviour() {
    let timer;
    try {
      await Promise.race([
        Promise.resolve(window.DeepCheck?.flush?.()),
        new Promise((resolve) => {
          timer = window.setTimeout(resolve, FLUSH_WAIT_MS);
        }),
      ]);
    } catch (err) {
      console.error("[Demo] davranış verisi gönderilemedi:", err);
    } finally {
      window.clearTimeout(timer);
    }
  }

  // The charge itself happens on the server. This page sends the request and
  // renders whatever came back; it holds no rule that could be edited away.
  // Whether the payment goes through is decided by /api/demo/charge, which
  // applies the ladder, the minimum-evidence and freshness rules, and any
  // server-recorded step-up verification -- none of which this code can see
  // or influence.
  async function submitCharge({ afterVerification = false } = {}) {
    setBlockMessage(null);
    setWarnMessage(null);
    setHintMessage(null);
    setReloadMessage(null);
    setStatus("loading");

    await flushBehaviour();

    try {
      const { sessionId, headers } = sessionHeaders();
      const ref = chargeCustomerRef();
      const body = { session_id: sessionId, amount: total };
      if (ref !== null) body.customer_ref = ref;
      const res = await fetch(`${API_URL}/api/demo/charge`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      if (res.status === 401) throw new NoSessionError("Oturum jetonu reddedildi");
      // /api/demo/charge answers 400 only for a malformed customer reference.
      // Without a reference a 400 is unexplained and falls through to the
      // fail-closed branch below like any other error.
      if (res.status === 400 && ref !== null) throw new CustomerRefError("Musteri referansi reddedildi");
      if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
      const result = await res.json();
      const decision = result.decision || {};
      const insufficient = result.status !== "charged" && decision.reason === "insufficient_evidence";
      insufficientStreak.current = insufficient ? insufficientStreak.current + 1 : 0;

      if (result.status === "charged") {
        if (decision.action === "warn") setWarnMessage(decision.message || null);
        setStatus("success");
        window.setTimeout(() => setStatus("idle"), 2500);
        return;
      }

      setStatus("idle");
      if (decision.action === "block") {
        setBlockMessage(decision.message || "İşlem Reddedildi — Şüpheli Davranış Tespit Edildi");
      } else if (decision.reason === "unknown_session") {
        // The modal here would loop: verification 404s for a session the
        // server never observed, and the next charge says the same thing.
        setReloadMessage(RELOAD_MESSAGE);
      } else if (afterVerification) {
        // The server accepted the code a moment ago and still did not charge.
        // Opening the modal again would ask for a code that has already been
        // accepted, and the customer could cycle through it indefinitely.
        setReloadMessage(VERIFIED_BUT_DECLINED_MESSAGE);
      } else if (insufficient && insufficientStreak.current === 1) {
        // Too little behaviour observed yet. Asking for an OTP here would be
        // odd for a real customer who has been on the page for two seconds;
        // tell them to continue and try again.
        setHintMessage(decision.message || "Karar için yeterli davranış verisi yok, lütfen birkaç saniye sonra tekrar deneyin.");
      } else {
        // Every other verify outcome, including a second consecutive
        // insufficient_evidence: the hint has been shown once and did not
        // help, so a customer whose input keeps falling short of the evidence
        // bar is offered step-up instead of the same hint forever.
        // Verification unlocks it on the server.
        //
        // That includes "step_up", the one public reason the server collapses
        // cluster, conformal, ambiguous and the per-customer profile deviation
        // into. Telling them apart here would require the server to name which
        // check fired, which is the tuning signal it withholds.
        setShowVerifyModal(true);
      }
    } catch (err) {
      console.error("[Demo] ödeme isteği başarısız:", err);
      insufficientStreak.current = 0;
      setStatus("idle");
      if (err instanceof CustomerRefError) {
        setCustomerRefError(CUSTOMER_REF_ERROR);
      } else if (err instanceof NoSessionError) {
        setReloadMessage(RELOAD_MESSAGE);
      } else if (afterVerification) {
        setReloadMessage(VERIFIED_BUT_DECLINED_MESSAGE);
      } else {
        // Fail closed. A charge we could not complete is not a completed
        // charge, so the customer is routed to step-up rather than told
        // "success".
        setShowVerifyModal(true);
      }
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (status === "loading") return;
    const ref = chargeCustomerRef();
    if (ref !== null && !CUSTOMER_REF_SHAPE.test(ref)) {
      setCustomerRefError(CUSTOMER_REF_ERROR);
      return;
    }
    setCustomerRefError(null);
    submitCharge();
  }

  // Sends the step-up code to the server, which records the result on the
  // session. Resolves to { ok, message } for the modal; it never decides.
  async function verifyCode(code) {
    try {
      const { sessionId, headers } = sessionHeaders();
      const res = await fetch(`${API_URL}/api/demo/verify`, {
        method: "POST",
        headers,
        body: JSON.stringify({ session_id: sessionId, code }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) return { ok: false, message: body?.detail || "Doğrulama başarısız" };
      return { ok: body?.verified === true, message: body?.message };
    } catch (err) {
      console.error("[Demo] doğrulama isteği başarısız:", err);
      return { ok: false, message: "Doğrulama sunucusuna ulaşılamadı" };
    }
  }

  function handleVerified() {
    setShowVerifyModal(false);
    // Verification was recorded server-side; charging again lets the server
    // apply it. If it did not take (e.g. the verdict was "block"), the
    // server declines again and the page shows that.
    submitCharge({ afterVerification: true });
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
        <div
          role="status"
          aria-live="polite"
          className="max-w-4xl mx-auto mb-4 rounded-lg border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-400"
        >
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
              <label htmlFor="card-number" className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Kart Numarası</label>
              <div className="relative">
                <input
                  id="card-number"
                  type="text"
                  inputMode="numeric"
                  autoComplete="cc-number"
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
              <label htmlFor="card-name" className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Kart Üzerindeki İsim</label>
              <input
                id="card-name"
                type="text"
                autoComplete="cc-name"
                placeholder="AD SOYAD"
                value={cardName}
                onChange={(e) => setCardName(e.target.value.toUpperCase())}
                className={inputClass}
                required
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="card-expiry" className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Son Kullanma Tarihi</label>
                <input
                  id="card-expiry"
                  type="text"
                  inputMode="numeric"
                  autoComplete="cc-exp"
                  placeholder="AA/YY"
                  value={expiry}
                  onChange={(e) => setExpiry(formatExpiry(e.target.value))}
                  className={inputClass}
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="card-cvv" className="text-xs font-medium text-zinc-400 uppercase tracking-wider">CVV</label>
                <input
                  id="card-cvv"
                  type="text"
                  inputMode="numeric"
                  autoComplete="cc-csc"
                  placeholder="123"
                  value={cvv}
                  onChange={(e) => setCvv(formatCvv(e.target.value))}
                  className={inputClass}
                  required
                />
              </div>
            </div>

            {/* Dashed and set apart from the card fields on purpose: this is not
                something a real checkout asks its customer. It stands in for
                the merchant server naming the customer. */}
            <div className="flex flex-col gap-1.5 rounded-md border border-dashed border-zinc-700 p-3">
              <label htmlFor="customer-ref" className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                Müşteri Referansı (demo)
              </label>
              <input
                id="customer-ref"
                type="text"
                autoComplete="off"
                spellCheck={false}
                maxLength={CUSTOMER_REF_MAX_LENGTH}
                value={customerRef}
                onChange={(e) => {
                  setCustomerRef(e.target.value);
                  setCustomerRefError(null);
                }}
                aria-describedby={
                  customerRefError
                    ? "customer-ref-help customer-ref-namespace customer-ref-error"
                    : "customer-ref-help customer-ref-namespace"
                }
                aria-invalid={customerRefError ? "true" : undefined}
                className={`${inputClass} tracking-normal`}
              />
              <p id="customer-ref-help" className="text-xs text-zinc-500">
                {CUSTOMER_REF_HELP}
              </p>
              <p id="customer-ref-namespace" className="text-xs text-amber-400/90">
                {CUSTOMER_REF_NAMESPACE_WARNING}
              </p>
              {customerRefError && (
                <p id="customer-ref-error" role="alert" className="text-xs text-rose-400">
                  {customerRefError}
                </p>
              )}
              <a
                href={KVKK_NOTICE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="self-start text-xs text-zinc-400 underline underline-offset-2 hover:text-zinc-200 transition-colors duration-200"
              >
                KVKK Aydınlatma Metni
              </a>
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

            {/* Tek bir canlı bölge: ödeme sonucu ekran okuyucuya duyurulmazsa,
                görme engelli bir kullanıcı işlemin reddedildiğini fark etmez. */}
            <div role="status" aria-live="polite">
              {blockMessage && (
                <p className="text-center rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
                  {blockMessage}
                </p>
              )}

              {hintMessage && (
                <p className="text-center rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-2 text-sm text-zinc-300">
                  {hintMessage}
                </p>
              )}

              {reloadMessage && (
                <div className="flex flex-col items-center gap-2 rounded-md border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
                  <p className="text-center">{reloadMessage}</p>
                  <button
                    type="button"
                    onClick={() => window.location.reload()}
                    className="rounded-md border border-amber-500/30 px-3 py-1 text-xs font-medium text-amber-300 hover:bg-amber-500/10 transition-colors duration-200"
                  >
                    Sayfayı Yenile
                  </button>
                </div>
              )}

              {status === "success" && (
                <p className="text-center rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
                  Ödeme başarıyla alındı (demo).
                </p>
              )}
            </div>
          </form>

          {/* No "confidence" figure here. The API's `confidence` is
              max(p, 1-p) of the latest single flush, not a calibrated
              probability of being right, while the badge shows the smoothed
              session score. The two can point opposite ways: smoothing damps
              a drop, so one human-looking flush after bot-looking ones shows
              a red badge beside "95%" that is certainty in the HUMAN class.
              The analyst view keeps the figure, labelled for what it is. */}
          {hasScore && (
            <p className="font-mono text-xs text-zinc-500 text-center">
              Yanıt süresi: {risk.response_time_ms} ms
            </p>
          )}

          <div className="pt-4 border-t border-zinc-800 flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-500">
            {/* This used to claim "256-bit SSL" on a page served over plain
                http://localhost. What is stated instead holds for this code:
                the charge request carries only session_id, amount and the
                demo customer reference -- never a card field -- and the SDK
                records keydown timestamps, never key values. */}
            <div className="flex items-center gap-1.5">
              <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 11v5M12 8h.01" />
              </svg>
              <span>Demo ortamı — gerçek ödeme alınmaz, kart bilgileri sunucuya gönderilmez</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span>DeepCheck ile korunuyor</span>
            </div>
          </div>
        </div>
      </div>

      {showVerifyModal && (
        <VerificationModal
          onVerified={handleVerified}
          onClose={() => setShowVerifyModal(false)}
          verify={verifyCode}
          demoCode={DEMO_VERIFY_CODE}
        />
      )}
    </div>
  );
}
