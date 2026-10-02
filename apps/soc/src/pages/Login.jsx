import { useState } from "react";

import { login, PanoError, MESSAGES } from "../api.js";
import Alert from "../components/Alert.jsx";
import Button from "../components/Button.jsx";
import Card from "../components/Card.jsx";
import Field, { Input } from "../components/Field.jsx";
import SectionHeading from "../components/SectionHeading.jsx";
import { EyeIcon, EyeOffIcon, LockIcon } from "../components/icons.jsx";

// The SOC login. It is the first thing a juror may see, so it says what the
// pano is, what the key protects and what this browser keeps -- which is no
// longer the key. The key goes to soc-api once, in this request's body; the
// browser is left with an httpOnly cookie it cannot read (src/api.js).
//
// `notice` is why the analyst is here when it is not a first visit: a session
// that ended, a logout, a server that could not be reached.
export default function Login({ onLoggedIn, notice }) {
  const [key, setKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    const value = key.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(value);
      // Gone from memory as well: nothing on this page holds it after login.
      setKey("");
      onLoggedIn();
    } catch (err) {
      setError(err instanceof PanoError ? err.message : MESSAGES.unreachable);
      setBusy(false);
    }
  }

  return (
    <div className="relative isolate flex min-h-full items-start justify-center px-4 py-12 sm:py-20">
      {/* Decoration only: a faint accent glow behind the card. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-80 bg-[radial-gradient(ellipse_at_top,rgb(108_176_251/0.12),transparent_70%)]"
      />
      <div className="w-full max-w-md space-y-4">
        {notice && (
          <Alert tone={notice.tone} role={notice.tone === "danger" ? "alert" : undefined}>
            {notice.text}
          </Alert>
        )}

        <Card as="section" padding="lg" className="space-y-6" aria-labelledby="pano-giris-baslik">
          <div className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-field border border-accent/35 bg-accent/10 text-accent"
            >
              <LockIcon className="h-5 w-5" />
            </span>
            <SectionHeading
              level={1}
              size="section"
              id="pano-giris-baslik"
              eyebrow="Güvenlik Operasyon Merkezi"
              description="Pano, tüm oturumların canlı risk verisini gösterir. Devam etmek için erişim anahtarını girin."
            >
              SOC Panosu
            </SectionHeading>
          </div>

          <form onSubmit={submit} className="space-y-4" noValidate>
            <Field
              id="dashboard-key"
              label="Pano Erişim Anahtarı"
              note="Anahtar yalnızca bu girişte SOC sunucusuna gönderilir; tarayıcıda saklanmaz. Tarayıcı yalnızca 8 saat geçerli, imzalı bir oturum çerezi tutar."
              error={error ?? undefined}
            >
              {(field) => (
                <div className="relative">
                  <Input
                    {...field}
                    type={reveal ? "text" : "password"}
                    mono
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    autoFocus
                    autoComplete="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    className="min-h-[2.75rem] pr-12"
                  />
                  <button
                    type="button"
                    onClick={() => setReveal((on) => !on)}
                    aria-pressed={reveal}
                    aria-controls="dashboard-key"
                    aria-label={reveal ? "Anahtarı gizle" : "Anahtarı göster"}
                    title={reveal ? "Anahtarı gizle" : "Anahtarı göster"}
                    className="absolute inset-y-0 right-0 flex w-11 cursor-pointer items-center justify-center rounded-r-field text-ink-faint transition-colors hover:text-ink"
                  >
                    {reveal ? <EyeOffIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
                  </button>
                </div>
              )}
            </Field>
            <Button type="submit" variant="primary" size="lg" fullWidth loading={busy} disabled={!key.trim()}>
              {busy ? "Doğrulanıyor" : "Panoya Gir"}
            </Button>
          </form>
        </Card>

        <p className="px-1 text-caption leading-relaxed text-ink-faint">
          Bu pano analist içindir. Ödeme tarafı (TechStore) ayrı bir uygulamadır ve ödeyen kişiye hiçbir skor
          göstermez.
        </p>
      </div>
    </div>
  );
}
