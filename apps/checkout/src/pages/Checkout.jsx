import { useCallback, useEffect, useRef, useState } from "react";

import { BrandChip, DemoPayMark } from "../components/Brand.jsx";
import Button from "../components/Button.jsx";
import { TextField } from "../components/Field.jsx";
import { CardIcon, RefreshIcon } from "../components/icons.jsx";
import Notice from "../components/Notice.jsx";
import OrderSummary, { AmountHero } from "../components/OrderSummary.jsx";
import OtpDialog from "../components/OtpDialog.jsx";
import Receipt from "../components/Receipt.jsx";
import { fetchCart, submitCheckout, submitVerification } from "../lib/api.js";
import {
  ACCEPTED_BRANDS,
  cvvHelp,
  detectBrand,
  digitsOnly,
  formatCardName,
  formatCardNumber,
  formatCvv,
  formatExpiry,
  parseExpiry,
  validateCardName,
  validateCardNumber,
  validateCvv,
  validateExpiry,
} from "../lib/card.js";
import { formatTry } from "../lib/money.js";
import { reloadPage } from "../lib/navigation.js";
import { RIGHTS_HREF } from "../lib/routes.js";
import { sdkRegistered, sessionCredentials, startSdk, stopSdk } from "../lib/sdk.js";

// TechStore's checkout, as the payer sees it: an order summary and a payment
// form. It shows no score, band, label or reason in any state, because it is
// never given one -- the store's server decides with the DeepCheck core and
// answers only paid / requires_action / declined (apps/checkout-server).
//
// This page holds no rule that decides a payment. It sends the request and
// renders the outcome; deleting any check below changes nothing on the server.

// Printed in the step-up dialog so the demo code reads as a deliberate demo
// value. Must match the core's DEMO_VERIFY_CODE (both default to 482913).
const DEMO_VERIFY_CODE = import.meta.env.VITE_DEMO_VERIFY_CODE || "482913";

const FIELDS = ["number", "name", "expiry", "cvv"];

const VALIDATORS = {
  number: (v) => validateCardNumber(v.number),
  name: (v) => validateCardName(v.name),
  expiry: (v) => validateExpiry(v.expiry),
  cvv: (v) => validateCvv(v.cvv, detectBrand(v.number)),
};

const FORMATTERS = {
  number: formatCardNumber,
  name: formatCardName,
  expiry: formatExpiry,
  cvv: (value, values) => formatCvv(value, detectBrand(values.number)),
};

// What the payer is told, by outcome. Calm and generic on purpose: a declined
// payment never says why, the same as at any shop.
//
// A decline is the STORE's, and the text says so. The bank never saw an
// authorisation attempt, so "contact your bank" sent the payer somewhere that
// cannot help, and "try another card" invited the card cycling this system
// flags. Instead: the store is who to contact, and the link opens the notice's
// rights section, where an objection to an automated decision (KVKK m. 11/1-g)
// is addressed to the merchant. Still no reason: which check refused stays in
// the SOC.
const NOTICES = {
  declined: {
    tone: "danger",
    text: "Ödemeniz tamamlanamadı. Kartınızdan çekim yapılmadı. Sorun devam ederse mağazayla iletişime geçin.",
    rightsLink: true,
  },
  unavailable: {
    tone: "danger",
    text: "Ödeme şu anda işlenemiyor. Lütfen birkaç dakika sonra tekrar deneyin.",
  },
  reload: {
    tone: "danger",
    text: "Ödeme sayfası tam olarak yüklenemedi. Lütfen sayfayı yenileyip tekrar deneyin.",
    reload: true,
  },
  rate_limited: {
    tone: "danger",
    text: "Çok fazla deneme yapıldı. Lütfen biraz bekleyip tekrar deneyin.",
  },
  invalid: {
    tone: "danger",
    text: "Bilgileriniz doğrulanamadı. Lütfen kontrol edip tekrar deneyin.",
  },
  cancelled: {
    tone: "info",
    text: "Doğrulama tamamlanmadı; kartınızdan çekim yapılmadı. Dilediğinizde tekrar deneyebilirsiniz.",
  },
};

