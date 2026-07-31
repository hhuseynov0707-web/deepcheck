import { useState } from "react";

export default function VerificationModal({ onVerified, onClose }) {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("idle"); // idle | verifying | verified

  function handleVerify(e) {
    e.preventDefault();
    if (status !== "idle" || code.length !== 6) return;
    setStatus("verifying");
    window.setTimeout(() => {
      setStatus("verified");
      window.setTimeout(() => onVerified(), 800);
    }, 1500);
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
        <p className="text-sm text-zinc-400 mb-5">
          Bu işlem için ek güvenlik doğrulaması gerekiyor. Telefonunuza gönderilen 6 haneli kodu girin.
        </p>

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
