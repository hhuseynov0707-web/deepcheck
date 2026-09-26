import { useEffect, useId, useRef, useState } from "react";

import CardTypeIcon from "../components/CardTypeIcon.jsx";
import DecisionLadder from "../components/DecisionLadder.jsx";
import LiveScorePanel from "../components/LiveScorePanel.jsx";
import SyntheticBadge from "../components/SyntheticBadge.jsx";
import VerificationModal from "../components/VerificationModal.jsx";
import {
  Alert,
  Button,
  Card,
  CardWell,
  Disclosure,
  Field,
  Input,
  LockIcon,
  RefreshIcon,
  SectionHeading,
  Select,
  ShieldIcon,
  riskLevelFor,
} from "../components/ui.js";
import { SYNTHETIC_DEMO_CUSTOMERS, syntheticCustomerLabel } from "../demoCustomers.js";
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
// The retention sentence is backend/main.py's demo rule: everything a visitor
// leaves in the demo namespace is swept on the session's clock
// (ROW_RETENTION_HOURS, 24 by default).
//
// It stays verbatim, and it moved into a Disclosure rather than out of the
// page: three lines of amber small print in the middle of a payment form are
// not read by anyone, while one keystroke away it is still in the document,
// still in the accessibility tree, still wired into the field's
// aria-describedby and still found by ctrl-F.
const CUSTOMER_REF_NAMESPACE_WARNING =
  "Uyarı: bu referans yalnızca ayrılmış demo ad alanında tutulur; hiçbir gerçek satıcının müşterisiyle eşleşemez ve raporlanan ölçümlere katılmaz. Bu ödemeden demo ad alanında kalan her şey 24 saat içinde silinir.";
// The jury prototype's SYNTHETIC demo customers (backend/demo_seed.py), offered
// beside free entry. The team has no customer base, so these are simulator
// identities with a seeded history -- and the page says so three times: in
// every option label, in the line under the selector, and in a badge beside the
// reference field whenever one of them is chosen. All three are static facts
// about the seed, not something the server said about a profile, so the rule
// above ("nothing about the profile comes back to this page") still holds: a
// juror paying as Ayşe learns nothing from the server that the label did not
// state.
//
// Free entry stays the DEFAULT on purpose. The page's main demonstration is the
// behavioural score, and a default that names a mature synthetic customer
// would put the profile layer's step-up in front of every visitor who simply
// fills in the card, blurring which check asked for the code. Choosing a
// synthetic customer is a deliberate act of the presenter.
const CUSTOM_CUSTOMER = "serbest";

