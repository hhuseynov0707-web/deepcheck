import { useEffect, useRef, useState } from "react";

import { DemoPayMark } from "./Brand.jsx";
import Button from "./Button.jsx";
import { CloseIcon, PhoneIcon } from "./icons.jsx";
import Notice from "./Notice.jsx";

const CODE_LENGTH = 6;

// The step-up prompt. It says only what the payer needs: the payment needs one
// more confirmation. It never says why -- which check asked is withheld by the
// core (its public reason collapses to one), and the store does not pass even
// that on.
//
// It does not say the BANK is asking, either. The store and DeepCheck asked,
// and no bank sent anything: a payer told "your bank wants to verify" who
// then calls the bank reaches someone who has seen no attempt. The title is
// the store's ("ek doğrulama gerekiyor"), and one sentence says plainly that
// in this demo the step stands in for the bank's 3-D Secure step.
//
// `onSubmit(code)` resolves to an outcome kind (lib/api.js). This component
// handles the two that keep it open -- a wrong code, a temporary failure --
// and hands every other outcome back to the page through `onResult`.
//
// The demo hint prints the code on purpose: the real integration replaces this
// prompt with the card issuer's own SMS / 3-D Secure step, and a jury member
// who sees the code knows it is a deliberate demo value, not "any six digits".
export default function OtpDialog({ amountLabel, demoCode, onSubmit, onResult, onCancel }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [fieldError, setFieldError] = useState(null);
  const [notice, setNotice] = useState(null);
  // Bumped when an answer leaves the dialog open. The code field is focused
  // AFTER the render that re-enables it: focusing straight after the await
  // targets a field that is still disabled, the call does nothing, and the
  // payer's next keystrokes -- the right code -- go nowhere. Measured, not
  // guessed: a scripted browser typing a wrong code and then the right one
  // never reached /api/checkout/verify the second time.
  const [refocus, setRefocus] = useState(0);
  const dialogRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (refocus) inputRef.current?.focus();
  }, [refocus]);

  // Focus moves into the dialog on open and back to whatever opened it on
  // close (mount/unmount only, so typing never yanks focus around).
  useEffect(() => {
    const opener = document.activeElement;
    inputRef.current?.focus();
    return () => {
      if (opener instanceof HTMLElement && document.contains(opener)) opener.focus();
    };
  }, []);

  // Tab stays inside the dialog; Escape cancels unless a request is in flight.
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "Escape") {
        if (!busy) onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (busy) return;
    if (code.length !== CODE_LENGTH) {
      setFieldError("6 haneli kodu girin.");
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setFieldError(null);
    setNotice(null);
    const result = await onSubmit(code);
    setBusy(false);
    if (result.kind === "wrong_code") {
      setCode("");
      setFieldError("Kod hatalı. Lütfen tekrar deneyin.");
      setRefocus((n) => n + 1);
      return;
    }
    if (result.kind === "unavailable") {
      setNotice("Ödeme şu anda işlenemiyor. Lütfen birkaç dakika sonra tekrar deneyin.");
      setRefocus((n) => n + 1);
      return;
    }
    if (result.kind === "rate_limited") {
      setNotice("Çok fazla deneme yapıldı. Lütfen biraz bekleyip tekrar deneyin.");
      setRefocus((n) => n + 1);
      return;
    }
    onResult(result);
  }

  const errorId = fieldError ? "otp-code-error" : null;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/45 p-4 backdrop-blur-[2px] animate-fade-in sm:items-center">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="otp-title"
        aria-describedby="otp-intro"
        className="w-full max-w-md animate-rise-in rounded-card bg-card shadow-dialog"
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-6 pb-4 pt-5">
          <div className="flex min-w-0 items-start gap-3">
            <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand">
              <PhoneIcon className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h2 id="otp-title" className="text-lg font-semibold leading-snug text-ink">
                Bu ödeme için ek doğrulama gerekiyor
              </h2>
              <p id="otp-intro" className="mt-1 text-sm leading-6 text-ink-muted">
                <span className="num font-medium text-ink">{amountLabel}</span> tutarındaki ödemeyi onaylamak için
                6 haneli doğrulama kodunu girin. Bu demoda bu adım, bankanızın 3-D Secure doğrulamasının yerini tutar.
              </p>
            </div>
          </div>
          {!busy && (
            <button
              type="button"
              onClick={onCancel}
              aria-label="Kapat"
              className="-mr-2 -mt-1 flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-field text-ink-muted transition-colors duration-150 hover:bg-canvas hover:text-ink"
            >
              <CloseIcon className="h-5 w-5" />
            </button>
          )}
        </div>

        <form onSubmit={handleSubmit} noValidate className="space-y-4 px-6 py-5">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="otp-code" className="text-sm font-medium text-ink">
              Doğrulama kodu
            </label>
            <input
              ref={inputRef}
              id="otp-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]*"
              maxLength={CODE_LENGTH}
              value={code}
              disabled={busy}
              onChange={(e) => {
                setCode(e.target.value.replace(/\D/g, "").slice(0, CODE_LENGTH));
                if (fieldError) setFieldError(null);
              }}
              aria-invalid={fieldError ? "true" : undefined}
              aria-describedby={errorId ?? undefined}
              className={`field-control num h-14 w-full rounded-field border bg-white px-4 text-center text-2xl font-semibold tracking-[0.5em] text-ink transition-[border-color,box-shadow] duration-150
                disabled:bg-canvas ${fieldError ? "border-danger focus:shadow-[0_0_0_4px_rgb(185_28_28/0.12)]" : "border-line-control hover:border-ink-muted focus:border-brand focus:shadow-[0_0_0_4px_rgb(37_99_235/0.14)]"}`}
            />
            {fieldError && (
              <p id="otp-code-error" className="text-[13px] font-medium leading-5 text-danger">
                {fieldError}
              </p>
            )}
          </div>

          <div role="status" aria-live="polite">
            {notice && <Notice tone="danger">{notice}</Notice>}
          </div>

          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button variant="secondary" onClick={onCancel} disabled={busy} className="sm:w-2/5">
              Vazgeç
            </Button>
            <Button type="submit" variant="primary" loading={busy} className="flex-1">
              {busy ? "Doğrulanıyor…" : "Doğrula"}
            </Button>
          </div>
        </form>

        {demoCode && (
          <div className="flex items-center gap-2 rounded-b-card border-t border-line bg-canvas px-6 py-3.5">
            <DemoPayMark className="h-4 w-4" />
            <p className="text-[13px] text-ink-muted">
              Demo ortamı: SMS kodu <span className="num font-semibold tracking-[0.12em] text-ink">{demoCode}</span>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
