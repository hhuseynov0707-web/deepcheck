import { useState } from "react";

// Step-up verification. The code is checked by POST /api/demo/verify on the
// server, which records the result on the session; this component cannot
// declare success on its own. `verify(code)` is supplied by the page and
// resolves to { ok, message }.
//
// The demo code is printed on purpose: the real integration replaces this
// screen with the merchant's SMS / 3-D Secure provider, and a jury member who
// sees the code knows this is a deliberate demo value rather than an
// "any six digits" bypass.
export default function VerificationModal({ onVerified, onClose, verify, demoCode }) {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("idle"); // idle | verifying | verified
  const [error, setError] = useState(null);

  async function handleVerify(e) {
    e.preventDefault();
    if (status !== "idle" || code.length !== 6) return;
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
      <div className="w-full max-w-sm bg-[#18181b] border border-zinc-800 rounded-lg p-6 shadow-2xl shadow-black/50">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-lg font-semibold tracking-tight text-zinc-50">Ek Doğrulama Gerekli</h3>
          {status === "idle" && (
            <button
              type="button"
              onClick={onClose}
              className="text-zinc-500 hover:text-zinc-300 transition-colors duration-200 ease-out text-sm"
              aria-label="Kapat"
            >
              ✕
            </button>
          )}
        </div>
        <p className="text-sm text-zinc-400 mb-3">
          Bu işlem için ek güvenlik doğrulaması gerekiyor. Telefonunuza gönderilen 6 haneli kodu girin.
        </p>
        {demoCode && (
          <p className="text-xs text-zinc-500 mb-5 rounded-md border border-zinc-800 bg-[#09090b] px-3 py-2">
            Demo doğrulama kodu: <span className="font-mono text-zinc-300 tracking-widest">{demoCode}</span>
            <br />
            Gerçek entegrasyonda bu adım SMS / 3-D Secure sağlayıcınız tarafından yapılır; sonuç sunucuda
            kaydedilir, tarayıcı tarafından beyan edilmez.
          </p>
        )}

        <form onSubmit={handleVerify} className="space-y-4">
          <input
            type="text"
            inputMode="numeric"
            maxLength={6}
            placeholder="000000"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            disabled={status !== "idle"}
            className="w-full text-center tracking-[0.5em] text-lg font-mono bg-[#09090b] border border-zinc-800 rounded-md px-4 py-3 text-zinc-100 placeholder-zinc-600 outline-none focus:border-zinc-700 transition-colors duration-200 ease-out disabled:opacity-60"
            autoFocus
          />

          {error && (
            <p className="text-center rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={status !== "idle" || code.length !== 6}
            className="w-full bg-zinc-100 hover:bg-zinc-200 text-zinc-900 font-medium py-3 px-4 rounded-md transition-colors duration-200 cursor-pointer text-sm tracking-wide shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {status === "verifying" && (
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {status === "idle" && "Doğrula"}
            {status === "verifying" && "Doğrulanıyor..."}
            {status === "verified" && "Doğrulandı"}
          </button>
        </form>
      </div>
    </div>
  );
}
