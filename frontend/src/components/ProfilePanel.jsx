import { ShapBarChart } from "./RiskChart.jsx";
import SyntheticBadge from "./SyntheticBadge.jsx";

// The SOC view of the per-customer profile layer for one session, read from the
// `profile` block of GET /api/score/{id} (behind the dashboard key). That block
// is always present with the same keys and never carries the profile id, the
// stored vectors, the customer reference or the merchant; those sit behind the
// per-operator review credential. Nothing here is ever shown to the customer:
// the Demo page renders none of it, and a profile escalation reaches the
// checkout as the same generic step-up as every other one.

// profiles.PROFILE_MIN_SESSIONS: with n references the smallest attainable
// p-value is 1/(n+1), so at alpha 0.05 the layer cannot fire below 19. Derived,
// not tuned. Duplicated here because the block deliberately carries no
// constants; backend/test_profiles.py fails if the two ever disagree.
export const PROFILE_MIN_SESSIONS = 19;

// One label per profiles.PROFILE_STATES entry (the same backend test checks the
// key set). The spec lists eight labels for nine states; "suppressed" -- the
// customer objected or profiling is switched off for them -- gets its own
// rather than borrowing the deployment-wide "Profilleme kapalı". "rate_limited"
// came later: too many decisions named this customer within the hour, so the
// profile was not read (and, when enforcing, step-up was asked for instead --
// the "Ek doğrulama istendi" badge says so).
export const PROFILE_STATE_LABELS = {
  disabled: "Profilleme kapalı",
  no_profile: "Profil yok",
  suppressed: "Profilleme bu müşteri için kapalı",
  immature: "Profil olgunlaşmadı",
  thin_session: "Oturum verisi yetersiz",
  too_few_features: "Yeterli özellik ölçülmedi",
  budget_exhausted: "Sorgulama bütçesi doldu",
  breaker: "Katman geçici olarak durduruldu",
  rate_limited: "Bu müşteri için karar sınırı aşıldı — profil okunmadı",
  evaluated: "Değerlendirildi",
};

export const MODALITY_LABELS = {
  touch: "Dokunmatik",
  mouse: "Fare",
  keyboard: "Klavye",
};

// States in which the decision actually read this customer's reference vectors
// for the session's input type, so reference_n is a count. Everywhere else the
// backend stops before that read (thin_session builds no session vector;
// disabled, no_profile and suppressed have nothing to read; a schema or key
// version mismatch is immature with no modality) and its 0 means "not read",
// which "0 / 19" would misstate as "this customer has no history".
const REFERENCE_COUNT_STATES = new Set(["immature", "too_few_features", "budget_exhausted", "breaker", "evaluated"]);

const formatZ = (value) => value.toFixed(1);

function formatNumber(value, digits) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