const EMPTY = { number: "", name: "", expiry: "", cvv: "" };

// The small caps over the field group ("Kart bilgileri").
const GROUP_HEADING = "text-xs font-semibold uppercase tracking-[0.08em] text-ink-muted";

export default function Checkout() {
  const [cart, setCart] = useState(null);
  const [cartFailed, setCartFailed] = useState(false);
  const [values, setValues] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [phase, setPhase] = useState("form"); // form | processing | otp | paid
  const [notice, setNotice] = useState(null);
  const [receipt, setReceipt] = useState(null);
  // The session the checkout was sent under. The OTP is verified against the
  // same one: the store keyed its pending challenge to it, and the SDK may
  // have rotated to a new session since.
  const credentialsRef = useRef(null);
  const inputRefs = {
    number: useRef(null),
    name: useRef(null),
    expiry: useRef(null),
    cvv: useRef(null),
  };
  const payButtonRef = useRef(null);
  // Bumped whenever an answer puts the form back. The pay button is focused
  // AFTER the render that re-enables it (the fieldset is disabled while the
  // store decides, and focusing a disabled control does nothing), so a
  // keyboard user is left where they pressed, not at the top of the page.
  const [refocusPay, setRefocusPay] = useState(0);

  // The SDK registers in the background while the payer fills the form. If
  // that fails it never retries, and the only remedy is a reload -- which
  // wipes the form. So the page asks for it as soon as it knows, not after
  // the payer has typed everything and pressed "Öde". A slow registration
  // (proof of work, a busy core) shows nothing until it actually fails, and a
  // successful one leaves the form exactly as it was.
  useEffect(() => {
    const stop = startSdk();
    let active = true;
    sdkRegistered().then((registered) => {
      if (active && !registered) setNotice((current) => current ?? "reload");
    });
    return () => {
      active = false;
      stop();
    };
  }, []);

  useEffect(() => {
    if (refocusPay) payButtonRef.current?.focus();
  }, [refocusPay]);

  const loadCart = useCallback(async () => {
    setCartFailed(false);
    setCart(null);
    const data = await fetchCart();
    if (data) setCart(data);
    else setCartFailed(true);
  }, []);

  useEffect(() => {
    loadCart();
  }, [loadCart]);

  const brand = detectBrand(values.number);
  const busy = phase === "processing";

  function update(field, raw) {
    const next = { ...values, [field]: FORMATTERS[field](raw, values) };
    // A new brand can change the CVV length (American Express has four).
    if (field === "number") next.cvv = formatCvv(next.cvv, detectBrand(next.number));
    setValues(next);
    // An error already on screen is re-checked as the payer fixes it, so it
    // disappears the moment the value is right. A field with no error is
    // left alone until it loses focus: nobody wants "eksik" while typing.
    setErrors((current) => {
      const shown = { ...current };
      for (const key of field === "number" ? ["number", "cvv"] : [field]) {
        if (shown[key]) shown[key] = VALIDATORS[key](next);
      }
      return shown;
    });
  }

  function blur(field) {
    if (!values[field]) return;
    setErrors((current) => ({ ...current, [field]: VALIDATORS[field](values) }));
  }

  function handleOutcome(result) {
    switch (result.kind) {
      case "paid":
        stopSdk();
        setReceipt(result.receipt);
        setPhase("paid");
        return;
      case "requires_action":
      case "wrong_code":
        setPhase("otp");
        return;
      case "declined":
        setNotice("declined");
        break;
      case "session":
        setNotice("reload");
        break;
      case "rate_limited":
        setNotice("rate_limited");
        break;
      case "invalid":
        setNotice("invalid");
        break;
      default:
        setNotice("unavailable");
    }
    setPhase("form");
    setRefocusPay((n) => n + 1);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (phase !== "form" || !cart) return;

    const found = Object.fromEntries(FIELDS.map((field) => [field, VALIDATORS[field](values)]));
    setErrors(found);
    const firstInvalid = FIELDS.find((field) => found[field]);
    if (firstInvalid) {
      inputRefs[firstInvalid].current?.focus();
      return;
    }

    setNotice(null);
    setPhase("processing");

    const credentials = await sessionCredentials();
    if (!credentials) {
      // No session token: the SDK never registered (a blocked script, no
      // route to the core). Without one the store cannot ask for a decision,
      // and no decision is never a payment -- only a reload can help.
      setNotice("reload");
      setPhase("form");
      setRefocusPay((n) => n + 1);
      return;
    }
    credentialsRef.current = credentials;

    const expiry = parseExpiry(values.expiry);
    const result = await submitCheckout({
      sessionId: credentials.sessionId,
      token: credentials.token,
      card: {
        last4: digitsOnly(values.number).slice(-4),
        brand,
        expMonth: expiry.month,
        expYear: expiry.year,
      },
    });
    handleOutcome(result);
  }

  const verify = useCallback(
    (code) => submitVerification({ ...credentialsRef.current, code }),
    [],
  );

  const cancelVerification = useCallback(() => {
    setPhase("form");
    setNotice("cancelled");
    // The dialog opened while the form was disabled, so there is no field to
    // hand focus back to; the pay button is where the payer left off.
    setRefocusPay((n) => n + 1);
  }, []);

  const shownNotice = notice ? NOTICES[notice] : null;
  const payLabel = cart ? `${formatTry(cart.total)} Öde` : "Öde";

  return (
    <div className="mx-auto w-full max-w-5xl px-4 pb-14 pt-6 sm:px-6 sm:pt-10 lg:pt-14">
      {/* A hosted-checkout split: what is being paid for on the left, how on
          the right. Stacked below lg, the amount first. */}
      <div className="grid items-start gap-6 sm:gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)] lg:gap-14">
        <div className="space-y-6 lg:sticky lg:top-24 lg:pt-2">
          <AmountHero cart={cart} failed={cartFailed} paid={phase === "paid"} />
          <OrderSummary cart={cart} failed={cartFailed} onRetry={loadCart} />
        </div>

        <section
          aria-labelledby={phase === "paid" ? undefined : "odeme-basligi"}
          aria-label={phase === "paid" ? "Ödeme sonucu" : undefined}
          className="overflow-hidden rounded-card border border-line bg-card shadow-card"
        >
          {phase === "paid" && receipt ? (
            <div className="p-5 sm:p-8">
              <Receipt receipt={receipt} onNewPayment={reloadPage} />
            </div>
          ) : (
            <form onSubmit={handleSubmit} noValidate className="p-5 sm:p-8">
              <h2 id="odeme-basligi" className="text-lg font-semibold tracking-tight text-ink">
                Kart ile öde
              </h2>

              {/* No top margin: the sr-only legend is the first child, so space-y
                  already puts the gap above the first group. */}
              <fieldset disabled={phase !== "form"} className="space-y-7">
                <legend className="sr-only">Ödeme bilgileri</legend>

                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                    <h3 className={GROUP_HEADING}>Kart bilgileri</h3>
                    <ul className="flex flex-wrap gap-1" aria-label="Kabul edilen kartlar">
                      {ACCEPTED_BRANDS.map((key) => (
                        <li key={key}>
                          <BrandChip brand={key} active={!brand || brand === key} />
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="space-y-4">
                    <TextField
                      ref={inputRefs.number}
                      id="card-number"
                      label="Kart numarası"
                      type="text"
                      inputMode="numeric"
                      autoComplete="cc-number"
                      placeholder="0000 0000 0000 0000"
                      maxLength={19}
                      value={values.number}
                      error={errors.number}
                      onChange={(e) => update("number", e.target.value)}
                      onBlur={() => blur("number")}
                      inputClassName="num tracking-[0.06em]"
                      adornment={
                        brand ? <BrandChip brand={brand} /> : <CardIcon className="h-5 w-5 text-ink-muted" />
                      }
                      adornmentPad={brand ? "pr-28" : "pr-11"}
                    />

                    <TextField
                      ref={inputRefs.name}
                      id="card-name"
                      label="Kart üzerindeki isim"
                      type="text"
                      autoComplete="cc-name"
                      autoCapitalize="characters"
                      spellCheck={false}
                      maxLength={60}
                      value={values.name}
                      error={errors.name}
                      onChange={(e) => update("name", e.target.value)}
                      onBlur={() => blur("name")}
                    />

                    <div className="grid grid-cols-2 gap-3 sm:gap-4">
                      <TextField
                        ref={inputRefs.expiry}
                        id="card-expiry"
                        label="Son kullanma (AA/YY)"
                        type="text"
                        inputMode="numeric"
                        autoComplete="cc-exp"
                        placeholder="AA/YY"
                        maxLength={5}
                        value={values.expiry}
                        error={errors.expiry}
                        onChange={(e) => update("expiry", e.target.value)}
                        onBlur={() => blur("expiry")}
                        inputClassName="num"
                      />
                      <TextField
                        ref={inputRefs.cvv}
                        id="card-cvv"
                        label="CVV"
                        type="text"
                        inputMode="numeric"
                        autoComplete="cc-csc"
                        maxLength={4}
                        value={values.cvv}
                        error={errors.cvv}
                        hint={cvvHelp(brand)}
                        onChange={(e) => update("cvv", e.target.value)}
                        onBlur={() => blur("cvv")}
                        inputClassName="num"
                      />
                    </div>
                  </div>
                </div>

                <div>
                  {/* Right above the button, where the payer is looking when
                      the answer comes back. Always in the tree, even empty:
                      a live region that appears together with its text is
                      not reliably announced. */}
                  <div aria-live="polite" aria-atomic="true" className="[&:not(:empty)]:mb-4">
                    {shownNotice && (
                      <Notice
                        tone={shownNotice.tone}
                        action={
                          shownNotice.reload ? (
                            <Button variant="secondary" onClick={reloadPage}>
                              <RefreshIcon className="h-4 w-4" />
                              Sayfayı yenile
                            </Button>
                          ) : null
                        }
                      >
                        {shownNotice.text}
                        {shownNotice.rightsLink && (
                          <>
                            {" "}
                            <a
                              href={RIGHTS_HREF}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="cursor-pointer rounded-sm font-medium text-brand underline underline-offset-2 transition-colors duration-150 hover:text-brand-hover"
                            >
                              Haklarınız ve başvuru yolu
                            </a>
                          </>
                        )}
                      </Notice>
                    )}
                  </div>

                  <Button
                    ref={payButtonRef}
                    type="submit"
                    variant="primary"
                    size="lg"
                    fullWidth
                    loading={busy}
                    disabled={!cart}
                  >
                    <span className="num">{busy ? "İşleniyor…" : payLabel}</span>
                  </Button>
                </div>
              </fieldset>
            </form>
          )}

          <div className="flex items-start gap-2.5 border-t border-line bg-canvas/70 px-5 py-4 sm:px-8">
            <DemoPayMark className="mt-0.5 h-4 w-4" />
            <p className="text-[13px] leading-5 text-ink-muted">
              Demo: tam kart numarası ve CVV hiçbir sunucuya gönderilmez.
            </p>
          </div>
        </section>
      </div>

      {phase === "otp" && cart && (
        <OtpDialog
          amountLabel={formatTry(cart.total)}
          demoCode={DEMO_VERIFY_CODE}
          onSubmit={verify}
          onResult={handleOutcome}
          onCancel={cancelVerification}
        />
      )}
    </div>
  );
}
