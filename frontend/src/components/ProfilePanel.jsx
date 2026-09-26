import Alert from "./Alert.jsx";
import Badge from "./Badge.jsx";
import Card from "./Card.jsx";
import Disclosure from "./Disclosure.jsx";
import FeatureBars from "./FeatureBars.jsx";
import SectionHeading from "./SectionHeading.jsx";
import SyntheticBadge from "./SyntheticBadge.jsx";

// The SOC view of the per-customer profile layer for one session, read from the
// `profile` block of GET /api/score/{id} (behind the dashboard key). That block
// is always present with the same keys and never carries the profile id, the
// stored vectors, the customer reference or the merchant; those sit behind the
// per-operator review credential. Nothing here is ever shown to the customer:
// the Demo page renders none of it, and a profile escalation reaches the
// checkout as the same generic step-up as every other one.
//
// Presented as data. The card used to be five paragraphs of grey prose with the
// figures buried in them; the same sentences are still here, but the numbers
// are now tiles and bars and the standing caveats are folded into a disclosure,
// so an operator reading their tenth card reads four values instead of an
// essay. Nothing was dropped and nothing new is claimed: every figure below is
// a field of that block, and a field the backend did not fill renders as an em
// dash.

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

// The profile's bars are z scores, not the SHAP percentages the other card
// plots, so the unit is printed on every value rather than left to a heading.
const formatZ = (value) => `z ${value.toFixed(1)}`;

// Read from the newest decision_audit row carrying a profile opinion. A session
// watched before its customer presses Onayla has no such row, so it reads
// "Profil yok" even for a mature customer; without this sentence that looks
// like the layer losing a profile.
const READ_FROM_DECISION =
  "Bu oturumda müşteri referansıyla verilen son ödeme kararından okunur. Henüz böyle bir karar verilmemiş " +
  "oturumda durum “Profil yok” görünür.";

function formatNumber(value, digits) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

// A labelled value in its own well. The dt is immediately followed by its dd,
// always: that pairing is what makes the two lines read as one definition.
function Detail({ term, children, className = "" }) {
  return (
    <div className={`rounded-field border border-line bg-canvas-sunken px-3 py-2.5 ${className}`}>
      <dt className="eyebrow">{term}</dt>
      <dd className="mt-1 text-body leading-snug text-ink">{children}</dd>
    </div>
  );
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
  const explainsNoProfile = profile.state === "no_profile";
  const readFromDecision = profile.state !== "disabled";

  return (
    <Card
      as="section"
      tone={synthetic ? "synthetic" : "default"}
      aria-labelledby="profile-heading"
      className="flex flex-col gap-4"
    >
      <SectionHeading
        level={2}
        size="card"
        id="profile-heading"
        eyebrow="Hesap ele geçirme kontrolü"
        actions={
          <>
            {synthetic && <SyntheticBadge />}
            {profile.escalated && (
              <Badge tone="suspect" size="sm">
                Ek doğrulama istendi
              </Badge>
            )}
            {profile.shadow && (
              <Badge tone="accent" size="sm">
                Gölge modu — karar etkilenmedi
              </Badge>
            )}
          </>
        }
      >
        Müşteri Profili
      </SectionHeading>

      {synthetic && (
        <Alert tone="synthetic">
          Bu karara sentetik demo verisi katıldı: karşılaştırılan müşteri geçmişi ya da oturumun kendisi
          simülatörle üretildi, gerçek bir kişiye ait değil. Sonuç mekanizmayı gösterir, gerçek kişilerdeki
          doğruluğu değil; hiçbir ölçüme katılmaz.
        </Alert>
      )}

      <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <Detail term="Profil durumu">{stateLabel}</Detail>
        <Detail term="Giriş türü">{modalityLabel ?? "—"}</Detail>
        <Detail term="Sapma skoru">
          <span className="num text-h3 font-semibold">{formatNumber(profile.deviation, 2)}</span>
        </Detail>
        <Detail term="Sapma p-değeri">
          <span className="num text-h3 font-semibold">{formatNumber(profile.p_value, 4)}</span>
        </Detail>
      </dl>

      <div className={topFeatures.length > 0 ? "grid gap-5 xl:grid-cols-2" : undefined}>
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="eyebrow">Referans oturum sayısı</h3>
          {countKnown && (
            <span
              className={`text-caption font-medium ${maturityNote === "Olgun" ? "text-risk-safe" : "text-ink-faint"}`}
            >
              {maturityNote}
            </span>
          )}
        </div>
        {countKnown ? (
          <div className="mt-2 space-y-2">
            <p className="num text-h2 font-semibold text-ink">
              {modalityLabel}: {referenceN} / {PROFILE_MIN_SESSIONS}
            </p>
            <div
              role="progressbar"
              aria-label={`${modalityLabel} profili olgunluğu`}
              aria-valuemin={0}
              aria-valuemax={PROFILE_MIN_SESSIONS}
              aria-valuenow={progress}
              className="h-1.5 w-full overflow-hidden rounded-full bg-panel-raised"
            >
              <div
                className={`h-full rounded-full transition-[width] duration-500 ${
                  maturityNote === "Olgun" ? "bg-risk-safe" : "bg-accent"
                }`}
                style={{ width: `${(progress / PROFILE_MIN_SESSIONS) * 100}%` }}
              />
            </div>
            <p className="text-caption text-ink-faint">
              Karar anındaki sayı. Her giriş türü ayrı olgunlaşır; eşik altında katman karşılaştırma yapmaz.
            </p>
            {probationN > 0 && (
              <p className="text-caption text-ink-muted">
                <span className="num font-semibold text-ink">Onay bekleyen oturum: {probationN}</span>
                {" — "}ek doğrulamayla geçti; satıcı ödemeyi onaylayana ya da üst üste 3 başarılı doğrulama
                profili yenileyene kadar karşılaştırmaya ve olgunluğa katılmaz.
              </p>
            )}
          </div>
        ) : (
          <p className="num mt-2 text-h2 font-semibold text-ink-faint">—</p>
        )}
      </div>

      {topFeatures.length > 0 && (
        <div className="border-t border-line pt-4 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
          <h3 className="eyebrow mb-3">En çok sapan özellikler</h3>
          <FeatureBars
            items={topFeatures.map((f) => ({ feature: f.feature, amount: f.z }))}
            format={formatZ}
            axisCaption="Müşterinin kendi referans dağılımından sapma (z), aynı giriş türü içinde."
          />
        </div>
      )}
      </div>

      {/* The state an operator is most likely to misread gets the explanation
          in front of them; on every other state it is a standing caveat and
          lives with the others, one keystroke away. */}
      {explainsNoProfile && <Alert tone="neutral">{READ_FROM_DECISION}</Alert>}

      {/* The layer's contract, folded away: it is the same sentence on every
          session, and an operator reading the tenth card does not need it
          expanded -- but it is one keystroke away, and it is what stops this
          panel from being mistaken for something that blocks payments. */}
      <Disclosure summary="Bu katman ne yapar, ne yapmaz?" className="mt-auto">
        <p>
          Bu katman yalnızca ek doğrulama isteyebilir: engellemez, onaylamaz, risk skorunu değiştirmez. Katmanın
          ayarları yalnızca sentetik kimlikler üzerinde ölçüldü; gerçek müşteri verisi yoktur.
        </p>
        {readFromDecision && !explainsNoProfile && <p>{READ_FROM_DECISION}</p>}
      </Disclosure>
    </Card>
  );
}
