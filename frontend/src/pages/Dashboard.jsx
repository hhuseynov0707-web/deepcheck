import { useEffect, useMemo, useState } from "react";

import Alert from "../components/Alert.jsx";
import Badge from "../components/Badge.jsx";
import Button from "../components/Button.jsx";
import Card from "../components/Card.jsx";
import EmptyState from "../components/EmptyState.jsx";
import FeatureBars from "../components/FeatureBars.jsx";
import Field, { Input } from "../components/Field.jsx";
import MetricCard from "../components/MetricCard.jsx";
import ProfilePanel from "../components/ProfilePanel.jsx";
import RiskChart from "../components/RiskChart.jsx";
import SectionHeading from "../components/SectionHeading.jsx";
import SessionTable, { SIMULATED_SESSION_TITLE } from "../components/SessionTable.jsx";
import Skeleton, { SkeletonText } from "../components/Skeleton.jsx";
import SyntheticBadge from "../components/SyntheticBadge.jsx";
import { LockIcon, PulseIcon, RadarIcon, ShieldIcon } from "../components/icons.jsx";
import { riskLevelFor } from "../components/riskLevels.js";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const REFRESH_MS = 3000;

// The SOC endpoints expose every customer's live session id and score, so
// they sit behind DASHBOARD_KEY. That key is NOT built into this bundle: a
// key compiled into frontend JavaScript is served to everyone who opens the
// page, so `view-source` defeats the header check and what looks like
// authentication is decoration. The analyst types it, and it is kept in
// sessionStorage -- cleared when the tab closes, not shared with other tabs,
// and never written to disk.
//
// A typed key in a browser is still a shared secret rather than a user
// identity. The real answer is an operator login with per-user sessions and
// an audit trail; this is the smallest change that stops the key being
// public, and it is honest about what it is.
const KEY_STORAGE = "deepcheck.dashboardKey";
const UNAUTHORIZED = "Yetkisiz erişim — panoya erişim anahtarı geçersiz";

function readStoredKey() {
  try {
    return window.sessionStorage.getItem(KEY_STORAGE) || "";
  } catch {
    // Private mode or blocked storage: the analyst just retypes the key.
    return "";
  }
}

function storeKey(value) {
  try {
    if (value) window.sessionStorage.setItem(KEY_STORAGE, value);
    else window.sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* not fatal: the key stays in memory for this page view */
  }
}

class UnauthorizedError extends Error {
  constructor() {
    super(UNAUTHORIZED);
    this.name = "UnauthorizedError";
  }
}

// Failures this page recognised and described in Turkish.
class PanoError extends Error {
  constructor(message) {
    super(message);
    this.name = "PanoError";
  }
}

// A failed fetch() rejects with the BROWSER's own message, and that string is
// English and locale-independent: "Failed to fetch" in Chrome, "NetworkError
// when attempting to fetch resource." in Firefox. Rendering it put English in
// front of a Turkish-speaking analyst. Only messages written in this file are
// shown; anything else is reported as what a rejected fetch() actually means
// -- the server was not reached at all.
function panoMesaji(err) {
  if (err instanceof PanoError) return err.message;
  return "Sunucuya ulaşılamadı — bağlantıyı ve sunucunun çalıştığını kontrol edin";
}

// Every timestamp on this page comes from the API as an ISO string. A missing
// one is an em dash, never a fabricated "now".
function formatStamp(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "medium" });
}

function formatFixed(value, digits, suffix = "") {
  return Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : "—";
}

// A figure and its label, side by side in a well. Used for the facts about the
// selected session that are read rather than watched.
function Fact({ term, children, hint }) {
  return (
    <div className="rounded-field border border-line bg-canvas-sunken px-3 py-2.5">
      <dt className="eyebrow">{term}</dt>
      <dd className="num mt-1 text-body font-medium text-ink">{children}</dd>
      {hint && <p className="mt-1 text-caption leading-snug text-ink-faint">{hint}</p>}
    </div>
  );
}