export default function ProfilePanel({ profile }) {
  if (!profile) return null;

  const stateLabel = PROFILE_STATE_LABELS[profile.state] ?? "Bilinmeyen durum";
  const modalityLabel = MODALITY_LABELS[profile.modality] ?? null;
  const referenceN = Number.isInteger(profile.reference_n) ? profile.reference_n : null;
  // Stored beside the references, never counted among them: sessions learned
  // only because a step-up rescued a profile challenge. The statistic and the
  // maturity count ignore them until the merchant settles the payment or three
  // passed challenges in a row promote them (backend profiles.promotable_run).
  const probationN = Number.isInteger(profile.probation_n) ? profile.probation_n : 0;
  const countKnown = REFERENCE_COUNT_STATES.has(profile.state) && modalityLabel !== null && referenceN !== null;
  const mature = countKnown && referenceN >= PROFILE_MIN_SESSIONS;
  // Enough stored sessions but still immature: too many of them could not be
  // scored on enough features against the other sessions, so the conformal
  // calibration set fell below 19 (profiles.calibration_deviations). "Olgun" beside
  // "Profil olgunlaşmadı" would contradict itself.
  const maturityNote = !mature
    ? "Olgunlaşıyor"
    : profile.state === "immature"
      ? "Karşılaştırılabilir referans yetersiz"
      : "Olgun";
  const progress = countKnown ? Math.min(referenceN, PROFILE_MIN_SESSIONS) : 0;
  const topFeatures = Array.isArray(profile.top_features)
    ? profile.top_features.filter((f) => typeof f?.feature === "string" && Number.isFinite(f?.z))
    : [];
  // decision_audit.is_synthetic, carried as `synthetic` in the block: synthetic
  // demo data took part in the decision this card is read from -- the profile
  // it was compared against is a seeded synthetic customer, or the session
  // itself was simulated. Strictly true, so a server that does not send the
  // field yet labels nothing rather than guessing.
  const synthetic = profile.synthetic === true;

  return (
    <section
      aria-labelledby="profile-heading"
      className="bg-[#18181b] border border-zinc-800 rounded-lg p-5 shadow-xl shadow-black/50 space-y-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="profile-heading" className="text-lg font-semibold tracking-tight text-zinc-50">
          Müşteri Profili
        </h2>
        <div className="flex flex-wrap gap-2">
          {synthetic && <SyntheticBadge />}
          {profile.escalated && (
            <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-xs font-medium text-amber-400">
              Ek doğrulama istendi
            </span>
          )}
          {profile.shadow && (
            <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-xs font-medium text-sky-400">
              Gölge modu — karar etkilenmedi
            </span>
          )}
        </div>
      </div>

      {synthetic && (
        <p className="rounded-md border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs text-violet-200">
          Bu karara sentetik demo verisi katıldı: karşılaştırılan müşteri geçmişi ya da oturumun kendisi simülatörle
          üretildi, gerçek bir kişiye ait değil. Sonuç mekanizmayı gösterir, gerçek kişilerdeki doğruluğu değil; hiçbir
          ölçüme katılmaz.
        </p>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <div>
          <dt className="text-xs text-zinc-500">Profil durumu</dt>
          <dd className="text-zinc-200">{stateLabel}</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Giriş türü</dt>
          <dd className="text-zinc-200">{modalityLabel ?? "—"}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-zinc-500">Referans oturum sayısı</dt>
          <dd className="space-y-1.5">
            {countKnown ? (
              <>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-zinc-200">
                    {modalityLabel}: {referenceN} / {PROFILE_MIN_SESSIONS}
                  </span>
                  <span className={`text-xs ${maturityNote === "Olgun" ? "text-emerald-400" : "text-zinc-500"}`}>
                    {maturityNote}
                  </span>
                </div>
                <div
                  role="progressbar"
                  aria-label={`${modalityLabel} profili olgunluğu`}
                  aria-valuemin={0}
                  aria-valuemax={PROFILE_MIN_SESSIONS}
                  aria-valuenow={progress}
                  className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"
                >
                  <div
                    className={`h-full rounded-full ${maturityNote === "Olgun" ? "bg-emerald-500" : "bg-zinc-400"}`}
                    style={{ width: `${(progress / PROFILE_MIN_SESSIONS) * 100}%` }}
                  />
                </div>
                <p className="text-xs text-zinc-500">
                  Karar anındaki sayı. Her giriş türü ayrı olgunlaşır; eşik altında katman karşılaştırma yapmaz.
                </p>
                {probationN > 0 && (
                  <p className="text-xs text-zinc-400">
                    <span className="font-mono text-zinc-200">Onay bekleyen oturum: {probationN}</span>
                    {" — "}ek doğrulamayla geçti; satıcı ödemeyi onaylayana ya da üst üste 3 başarılı doğrulama
                    profili yenileyene kadar karşılaştırmaya ve olgunluğa katılmaz.
                  </p>
                )}
              </>
            ) : (
              <span className="font-mono text-zinc-200">—</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Sapma p-değeri</dt>
          <dd className="font-mono text-zinc-200">{formatNumber(profile.p_value, 4)}</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Sapma skoru</dt>
          <dd className="font-mono text-zinc-200">{formatNumber(profile.deviation, 2)}</dd>
        </div>
      </dl>

      {/* backend main._profile_block reads the newest decision_audit row that
          carries a profile opinion, and answers no_profile when there is none.
          So a session watched on the dashboard before its customer presses
          Onayla shows "Profil yok" even when that customer has a mature
          profile; without this line that reads as the layer having lost it. */}
      {profile.state !== "disabled" && (
        <p className="text-xs text-zinc-500">
          Bu oturumda müşteri referansıyla verilen son ödeme kararından okunur. Henüz böyle bir karar verilmemiş
          oturumda durum “Profil yok” görünür.
        </p>
      )}

      {topFeatures.length > 0 && (
        <div>
          <h3 className="text-xs text-zinc-500 mb-2">En çok sapan özellikler (z)</h3>
          <ShapBarChart
            shapExplanation={topFeatures.map((f) => ({ feature: f.feature, impact: f.z }))}
            formatValue={formatZ}
          />
          {/* The bars are an SVG a screen reader cannot read. */}
          <ul className="sr-only">
            {topFeatures.map((f) => (
              <li key={f.feature}>
                {f.feature}: z {formatZ(f.z)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-zinc-500 border-t border-zinc-800 pt-3">
        Bu katman yalnızca ek doğrulama isteyebilir: engellemez, onaylamaz, risk skorunu değiştirmez. Katmanın
        ayarları yalnızca sentetik kimlikler üzerinde ölçüldü; gerçek müşteri verisi yoktur.
      </p>
    </section>
  );
}
