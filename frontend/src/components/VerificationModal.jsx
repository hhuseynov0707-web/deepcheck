import { useEffect, useRef, useState } from "react";

import Alert from "./Alert.jsx";
import Button from "./Button.jsx";
import { CheckIcon, LockIcon } from "./icons.jsx";

// Step-up verification. The code is checked by POST /api/demo/verify on the
// server, which records the result on the session; this component cannot
// declare success on its own. `verify(code)` is supplied by the page and
// resolves to { ok, message }.
//
// It says what happened -- the server asked for another factor before it would
// complete THIS payment -- and nothing about why. Which check fired is the
// tuning signal the server collapses into one public reason, so the prompt is
// identical whatever raised it.
//
// The demo code is printed on purpose: the real integration replaces this
// screen with the merchant's SMS / 3-D Secure provider, and a jury member who
// sees the code knows this is a deliberate demo value rather than an
// "any six digits" bypass.
const CODE_LENGTH = 6;

export default function VerificationModal({ onVerified, onClose, verify, demoCode, amountLabel }) {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("idle"); // idle | verifying | verified
  const [error, setError] = useState(null);
  const dialogRef = useRef(null);
  const inputRef = useRef(null);

  // Focus starts inside the dialog and comes back to whatever opened it. Mount
  // only: re-running this on every state change would drag focus back to the
  // input mid-interaction and "restore" it to the dialog's own field.
  useEffect(() => {
    const previouslyFocused = document.activeElement;
    inputRef.current?.focus();
    return () => {
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, []);

  // A dialog that can be tabbed out of is a dialog a keyboard user loses: the
  // focus ring walks off into the checkout form behind the overlay, which is
  // inert to the mouse but not to Tab. Escape closes it for the same reason the
  // ✕ does -- and under the same condition, so a request in flight cannot be
  // abandoned halfway.
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "Escape") {
        if (status === "idle") onClose?.();
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
  }, [status, onClose]);

  async function handleVerify(e) {
    e.preventDefault();
    if (status !== "idle" || code.length !== CODE_LENGTH) return;
    setStatus("verifying");
    setError(null);
    const result = await verify(code);
    if (result.ok) {
      setStatus("verified");
      window.setTimeout(() => onVerified(), 600);
      return;
    }
    setStatus("idle");
    setCode("");
    setError(result.message || "Doğrulama kodu hatalı");
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-canvas-sunken/80 px-4 py-6 backdrop-blur-sm sm:items-center">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="verify-heading"
        aria-describedby="verify-intro"
        className="w-full max-w-md animate-fade-rise rounded-card border border-line-strong bg-panel shadow-overlay"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-6 py-5">
          <div className="flex min-w-0 items-start gap-3">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-field border border-accent/35 bg-accent/10 text-accent"
            >
              <LockIcon className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <h3 id="verify-heading" className="text-h3 font-semibold leading-tight text-ink">
                Ek Doğrulama Gerekli
              </h3>
              <p id="verify-intro" className="mt-1.5 text-caption leading-relaxed text-ink-muted">
                {amountLabel ? (
                  <>
                    <span className="num font-semibold text-ink">{amountLabel}</span> tutarındaki ödemeyi
                    tamamlamak için sunucu ikinci bir kanıt istiyor.
                  </>
                ) : (
                  "Ödemeyi tamamlamak için sunucu ikinci bir kanıt istiyor."
                )}
              </p>
            </div>
          </div>
          {status === "idle" && (
            <button
              type="button"
              onClick={onClose}
              className="-mr-2 -mt-1 flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-field
                         text-ink-faint transition-colors hover:bg-panel-raised hover:text-ink"
              aria-label="Kapat"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="m6.5 6.5 11 11M17.5 6.5l-11 11" />
              </svg>
            </button>
          )}
        </div>

        <form onSubmit={handleVerify} className="space-y-4 px-6 py-5">
          <div className="space-y-2">
            {/* A visible label, not a placeholder: a placeholder disappears the
                moment the first digit is typed, taking the only description of
                the field with it. */}
            <label
              htmlFor="verify-code"
              className="block text-eyebrow font-semibold uppercase tracking-[0.08em] text-ink-muted"
            >
              6 haneli doğrulama kodu
            </label>
            <input
              ref={inputRef}
              id="verify-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={CODE_LENGTH}
              placeholder="000000"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, CODE_LENGTH))}
              disabled={status !== "idle"}
              aria-invalid={error ? "true" : undefined}
              aria-describedby="verify-code-hint"
              className={`num w-full rounded-field border bg-canvas-sunken px-4 py-4 text-center text-h1 font-semibold
                          tracking-[0.42em] text-ink transition-colors placeholder:text-ink-faint/40
                          disabled:cursor-not-allowed disabled:opacity-60 ${
                            error ? "border-risk-blocked/70" : "border-line-control focus:border-accent"
                          }`}
            />
            <p id="verify-code-hint" className="text-caption text-ink-faint">
              Gerçek entegrasyonda kod, telefonunuza SMS / 3-D Secure ile gelir.
            </p>
          </div>

          {error && (
            <Alert tone="blocked" role="alert">
              {error}
            </Alert>
          )}

          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            {status === "idle" && (
              <Button variant="secondary" size="lg" onClick={onClose} className="sm:w-2/5">
                Vazgeç
              </Button>
            )}
            <Button
              type="submit"
              variant="primary"
              size="lg"
              className="flex-1"
              loading={status === "verifying"}
              disabled={status !== "idle" || code.length !== CODE_LENGTH}
            >
              {status === "verified" && <CheckIcon className="h-4 w-4" />}
              {status === "idle" && "Doğrula"}
              {status === "verifying" && "Doğrulanıyor..."}
              {status === "verified" && "Doğrulandı"}
            </Button>
          </div>
        </form>

        {demoCode && (
          <div className="space-y-2 rounded-b-card border-t border-line bg-canvas-sunken/60 px-6 py-4">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-ink-muted">
              <span>Demo doğrulama kodu:</span>
              <span className="num rounded border border-line bg-panel px-2 py-0.5 text-body font-semibold tracking-[0.25em] text-ink">
                {demoCode}
              </span>
            </p>
            <p className="text-caption leading-relaxed text-ink-faint">
              Sonucu sunucu kaydeder; tarayıcı doğrulandığını kendi başına beyan edemez.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