// The access-key screen. It is the first thing a juror may see, so it says what
// the pano is, what the key protects and where the key is kept -- and it is
// built from the same primitives as every other screen rather than from a
// one-off stack of utility classes.
function KeyGate({ value, onChange, onSubmit, error }) {
  return (
    <div className="mx-auto w-full max-w-md px-4 py-14 sm:px-6 sm:py-20">
      <Card as="section" padding="lg" className="space-y-5" aria-labelledby="pano-giris-baslik">
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

        <form onSubmit={onSubmit} className="space-y-4">
          <Field
            id="dashboard-key"
            label="Pano Erişim Anahtarı"
            note="Anahtar yalnızca bu sekmede saklanır (sessionStorage) ve sekme kapandığında silinir."
            error={error ?? undefined}
          >
            {(field) => (
              <Input
                {...field}
                type="password"
                mono
                value={value}
                onChange={(e) => onChange(e.target.value)}
                placeholder="••••••••••••"
                autoFocus
                autoComplete="off"
              />
            )}
          </Field>
          <Button type="submit" variant="primary" fullWidth disabled={!value.trim()}>
            Panoya Gir
          </Button>
        </form>
      </Card>
    </div>
  );
}

// What the SOC list and the metric cards look like before the first answer
// arrives. Shaped like the page that is coming, so nothing jumps when it does.
function DashboardSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
      <div className="xl:col-span-4">
        <Card className="space-y-3">
          <Skeleton className="h-4 w-32" />
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </Card>
      </div>
      <div className="space-y-5 xl:col-span-8">
        <Card className="space-y-4">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-20 w-full" />
          <SkeletonText lines={2} />
        </Card>
        <Card className="space-y-4">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-60 w-full" />
        </Card>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [error, setError] = useState(null);
  const [dashboardKey, setDashboardKey] = useState(readStoredKey);
  const [keyInput, setKeyInput] = useState("");
  // "Nothing has come back yet", "nothing could be fetched" and "nothing
  // exists" are three different pages. They were all the same one: four metric
  // cards reading 0, which is a measurement nobody made. `loadedOnce` turns
  // true only when /api/sessions has actually answered.
  const [loadedOnce, setLoadedOnce] = useState(false);

  // One place decides what a rejected key means, so a 401 from either poll
  // drops the analyst back to the key prompt instead of leaving a dead page
  // refreshing every 3 seconds.
  function clearKey(message) {
    storeKey("");
    setDashboardKey("");
    setSessions([]);
    setSelectedDetail(null);
    setSelectedId(null);
    setError(message);
  }

  const handleAuthFailure = () => clearKey(UNAUTHORIZED);
  const logout = () => clearKey(null);

  function submitKey(e) {
    e.preventDefault();
    const value = keyInput.trim();
    if (!value) return;
    storeKey(value);
    setDashboardKey(value);
    setKeyInput("");
    setError(null);
  }

  useEffect(() => {
    if (!dashboardKey) return;
    let cancelled = false;
    setLoadedOnce(false);

    async function fetchSessions() {
      try {
        const res = await fetch(`${API_URL}/api/sessions`, {
          headers: { "X-Dashboard-Key": dashboardKey },
        });
        if (res.status === 401) throw new UnauthorizedError();
        if (!res.ok) throw new PanoError("Session'lar alınamadı");
        const data = await res.json();
        if (!cancelled) {
          setSessions(data);
          setLoadedOnce(true);
          setError(null);
          // Functional update instead of reading `selectedId` from the
          // closure: depending on it forced this effect to tear down and
          // restart the 3s poll on every click in the session list, which
          // both dropped the current polling cycle and re-fetched immediately
          // on each selection.
          if (data.length > 0) setSelectedId((prev) => prev ?? data[0].session_id);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) handleAuthFailure();
        else setError(panoMesaji(err));
      }
    }

    fetchSessions();
    const timer = window.setInterval(fetchSessions, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [dashboardKey]);

  useEffect(() => {
    if (!selectedId || !dashboardKey) return;
    let cancelled = false;
    // A new selection must not be described by the previous session's detail
    // for the length of a fetch; the panels show their loading shape instead.
    setSelectedDetail(null);

    async function fetchDetail() {
      try {
        const res = await fetch(`${API_URL}/api/score/${selectedId}`, {
          headers: { "X-Dashboard-Key": dashboardKey },
        });
        if (res.status === 401) throw new UnauthorizedError();
        if (!res.ok) throw new PanoError("Session detayı alınamadı");
        const data = await res.json();
        if (!cancelled) setSelectedDetail(data);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) handleAuthFailure();
        else setError(panoMesaji(err));
      }
    }

    fetchDetail();
    const timer = window.setInterval(fetchDetail, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId, dashboardKey]);

  // The four cards are figures a jury reads as "what the system saw", so a
  // simulated session (backend/demo_seed.py --simulate) is left out of all of
  // them: synthetic data may be SHOWN, labelled, but never counted into a
  // number. The list below still shows those sessions, with their badge, and
  // the note under the cards says how many were left out.
  const metrics = useMemo(() => {
    const counted = sessions.filter((s) => s.is_synthetic !== true);
    const syntheticCount = sessions.length - counted.length;
    const total = counted.length;
    const avgRisk = total ? counted.reduce((sum, s) => sum + (s.risk_score || 0), 0) / total : 0;
    const botCount = counted.filter((s) => s.label === "Bot Tespit Edildi").length;
    const responseValues = counted.filter((s) => typeof s.response_time_ms === "number");
    const avgResponse = responseValues.length
      ? responseValues.reduce((sum, s) => sum + s.response_time_ms, 0) / responseValues.length
      : 0;
    return { total, avgRisk, botCount, avgResponse, syntheticCount };
  }, [sessions]);

  if (!dashboardKey) {
    return <KeyGate value={keyInput} onChange={setKeyInput} onSubmit={submitKey} error={error} />;
  }

  const detail = selectedDetail;
  const level = detail && Number.isFinite(detail.risk_score) ? riskLevelFor(detail.risk_score) : null;
  const history = Array.isArray(detail?.history) ? detail.history : [];
  const shap = Array.isArray(detail?.shap_explanation) ? detail.shap_explanation : [];
  // Colour of the average is the band that average falls in -- the same table
  // the badges and the chart read, never a second opinion about what is high.
  const avgLevel = riskLevelFor(metrics.avgRisk);
  // Until /api/sessions has answered once, the four cards have no figures:
  // a shape while the answer may still arrive, an em dash once the request has
  // failed. Never a zero standing in for an unknown.
  const pending = { loading: !loadedOnce && !error, unavailable: !loadedOnce && Boolean(error) };

  return (
    <div className="mx-auto max-w-[92rem] space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <SectionHeading
        level={1}
        size="page"
        eyebrow="Güvenlik Operasyon Merkezi"
        description="Skoru ve kararı sunucu üretir; bu pano yalnızca sunucunun gönderdiğini gösterir."
        actions={
          <>
            <Badge tone="neutral" size="sm" dot pulse>
              {REFRESH_MS / 1000} sn’de bir yenilenir
            </Badge>
            <Button variant="ghost" size="sm" onClick={logout}>
              Oturumu Kapat
            </Button>
          </>
        }
      >
        Canlı Oturum İzleme
      </SectionHeading>

      {error && (
        <Alert tone="blocked" role="alert" title="Pano verisi alınamadı">
          {error}
        </Alert>
      )}

      <section aria-label="Özet metrikler" className="space-y-2">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="Toplam Oturum"
            value={metrics.total}
            decimals={0}
            hint="Sunucunun döndürdüğü son oturumlar"
            {...pending}
          />
          <MetricCard
            label="Ortalama Risk Skoru"
            value={metrics.avgRisk}
            decimals={1}
            hint="Oturum başına yumuşatılmış skor"
            {...pending}
            accent={metrics.total ? avgLevel.text : undefined}
          />
          <MetricCard
            label="Tespit Edilen Bot"
            value={metrics.botCount}
            decimals={0}
            hint="Etiketi 80-100 bandında olan oturum"
            {...pending}
            tone={metrics.botCount > 0 ? "blocked" : "muted"}
          />
          <MetricCard
            label="Ortalama Yanıt Süresi"
            value={metrics.avgResponse}
            decimals={1}
            suffix=" ms"
            hint="Sunucunun ölçtüğü skorlama süresi"
            {...pending}
          />
        </div>
        {metrics.syntheticCount > 0 && (
          <p className="text-caption text-synthetic">
            Metrikler {metrics.syntheticCount} sentetik demo oturumunu (simüle edilmiş, gerçek kişi değil)
            içermez.
          </p>
        )}
      </section>

      {!loadedOnce ? (
        // Nothing has answered yet. With an error already stated above, a
        // shimmering skeleton would promise data that is not coming.
        error ? null : <DashboardSkeleton />
      ) : sessions.length === 0 ? (
        <Card padding="lg">
          <EmptyState
            icon={PulseIcon}
            title="Henüz oturum kaydı yok"
            description="Ödeme demosu bir tarayıcıda açıldığında SDK davranış göndermeye başlar ve oturumlar birkaç saniye içinde burada listelenir."
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
          {/* Master. Bounded and sticky on a wide screen so the detail beside
              it governs how long the page is, and the list stays reachable
              without scrolling back up past a chart. */}
          <div className="min-w-0 xl:col-span-4">
            <Card as="section" aria-labelledby="oturum-listesi-baslik" className="flex flex-col gap-4">
              <SectionHeading
                level={2}
                size="card"
                id="oturum-listesi-baslik"
                eyebrow="Risk sırasına göre"
                actions={
                  <Badge tone="neutral" size="sm">
                    Listede {sessions.length}
                  </Badge>
                }
              >
                Oturum Akışı
              </SectionHeading>
              <div className="min-h-0 flex-1">
                <SessionTable
                  sessions={sessions}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  layout="rail"
                />
              </div>
            </Card>
          </div>

          {/* Detail. Three bands: what this session is, what it did over time,
              and the two explanations of why. The first band pairs the session
              summary with the SHAP card because the two are about the same
              moment -- the latest behaviour window -- and they come out the
              same height, so neither column is left half empty. */}
          <div className="min-w-0 space-y-5 xl:col-span-8">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
            <Card
              as="section"
              aria-labelledby="secili-oturum-baslik"
              className="space-y-5 lg:col-span-7"
            >
              <SectionHeading
                level={2}
                size="card"
                id="secili-oturum-baslik"
                eyebrow="İzlenen oturum"
                actions={detail?.is_synthetic === true && <SyntheticBadge title={SIMULATED_SESSION_TITLE} />}
              >
                Seçili Oturum
              </SectionHeading>

              {detail ? (
                <>
                  {detail.is_synthetic === true && (
                    <Alert tone="synthetic">{SIMULATED_SESSION_TITLE}</Alert>
                  )}

                  <div className="flex overflow-hidden rounded-field border border-line bg-canvas-sunken">
                    <span aria-hidden="true" className={`w-1.5 shrink-0 ${level?.fill ?? "bg-line-strong"}`} />
                    <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-6 gap-y-4 px-4 py-4">
                      <div className="min-w-0">
                        <p className="eyebrow">Risk skoru</p>
                        <p className="mt-1 flex items-baseline gap-3">
                          <span className={`num text-metric-lg font-semibold leading-none ${level?.text ?? ""}`}>
                            {formatFixed(detail.risk_score, 1)}
                          </span>
                          {level && (
                            <span className={`flex items-center gap-1.5 ${level.text}`}>
                              <level.Icon className="h-4 w-4 shrink-0" />
                              <span className="text-lead font-semibold">{level.label}</span>
                            </span>
                          )}
                        </p>
                      </div>
                      {level && (
                        <p className="max-w-[34ch] text-caption leading-relaxed text-ink-faint">
                          Bandın yayımlanmış karşılığı:{" "}
                          <span className="font-medium text-ink-muted">{level.action}</span>. Kararı ödeme
                          isteğinde sunucu verir.
                        </p>
                      )}
                    </div>
                  </div>

                  <div>
                    <p className="eyebrow">Oturum kimliği</p>
                    <p className="num mt-1 break-all text-caption text-ink-muted">{detail.session_id}</p>
                  </div>

                  <dl className="grid grid-cols-2 gap-2">
                    <Fact term="Son pencere kesinliği" hint="max(p, 1−p) — kalibre edilmiş doğruluk değil">
                      {Number.isFinite(detail.confidence) ? `${(detail.confidence * 100).toFixed(0)}%` : "—"}
                    </Fact>
                    <Fact term="Yanıt süresi">{formatFixed(detail.response_time_ms, 1, " ms")}</Fact>
                    <Fact term="İlk görülme">{formatStamp(detail.created_at)}</Fact>
                    <Fact term="Son görülme">{formatStamp(detail.last_seen_at)}</Fact>
                  </dl>

                  {/* `confidence` is max(p, 1-p) of the session's latest flush:
                      the forest's certainty in whichever class it picked, not a
                      calibrated probability of being right, and not about the
                      smoothed score above. "Güven" claimed more. */}
                  <p className="text-caption leading-relaxed text-ink-faint">
                    Risk skoru, son pencerelerin medyanıyla yumuşatılmış oturum skorudur; grafikteki noktalar ham
                    pencere skorlarıdır, bu yüzden son noktadan farklı olabilir. Kesinlik ise modelin son
                    penceredeki seçimine verdiği olasılıktır, bir doğruluk oranı değildir.
                  </p>
                </>
              ) : (
                <div className="space-y-4">
                  <Skeleton className="h-20 w-full" />
                  <SkeletonText lines={2} />
                  <p className="sr-only">Oturum detayı yükleniyor.</p>
                </div>
              )}
            </Card>

            <Card as="section" aria-labelledby="shap-baslik" className="flex flex-col gap-4 lg:col-span-5">
              <SectionHeading
                level={2}
                size="card"
                id="shap-baslik"
                eyebrow="SHAP"
                description="Son davranış penceresinde skoru en çok taşıyan üç özellik."
              >
                Kararı Taşıyan Özellikler
              </SectionHeading>
              {!detail ? (
                <SkeletonText lines={4} />
              ) : shap.length > 0 ? (
                <FeatureBars
                  items={shap.map((item) => ({
                    feature: item.feature,
                    amount: item.impact,
                    value: item.value,
                  }))}
                  format={(amount) => `${amount.toFixed(1)} puan`}
                  axisCaption="Mutlak SHAP katkısı, risk skoru puanı cinsinden; yönü göstermez. “Ölçülen”, özelliğin o penceredeki değeridir."
                />
              ) : (
                <EmptyState
                  icon={ShieldIcon}
                  title="Açıklama kaydı yok"
                  description="Bu oturum için saklanmış bir SHAP açıklaması bulunmuyor. Açıklama yalnızca panoda gösterilir, skorlanan tarafa gönderilmez."
                />
              )}
            </Card>
            </div>

            <Card as="section" aria-labelledby="gecmis-baslik" className="space-y-4">
              <SectionHeading
                level={2}
                size="card"
                id="gecmis-baslik"
                eyebrow="Zaman serisi"
                description="Her nokta, bir davranış penceresinin ham skorudur; karttaki oturum skoru bunların yumuşatılmış hâlidir."
                actions={
                  history.length > 0 && (
                    <Badge tone="neutral" size="sm">
                      {history.length} pencere
                    </Badge>
                  )
                }
              >
                Risk Skoru Geçmişi
              </SectionHeading>
              {!detail ? (
                <Skeleton className="h-60 w-full" />
              ) : history.length > 0 ? (
                <RiskChart history={history} />
              ) : (
                <EmptyState
                  icon={RadarIcon}
                  title="Bu oturum için henüz ölçüm yok"
                  description="Oturum kaydedildi, fakat saklanmış bir davranış penceresi yok. İlk pencere geldiğinde eğri burada çizilir."
                />
              )}
            </Card>

            {/* Present whenever the backend sends the block, which it always
                does: its shape does not depend on whether the customer is
                profiled, so neither does whether this card appears. */}
            {detail?.profile ? (
              <ProfilePanel profile={detail.profile} />
            ) : (
              <Card className="space-y-4">
                <Skeleton className="h-4 w-36" />
                <SkeletonText lines={4} />
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