function listInTurkish(names) {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} ve ${names[names.length - 1]}`;
}

const SYNTHETIC_CUSTOMERS_NOTE = `${listInTurkish(
  SYNTHETIC_DEMO_CUSTOMERS.map((c) => c.name),
)} sentetik demo müşterileridir: geçmişleri simülatörle üretildi, gerçek kişi değildir.`;

// Served by this app (pages/KvkkNotice.jsx renders docs/kvkk-aydinlatma.md).
// It used to point at the file on GitHub, which did not exist -- a 404 on the
// jury-facing link -- and could only ever work for a pushed, public repository,
// never on an offline LAN demo.
const KVKK_NOTICE_URL = "/kvkk";

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
  // How many scored windows the SDK has handed back, i.e. how many
  // /api/analyze responses reached onUpdate. It is a count of things that
  // happened, not an estimate of anything: a failed flush routes to onError
  // and is not counted.
  const [windowCount, setWindowCount] = useState(0);
  // Consecutive "insufficient_evidence" answers. A ref, not state: it is read
  // by the charge that handleVerified starts, whose closure predates a render.
  const insufficientStreak = useRef(0);
  const namespaceNoteId = useId();

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
        setWindowCount((n) => n + 1);
      },
      onError: () => setScoreUnavailable(true),
    });

    return () => window.DeepCheck.stop();
  }, []);

  const tax = ORDER.subtotal * ORDER.taxRate;
  const total = ORDER.subtotal + tax;
  const cardType = detectCardType(cardNumber);

  // The panel is DISPLAY ONLY. Nothing on this page decides whether the
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
  // Only so the ladder can mark the band the live score is in. Resolved by the
  // same published table the panel uses; nothing branches on it.
  const activeBandKey = hasScore ? riskLevelFor(riskScore).key : null;

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

  // Derived from the reference field rather than stored beside it, so the two
  // can never disagree: the field always shows the reference that will be
  // sent, and typing a synthetic customer's reference selects that customer.
  const selectedSynthetic = SYNTHETIC_DEMO_CUSTOMERS.find((c) => c.ref === customerRef.trim());
  const customerChoice = selectedSynthetic ? selectedSynthetic.ref : CUSTOM_CUSTOMER;

  function chooseCustomer(value) {
    setCustomerRef(value === CUSTOM_CUSTOMER ? DEFAULT_CUSTOMER_REF : value);
    setCustomerRefError(null);
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
        // cluster, conformal, ambiguous, sequential, the per-customer profile
        // deviation and the per-customer decision limit into. Telling them apart here would require the server to name which
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
      if (res.status === 401) {
        // The session token expired (30 minutes) or was rejected while the
        // prompt was open. No code can help now, and the charge path answers
        // the same 401 with the reload message -- so does this one, instead
        // of showing the server's untranslated detail inside the prompt.
        setShowVerifyModal(false);
        setReloadMessage(RELOAD_MESSAGE);
        return { ok: false };
      }
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

  return (
    <div className="mx-auto w-full max-w-[76rem] px-4 pb-16 pt-8 sm:px-6 sm:pt-10">
      <header className="max-w-[46rem]">
        <p className="eyebrow">Canlı demo</p>
        <h1 className="mt-2 text-h1 font-semibold tracking-tight text-ink sm:text-display">
          Ödeme anında davranış analizi
        </h1>
        <p className="mt-3 max-w-[64ch] text-body leading-relaxed text-ink-muted">
          Formu doldurun: SDK her 2 saniyede bir davranış penceresi gönderir ve panel sunucunun döndürdüğü
          skoru gösterir. &laquo;Onayla&raquo;ya bastığınızda kararı sunucu verir, bu sayfa değil.
        </p>
      </header>

      {/* The product's output, above the fold and full width: it is the thing
          this page exists to show. It holds no focusable element, so leading
          with it costs a keyboard user nothing. */}
      <div className="mt-7">
        <LiveScorePanel
          risk={risk}
          hasScore={hasScore}
          unavailable={scoreUnavailable}
          windowCount={windowCount}
        />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-12 lg:items-start">
        {/* THE MERCHANT'S CHECKOUT: everything a real customer would see, and
            nothing else. The demo harness is a separate card, on the other
            side of the page, drawn differently on purpose. */}
        <section aria-labelledby="odeme-basligi" className="lg:col-span-7">
          <Card padding="lg">
            <SectionHeading id="odeme-basligi" level={2} size="section" eyebrow={`Satıcı · ${ORDER.merchant}`}>
              Ödeme
            </SectionHeading>

            <CardWell className="mt-5 p-4">
              <p className="text-body text-ink">{ORDER.product}</p>
              <dl className="mt-3.5 space-y-2 border-t border-line pt-3.5 text-caption">
                <div className="flex items-baseline justify-between gap-3">
                  <dt className="text-ink-muted">Ara Toplam</dt>
                  <dd className="num text-ink-muted">₺{formatCurrency(ORDER.subtotal)}</dd>
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <dt className="text-ink-muted">KDV (%{ORDER.taxRate * 100})</dt>
                  <dd className="num text-ink-muted">₺{formatCurrency(tax)}</dd>
                </div>
                <div className="flex items-baseline justify-between gap-3 border-t border-line pt-3">
                  <dt className="text-body font-medium text-ink">Toplam</dt>
                  <dd className="num text-metric font-semibold leading-none text-ink">
                    ₺{formatCurrency(total)}
                  </dd>
                </div>
              </dl>
            </CardWell>

            <form onSubmit={handleSubmit} className="mt-6 space-y-4">
              <Field label="Kart Numarası" id="card-number">
                {(aria) => (
                  <div className="relative">
                    <Input
                      {...aria}
                      type="text"
                      inputMode="numeric"
                      autoComplete="cc-number"
                      placeholder="1234 5678 9012 3456"
                      value={cardNumber}
                      onChange={(e) => setCardNumber(formatCardNumber(e.target.value))}
                      mono
                      className="pr-16"
                      required
                    />
                    <span
                      aria-hidden="true"
                      className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2"
                    >
                      <CardTypeIcon type={cardType} className="h-6 w-10" />
                    </span>
                  </div>
                )}
              </Field>

              <Field label="Kart Üzerindeki İsim" id="card-name">
                {(aria) => (
                  <Input
                    {...aria}
                    type="text"
                    autoComplete="cc-name"
                    placeholder="AD SOYAD"
                    value={cardName}
                    onChange={(e) => setCardName(e.target.value.toUpperCase())}
                    className="tracking-[0.04em]"
                    required
                  />
                )}
              </Field>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Son Kullanma Tarihi" id="card-expiry">
                  {(aria) => (
                    <Input
                      {...aria}
                      type="text"
                      inputMode="numeric"
                      autoComplete="cc-exp"
                      placeholder="AA/YY"
                      value={expiry}
                      onChange={(e) => setExpiry(formatExpiry(e.target.value))}
                      mono
                      required
                    />
                  )}
                </Field>
                <Field label="CVV" id="card-cvv">
                  {(aria) => (
                    <Input
                      {...aria}
                      type="text"
                      inputMode="numeric"
                      autoComplete="cc-csc"
                      placeholder="123"
                      value={cvv}
                      onChange={(e) => setCvv(formatCvv(e.target.value))}
                      mono
                      required
                    />
                  )}
                </Field>
              </div>

              <Button type="submit" variant="primary" size="lg" fullWidth loading={status === "loading"}>
                {status === "loading" ? "İşleniyor..." : `₺${formatCurrency(total)} Onayla`}
              </Button>

              {/* Tek bir canlı bölge: ödeme sonucu ekran okuyucuya duyurulmazsa,
                  görme engelli bir kullanıcı işlemin reddedildiğini fark etmez.
                  Sunucudan dönen her karar durumu buraya yazılır. */}
              <div role="status" aria-live="polite" className="space-y-3">
                {/* Each outcome gets a Turkish headline that names WHAT the
                    server did, and the server's own sentence underneath as the
                    detail. The headline is derived from the decision's own
                    `action` / `status` field -- never from the score, which
                    this page is not allowed to interpret. */}
                {status === "success" && (
                  <Alert tone="safe" title="Ödeme onaylandı">
                    Ödeme başarıyla alındı (demo). Gerçek tahsilat yapılmadı.
                  </Alert>
                )}

                {warnMessage && (
                  <Alert tone="suspect" title="Uyarı ile onaylandı">
                    {warnMessage}
                  </Alert>
                )}

                {blockMessage && (
                  <Alert tone="blocked" title="Ödeme reddedildi">
                    {blockMessage}
                  </Alert>
                )}

                {hintMessage && (
                  <Alert tone="info" title="Karar için biraz daha veri gerekiyor">
                    {hintMessage}
                  </Alert>
                )}

                {reloadMessage && (
                  <Alert
                    tone="suspect"
                    title="Oturumun yenilenmesi gerekiyor"
                    actions={
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => window.location.reload()}
                      >
                        <RefreshIcon className="h-3.5 w-3.5" />
                        Sayfayı Yenile
                      </Button>
                    }
                  >
                    {reloadMessage}
                  </Alert>
                )}
              </div>
            </form>

            {/* No "confidence" figure here. The API's `confidence` is
                max(p, 1-p) of the latest single flush, not a calibrated
                probability of being right, while the panel shows the smoothed
                session score. The two can point opposite ways: smoothing damps
                a drop, so one human-looking flush after bot-looking ones shows
                a red band beside "95%" that is certainty in the HUMAN class.
                The analyst view keeps the figure, labelled for what it is. */}

            {/* This used to claim "256-bit SSL" on a page served over plain
                http://localhost. What is stated instead holds for this code:
                the charge request carries only session_id, amount and the
                demo customer reference -- never a card field -- and the SDK
                records keydown timestamps, never key values. */}
            <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-4 text-caption text-ink-faint">
              <span className="inline-flex items-center gap-1.5">
                <LockIcon className="h-3.5 w-3.5 shrink-0" />
                Demo ortamı — gerçek ödeme alınmaz, kart bilgileri sunucuya gönderilmez
              </span>
              <span className="inline-flex items-center gap-1.5 text-ink-muted">
                <ShieldIcon className="h-3.5 w-3.5 shrink-0 text-accent" />
                DeepCheck ile korunuyor
              </span>
            </div>
          </Card>
        </section>

        <div className="flex flex-col gap-5 lg:col-span-5">
          <DecisionLadder activeKey={activeBandKey} />

          {/* THE DEMO HARNESS. Dashed and in the violet reserved for synthetic
              data, so a juror can see at a glance which part of the screen is
              the product and which part is the rig it is being shown on. No
              real checkout asks its customer any of this. */}
          <Card as="section" tone="synthetic" padding="lg" className="border-dashed" aria-labelledby="demo-kontrol-basligi">
            <SectionHeading
              id="demo-kontrol-basligi"
              level={2}
              size="card"
              eyebrow="Demo kontrolleri"
              description="Ürünün parçası değildir; sunumu yapan kişi içindir."
            >
              Müşteri seçimi
            </SectionHeading>

            <div className="mt-5 space-y-4">
              <Field
                id="demo-customer"
                label="Demo Müşterisi"
                hint={SYNTHETIC_CUSTOMERS_NOTE}
                labelSuffix={selectedSynthetic ? <SyntheticBadge size="sm" /> : null}
              >
                {(aria) => (
                  <Select {...aria} value={customerChoice} onChange={(e) => chooseCustomer(e.target.value)}>
                    {SYNTHETIC_DEMO_CUSTOMERS.map((customer) => (
                      <option key={customer.ref} value={customer.ref}>
                        {syntheticCustomerLabel(customer)}
                      </option>
                    ))}
                    <option value={CUSTOM_CUSTOMER}>Serbest referans (aşağıdaki alana yazılır)</option>
                  </Select>
                )}
              </Field>

              <Field
                id="customer-ref"
                label="Müşteri Referansı (demo)"
                hint={CUSTOMER_REF_HELP}
                error={customerRefError}
                describedBy={namespaceNoteId}
              >
                {(aria) => (
                  <Input
                    {...aria}
                    type="text"
                    autoComplete="off"
                    spellCheck={false}
                    maxLength={CUSTOMER_REF_MAX_LENGTH}
                    value={customerRef}
                    invalid={Boolean(customerRefError)}
                    onChange={(e) => {
                      setCustomerRef(e.target.value);
                      setCustomerRefError(null);
                    }}
                    mono
                    className="tracking-normal"
                  />
                )}
              </Field>

              <Disclosure summary="Bu referans nerede saklanır ve ne kadar kalır?">
                <p id={namespaceNoteId}>{CUSTOMER_REF_NAMESPACE_WARNING}</p>
                <a
                  href={KVKK_NOTICE_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block rounded-field text-accent underline underline-offset-4 transition-colors hover:text-accent-strong"
                >
                  KVKK Aydınlatma Metni
                </a>
              </Disclosure>
            </div>
          </Card>
        </div>
      </div>

      {showVerifyModal && (
        <VerificationModal
          onVerified={handleVerified}
          onClose={() => setShowVerifyModal(false)}
          verify={verifyCode}
          demoCode={DEMO_VERIFY_CODE}
          amountLabel={`₺${formatCurrency(total)}`}
        />
      )}
    </div>
  );
}
