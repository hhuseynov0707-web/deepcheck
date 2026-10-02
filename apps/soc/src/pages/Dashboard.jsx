import { useEffect, useMemo, useRef, useState } from "react";

import { apiGet, MESSAGES, PanoError, UnauthorizedError } from "../api.js";
import Alert from "../components/Alert.jsx";
import AppShell from "../components/AppShell.jsx";
import Badge from "../components/Badge.jsx";
import Button from "../components/Button.jsx";
import Card from "../components/Card.jsx";
import EmptyState from "../components/EmptyState.jsx";
import FeatureBars from "../components/FeatureBars.jsx";
import LiveStatus from "../components/LiveStatus.jsx";
import MetricCard from "../components/MetricCard.jsx";
import ProfilePanel from "../components/ProfilePanel.jsx";
import RiskChart from "../components/RiskChart.jsx";
import SectionHeading from "../components/SectionHeading.jsx";
import SessionTable, { SIMULATED_SESSION_TITLE } from "../components/SessionTable.jsx";
import Skeleton, { SkeletonText } from "../components/Skeleton.jsx";
import SyntheticBadge from "../components/SyntheticBadge.jsx";
import {
  BanIcon,
  ClockIcon,
  FilterIcon,
  GaugeIcon,
  LogOutIcon,
  PulseIcon,
  RadarIcon,
  ShieldIcon,
  UsersIcon,
} from "../components/icons.jsx";
import { riskLevelByKey, riskLevelFor } from "../components/riskLevels.js";

// Ported from the former combined demo's dashboard (frontend/, removed
// 2026-10-02), kept feature for feature. What changed, and only this:
//   - authentication: no key in the browser. soc-api holds DASHBOARD_KEY and
//     this page polls same-origin /api/... with its session cookie
//     (src/api.js); a 401 hands control back to App.jsx's login screen;
//   - the shell: a top bar carries the live controls and a data-feed status
//     (components/AppShell.jsx, components/LiveStatus.jsx);
//   - the look: the SOC palette and IBM Plex (designTokens.js).
// Every rule below -- live follow, "Yeni", "Değerlendiriliyor", the presenter
// cutoff, the metric exclusions, "Son kaydedilen karar" -- is the former demo
// page's, with its reasoning. backend/test_profiles.py pins the constants and
// label tables that copy backend names and numbers against the backend.

const REFRESH_MS = 3000;

// How long a card keeps its "Yeni" mark after the poll that first listed it:
// long enough for a juror to find it on the projector, short enough that the
// mark still means "just now". It is expired by the next poll, so it shows for
// 10-13 seconds.
const NEW_SESSION_MS = 10_000;

// "Canlı" while the session's newest behaviour is younger than this. The SDK
// keeps posting its 10 s rolling window for about 10 s after a person's last
// input, so 15 s covers that tail plus a slow poll; past it, the session has
// gone quiet.
const LIVE_ACTIVITY_MS = 15_000;

// The same tail, read the other way, for the presenter view. A session whose
// last input came before "Görünümü sıfırla" is still seen up to 10 s after it:
// the SDK re-sends its 10 s rolling window on every 2 s tick and sends nothing
// once no timestamped event is left in it (sdk/deepcheck.js ROLLING_WINDOW_MS).
// Seen later than this past the cutoff, something happened on that page after
// the reset -- new input, as when a rehearsal /demo tab is used again on stage,
// or a passed demo step-up, whose verified_at write moves last_seen_at too
// (models.Session, onupdate=now()) -- and it is shown. 12 s is that 10 s plus
// one 2 s tick of margin: arithmetic, not a value tuned on any measurement.
const RESUMED_AFTER_CUTOFF_MS = 12_000;

// The server's decision gate, mirrored rather than re-invented. In
// backend/main.py _decide_on_evidence reads the newest SPRT_MAX_FLUSHES
// windows and holds every decision at verify ("insufficient_evidence" or
// "unobserved") until MIN_FLUSHES_FOR_DECISION of them observed a generator --
// _observed_a_generator: at least one structural feature measured, a window
// with no recorded mask counting as observed. GET /api/score sends that
// verdict per window as `observed`. The stored score is the median of up to
// the newest five windows (scorer.smooth_session_score), and an unobserved
// window joins that median like any other, so until the count is reached the
// score may rest on windows the gate does not accept as evidence. The panel
// says "Değerlendiriliyor" then instead of printing a band nobody decided on.
// How far off such a score can be was measured under a related criterion, not
// this one: in benchmark.py's opening-seconds slice (SYNTHETIC form fills),
// 35% of legitimate sessions scored above the block threshold on windows with
// fewer than six MEASURED features (backend/scorer.py,
// MIN_MEASURED_FOR_CONFIDENT_SCORE). Nothing was measured on the
// observed-window count itself. Pinned against main.py by
// backend/test_profiles.py.
export const DECISION_WINDOW = 10;
export const MIN_OBSERVED_WINDOWS = 3;

// The SOC endpoints expose every customer's live session id and score. The
// legacy page asked for DASHBOARD_KEY and kept it in sessionStorage; here the
// key never reaches the browser at all. soc-api compared it once at login and
// set an httpOnly cookie, which fetch() sends on its own (credentials
// "same-origin", src/api.js) and which no script on this page can read.
//
// That cookie is still proof of a SHARED key, not of an analyst: there is one
// key, not one per person, and no audit trail of who watched what. The real
// answer is an operator login with per-user sessions; this is honest about
// being less.

// The presenter's "Görünümü sıfırla". Sessions are kept for 24 hours and the
// list returns up to 200 of them, so every rehearsal run of the day would be
// listed and counted in front of the jury. The cutoff HIDES older sessions
// from this tab -- it deletes nothing on the server, and the banner says how
// many are hidden. Kept in sessionStorage: a reload keeps the presenter view,
// closing the tab ends it. The name is the legacy page's; the SOC is its own
// origin, so the two never share it.
const CUTOFF_STORAGE = "deepcheck.dashboardCutoff";

const NO_IDS = new Set();

function readStoredCutoff() {
  try {
    const value = Number(window.sessionStorage.getItem(CUTOFF_STORAGE));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function storeCutoff(value) {
  try {
    if (value) window.sessionStorage.setItem(CUTOFF_STORAGE, String(value));
    else window.sessionStorage.removeItem(CUTOFF_STORAGE);
  } catch {
    /* not fatal: the cutoff stays in memory for this page view */
  }
}

// A failed fetch() rejects with the BROWSER's own message, and that string is
// English and locale-independent: "Failed to fetch" in Chrome, "NetworkError
// when attempting to fetch resource." in Firefox. Rendering it put English in
// front of a Turkish-speaking analyst. Only messages written in this app are
// shown; anything else is reported as what a rejected fetch() actually means
// -- the server was not reached at all.
function panoMesaji(err) {
  if (err instanceof PanoError) return err.message;
  return MESSAGES.unreachable;
}

// soc-api answers 502 with a Turkish detail when the CORE failed (down, slow,
// or rejecting soc-api's own key). That detail names which, so it is shown
// after this page's own sentence; anything that is not that shape is not.
async function coreDetail(res) {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" && body.detail ? ` (${body.detail})` : "";
  } catch {
    return "";
  }
}

// Every timestamp on this page comes from the API as an ISO string. A missing
// one is an em dash, never a fabricated "now".
function formatStamp(iso) {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "medium" });
}

function formatClock(iso) {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("tr-TR");
}

function formatFixed(value, digits, suffix = "") {
  return Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : "-";
}

// "14:05’ten", "14:10’dan", "14:20’den". The Turkish ablative after a time
// follows the last word as it is read aloud -- the minutes, or the hour when
// the minutes are 00 -- in vowel harmony and in voicing: "beş" takes -ten,
// "on" -dan, "kırk" -tan, "yirmi" -den. A fixed "’den" is wrong for half the
// clock.
const UNIT_ABLATIVE = { 1: "den", 2: "den", 3: "ten", 4: "ten", 5: "ten", 6: "dan", 7: "den", 8: "den", 9: "dan" };
const TENS_ABLATIVE = { 0: "dan", 10: "dan", 20: "den", 30: "dan", 40: "tan", 50: "den" };

function ablative(n) {
  return n % 10 === 0 ? TENS_ABLATIVE[n] : UNIT_ABLATIVE[n % 10];
}

export function sinceClock(ms) {
  const at = new Date(ms);
  const hours = at.getHours();
  const minutes = at.getMinutes();
  const pad = (v) => String(v).padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}’${ablative(minutes === 0 ? hours : minutes)}`;
}

// When a session STARTED, in ms. created_at is written once, by the first
// flush that carried behaviour; last_seen_at stands in only when a server does
// not send it. Unparseable reads as the epoch: the oldest, never the newest.
function startedAt(session) {
  const ms = Date.parse(session.created_at ?? session.last_seen_at);
  return Number.isFinite(ms) ? ms : 0;
}

// The session live follow shows: the one that started last.
//
// By start, not by last activity -- which is how /api/sessions orders its
// rows. The teammate's tab keeps posting for about 10 s after their last
// input, and any touch on it while the bot runs keeps it posting; following
// the newest last_seen_at would then bounce between the two sessions on every
// poll. A start time only changes when a session begins, so follow moves once
// per new session. On a tie, the current selection stays.
function pickLive(list, currentId) {
  if (list.length === 0) return null;
  const current = list.find((s) => s.session_id === currentId) ?? list[0];
  return list.reduce((best, s) => (startedAt(s) > startedAt(best) ? s : best), current).session_id;
}

// What the presenter view shows: sessions that started after the cutoff, and
// sessions that started before it but were active again after it
// (RESUMED_AFTER_CUTOFF_MS). The second kind matters because created_at is
// fixed by a session's first flush and the SDK keeps one session per page
// load, so a rehearsal /demo tab used again on stage would otherwise stay
// hidden however active it was -- and live follow, which only picks among
// visible sessions, could never select it. Any timestamped input counts, a
// tab switch included: a rehearsal tab the teammate merely switches away from
// after the reset reappears as well. Live follow still orders by start
// (pickLive), so a session that started after the cutoff is followed ahead of
// a resumed one however recently the resumed one was seen.
//
// The cutoff is this browser's Date.now() and created_at / last_seen_at are
// the backend's clock; in the demo both run on the presenter's laptop, so they
// are one clock. Seen from another machine, clock skew moves the line by the
// skew.
function resumedAfter(session, cutoff) {
  const seen = Date.parse(session.last_seen_at ?? "");
  return Number.isFinite(seen) && seen - cutoff > RESUMED_AFTER_CUTOFF_MS;
}

function visibleSince(list, cutoff) {
  return cutoff ? list.filter((s) => startedAt(s) >= cutoff || resumedAfter(s, cutoff)) : list;
}

// Whether a session is happening now: its newest behaviour is younger than
// LIVE_ACTIVITY_MS. One rule for the selected-session badge and the list card,
// so the two never disagree about the same session.
function isActive(lastSeenAt) {
  const seen = Date.parse(lastSeenAt ?? "");
  return Number.isFinite(seen) && Date.now() - seen < LIVE_ACTIVITY_MS;
}

// Observed windows among the newest DECISION_WINDOW, counted as the decision
// gate counts them: `observed` is the server's own per-window verdict (a
// window without one -- an older row -- counts, as it does there), and the
// window needs a finite score to enter the statistic at all. A backend that
// sends no `observed` field is approximated by the gate's first check, the
// number of windows.
function observedWindows(history) {
  const newest = history.slice(-DECISION_WINDOW);
  if (!newest.some((row) => row && "observed" in row)) return newest.length;
  return newest.filter((row) => row.observed !== false && Number.isFinite(row.risk_score)).length;
}

// What the server DID with the last payment request, in an analyst's words.
// The colour is the band whose published ladder action this is (40/60/80,
// main.ACTION_LADDER), so "Engellendi" reads in the same rose as "Bot Tespit
// Edildi"; the word carries the meaning and the colour only repeats it.
export const DECISION_ACTION_LABELS = {
  allow: { label: "Onaylandı", level: "safe" },
  warn: { label: "Uyarıyla onaylandı", level: "suspect" },
  verify: { label: "Ek doğrulama istendi", level: "high" },
  block: { label: "Engellendi", level: "blocked" },
};

// Every reason main.py can record (REASON_MESSAGES), in Turkish. This is the
// analyst's view, so it is the INTERNAL reason: the scored client is told a
// collapsed "step_up" in place of six of them (main.PUBLIC_REASONS), because
// naming the check that fired is a tuning signal -- submit, read the reason,
// adjust, repeat. The real one stays in decision_audit and here.
export const DECISION_REASON_LABELS = {
  score: "Yumuşatılmış risk skoru, 40/60/80 eşik merdivenine göre",
  unknown_session: "Oturum bulunamadı - davranış analizi yapılamadı",
  // Two server branches record this (main._decide_on_evidence): fewer than
  // MIN_FLUSHES_FOR_DECISION stored windows, or a sequential test still
  // inconclusive with fewer than SPRT_MAX_FLUSHES stored. The words cover both,
  // so a session with six observed windows and an inconclusive test is not
  // told it lacks windows.
  insufficient_evidence:
    "Davranış kanıtı henüz karar için yeterli değil (kayıtlı pencere az ya da ardışık test henüz sonuçsuz)",
  stale: "Oturumun davranış verisi güncel değil",
  cluster: "Aynı davranış kalıbı kısa sürede çok sayıda oturumda tekrarlandı",
  ambiguous: "Davranış yeterince uzun izlendi, ancak kesin bir sonuca varılamadı",
  sequential: "Oturum boyunca biriken davranış kanıtı otomasyona işaret ediyor",
  conformal: "Skor yüksek, ancak gerçek kullanıcı dağılımına uyuyor - engel yerine ek doğrulama",
  verified: "Ek doğrulama başarıyla tamamlandı",
  unobserved: "Gözlenen davranış kanıtı yetersiz - pencerelerde yapısal özellik ölçülemedi",
  profile_deviation: "Davranış, müşterinin kendi geçmişinden belirgin biçimde ayrılıyor",
  profile_rate_limited: "Bu müşteri için kısa sürede çok sayıda ödeme kararı istendi",
  step_up: "Ek doğrulama gerekiyor",
};

// What the merchant side received -- the core's public message -- for the
// "Satıcı tarafına giden genel gerekçe" line, which quotes it. Who that is
// depends on the integration and is not in the record: TechStore's server
// (checkout-api) receives it and builds every page response from its own
// literals, so it never reaches the payer's page; a synthetic session simulated
// from the command line (backend/demo_seed.py --simulate) prints it in that
// terminal. Only where the analyst label above
// differs from the core's sentence: insufficient_evidence is also the PUBLIC form of
// `unobserved`, so the analyst wording ("kayıtlı pencere az ya da ardışık test
// henüz sonuçsuz") would name two causes neither of which applied there. This
// is main.REASON_MESSAGES["insufficient_evidence"] with its Turkish letters.
const PUBLIC_REASON_MESSAGES = {
  insufficient_evidence: "Karar için yeterli davranış verisi yok, lütfen birkaç saniye sonra tekrar deneyin",
};

// A verify whose reason describes the STATE OF THE TELEMETRY rather than a
// verdict on it: the reasons main.PUBLIC_REASONS leaves uncollapsed for that
// very reason, plus "unobserved", which the client is told as
// "insufficient_evidence". The server neither approved nor blocked, and it did
// not ask for proof because of anything it saw -- it had too little, too
// inconclusive or too old behaviour to decide on. "Ek doğrulama istendi" would
// have the presenter narrate a challenge the payment page may never have
// shown. What the merchant side then did is not in this record: TechStore's
// server (checkout-api, per docs/architecture-two-apps.md) asks again up to
// three times, 2 s apart, before it shows a code at all. The note under the
// badge says so instead of guessing what happened. (unknown_session cannot actually reach this
// panel -- a session without a row has no decision_audit row and no
// /api/score -- and is listed so a future path to it is still not a step-up.)
const TELEMETRY_STATE_REASONS = new Set(["insufficient_evidence", "unobserved", "stale", "unknown_session"]);

export const DEFERRED_DECISION_LABEL = "Karar ertelendi";

function isDeferred(decision) {
  if (decision.action !== "verify") return false;
  return TELEMETRY_STATE_REASONS.has(decision.public_reason ?? decision.reason);
}

// The sentence the live region reads when follow moves to a new session. Built
// from the detail, not the list row, so a session the server cannot decide on
// yet is announced as being evaluated rather than under a band.
function describeFollowSwitch(detail) {
  const short = String(detail.session_id ?? "").slice(0, 8);
  const score = formatFixed(detail.risk_score, 1);
  const history = Array.isArray(detail.history) ? detail.history : [];
  const observed = observedWindows(history);
  if (observed < MIN_OBSERVED_WINDOWS) {
    return `Canlı takip yeni oturuma geçti: ${short}. Değerlendiriliyor (${observed}/${MIN_OBSERVED_WINDOWS} gözlenen pencere), ön skor ${score}.`;
  }
  const label = Number.isFinite(detail.risk_score) ? riskLevelFor(detail.risk_score).label : "skor yok";
  return `Canlı takip yeni oturuma geçti: ${short}. ${label}, risk skoru ${score}.`;
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

// Whether the selected session is happening now. Both stamps come from the
// presenter's laptop: last_seen_at is written by the backend there, and
// Date.now() is this browser on the same machine, so there is one clock. A
// browser whose clock runs behind the server's would see a negative age; it
// reads as 0 rather than as "in the future".
function ActivityBadge({ lastSeenAt }) {
  const seen = Date.parse(lastSeenAt ?? "");
  if (!Number.isFinite(seen)) return null;
  const age = Math.max(0, Date.now() - seen);
  if (isActive(lastSeenAt)) {
    return (
      <Badge tone="accent" size="sm" dot pulse>
        {`Canlı - son etkinlik ${Math.round(age / 1000)} sn önce`}
      </Badge>
    );
  }
  return <Badge tone="neutral" size="sm">{`Etkinlik yok - son görülme ${formatClock(lastSeenAt)}`}</Badge>;
}

// The band beside the score is the PUBLISHED ladder entry; this is what the
// server answered the last RECORDED payment request with (GET /api/score
// last_decision, read from decision_audit). They differ exactly when it
// matters on stage: a session in the block band answered verify because too
// few of its windows were observed, or a score in the safe band stepped up by
// the sequential test.
//
// "Son kaydedilen karar", not "Son karar": decision_audit is the profile
// layer's table. Nothing is written while the layer is off, and with it on an
// allow naming no customer is not written either: main._learn_and_audit
// returns before its first write when the layer is off or when there is no
// profile context and the action is allow. A warn, a verify or a block is
// written with or without a customer. So a later approval without a customer
// reference -- a passed step-up included -- leaves an earlier verify or block
// on screen; the label claims only what the table can show.
export const LAST_DECISION_LABEL = "Son kaydedilen karar";

function LastDecision({ decision }) {
  if (!decision) {
    // "No record" is not "no decision".
    return (
      <div className="rounded-field border border-line bg-canvas-sunken px-3 py-2.5">
        <p className="eyebrow">{LAST_DECISION_LABEL}</p>
        <p className="mt-1 text-body text-ink-muted">
          Karar kaydı yok - kayıt yalnızca profil katmanı açıkken tutulur
        </p>
        <p className="mt-1 text-caption leading-snug text-ink-faint">
          Ödeme isteği henüz gönderilmemiş olabilir. Katman açıkken de müşteri referansı taşımayan bir
          “Onaylandı” kararı kaydedilmez.
        </p>
      </div>
    );
  }
  const deferred = isDeferred(decision);
  const action = DECISION_ACTION_LABELS[decision.action];
  const level = action ? riskLevelByKey(action.level) : null;
  const reason = DECISION_REASON_LABELS[decision.reason];
  const told =
    decision.public_reason && decision.public_reason !== decision.reason
      ? (PUBLIC_REASON_MESSAGES[decision.public_reason] ?? DECISION_REASON_LABELS[decision.public_reason] ?? null)
      : null;
  return (
    <div className="rounded-field border border-line bg-canvas-sunken px-3 py-2.5">
      <p className="eyebrow">{LAST_DECISION_LABEL}</p>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
        {deferred ? (
          // Neutral, like "Değerlendiriliyor": no risk colour may mean "not
          // decided".
          <Badge tone="neutral" size="md" icon={PulseIcon}>
            {DEFERRED_DECISION_LABEL}
          </Badge>
        ) : (
          <Badge tone={action?.level ?? "neutral"} size="md" icon={level?.Icon}>
            {action?.label ?? "Tanımlanmamış karar"}
          </Badge>
        )}
        <span className="num text-caption text-ink-faint">{formatClock(decision.decided_at)}</span>
        {Number.isFinite(decision.risk_score) && (
          <span className="num text-caption text-ink-faint">
            {`karar anındaki skor ${decision.risk_score.toFixed(1)}`}
          </span>
        )}
      </p>
      <p className="mt-1.5 text-caption leading-relaxed text-ink-muted">
        {reason ?? (decision.reason ? `Tanımlanmamış gerekçe (${decision.reason})` : "Gerekçe kaydı yok")}
      </p>
      {told && (
        <p className="mt-1 text-caption leading-relaxed text-ink-faint">
          {`Satıcı tarafına giden genel gerekçe: “${told}” - TechStore'da bunu mağaza sunucusu alır ve ödeme sayfasına iletmez. Hangi kontrolün devreye girdiği skorlanan tarafa söylenmez.`}
        </p>
      )}
      {deferred && (
        <p className="mt-1 text-caption leading-relaxed text-ink-faint">
          Sunucu ödemeyi ne onayladı ne engelledi; ek doğrulama yanıtı verdi, ancak bu bir davranış tespiti
          değil: karar için yeterli ya da güncel davranış kanıtı yoktu. Satıcı tarafının bu yanıta nasıl karşılık
          verdiği bu kayıtta görünmez. TechStore'un sunucusu, kanıt yetersizse kararı 2 saniye arayla en fazla 3
          kez sessizce yeniden sorar, ardından kod adımını gösterir; veri güncel değilse doğrudan kod adımına
          geçer.
        </p>
      )}
    </div>
  );
}

// What the SOC list and the metric cards look like before the first answer
// arrives. Shaped like the page that is coming, so nothing jumps when it does.
function DashboardSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <div className="xl:col-span-4">
        <Card className="space-y-3">
          <Skeleton className="h-4 w-32" />
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </Card>
      </div>
      <div className="space-y-4 xl:col-span-8">
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

// `onUnauthorized` is called once when soc-api answers 401 to either poll --
// the session cookie expired, was logged out elsewhere or never existed -- and
// App.jsx then shows the login screen. `onLogout` is the "Oturumu Kapat"
// button; App.jsx calls POST /api/logout.
export default function Dashboard({ onUnauthorized, onLogout }) {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  // Errors from the list poll and from the selected session's poll are kept
  // apart. They used to share one state that only the list poll cleared, so a
  // detail fetch that kept failing put the red alert up and took it down
  // again every three seconds.
  const [error, setError] = useState(null);
  const [detailError, setDetailError] = useState(null);
  // "Nothing has come back yet", "nothing could be fetched" and "nothing
  // exists" are three different pages. They were all the same one: four metric
  // cards reading 0, which is a measurement nobody made. `loadedOnce` turns
  // true only when /api/sessions has actually answered.
  const [loadedOnce, setLoadedOnce] = useState(false);
  // When /api/sessions last answered (this browser's clock), for the top
  // bar's data-feed status. null until the first answer.
  const [lastUpdated, setLastUpdated] = useState(null);
  // "Canlı takip": the selection follows the session that started last, so
  // the presenter does not have to find the teammate's or the bot's session
  // in a list grouped by band. On by default; a click on a card turns it off.
  const [followLive, setFollowLive] = useState(true);
  const [cutoff, setCutoff] = useState(readStoredCutoff);
  const [newIds, setNewIds] = useState(NO_IDS);
  // Read by a screen reader only, and changed only when live follow moves to
  // another session -- never on a poll, which would be noise every 3 seconds.
  const [announcement, setAnnouncement] = useState("");
  // soc-api said 401. Both polls stop at once rather than keep asking with a
  // cookie that no longer works, and App.jsx is told.
  const [signedOut, setSignedOut] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  // The polls are set up once and must not restart on every click --
  // restarting dropped the current cycle and re-fetched at once -- so what
  // they read is mirrored in refs, written together with the state.
  const selectedRef = useRef(null);
  const followRef = useRef(true);
  const cutoffRef = useRef(cutoff);
  // session id -> when this page first listed it (ms); null for the sessions
  // that were already there on the first answer. null until that answer.
  const seenRef = useRef(null);
  // The session whose detail, once it arrives, is to be announced.
  const announceRef = useRef(null);
  const signedOutRef = useRef(false);
  // The latest callback, so the polls set up once still call the current one.
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  function selectSession(id) {
    selectedRef.current = id;
    setSelectedId(id);
  }

  function setFollow(on) {
    followRef.current = on;
    setFollowLive(on);
  }

  // Decides what is selected after the list changed. Live follow takes the
  // newest-started session; otherwise the selection stays -- unless it is no
  // longer in the visible list (hidden by the cutoff, swept by retention),
  // where there is nothing left to keep.
  function reconcileSelection(list, announce) {
    const visible = visibleSince(list, cutoffRef.current);
    const current = selectedRef.current;
    const listed = visible.some((s) => s.session_id === current);
    const next = followRef.current || !listed ? pickLive(visible, current) : current;
    if (next === current) return;
    announceRef.current = announce && followRef.current && next !== null ? next : null;
    selectSession(next);
  }

  // Which cards say "Yeni". The first answer is the backlog the page opened
  // onto, so it seeds the record and marks nothing -- otherwise every session
  // of the day would be "new". After that, an id not seen before is stamped
  // with the time this page first listed it. Ids that have left the list are
  // dropped, which keeps the record bounded on a page left open for hours.
  function trackNew(list) {
    const now = Date.now();
    const previous = seenRef.current;
    const seen = new Map();
    const fresh = new Set();
    for (const s of list) {
      let first = null;
      if (previous?.has(s.session_id)) first = previous.get(s.session_id);
      else if (previous) first = now;
      seen.set(s.session_id, first);
      if (first !== null && now - first < NEW_SESSION_MS) fresh.add(s.session_id);
    }
    seenRef.current = seen;
    return fresh;
  }

  // One place decides what a 401 means, so one from either poll ends the
  // dashboard instead of leaving a dead page refreshing every 3 seconds.
  function handleAuthFailure() {
    if (signedOutRef.current) return;
    signedOutRef.current = true;
    announceRef.current = null;
    setSignedOut(true);
    onUnauthorizedRef.current?.();
  }

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    await onLogout?.();
  }

  function chooseSession(id) {
    // A click means "I want to read this one". Left on, live follow would pull
    // the view away the moment another session started.
    setFollow(false);
    announceRef.current = null;
    selectSession(id);
  }

  function toggleFollow() {
    if (followRef.current) {
      setFollow(false);
      return;
    }
    setFollow(true);
    // Back on: show the live session now, not at the next poll up to 3 s later.
    reconcileSelection(sessions, true);
  }

  function resetView() {
    const at = Date.now();
    storeCutoff(at);
    cutoffRef.current = at;
    setCutoff(at);
    reconcileSelection(sessions, true);
  }

  function showAll() {
    storeCutoff(null);
    cutoffRef.current = null;
    setCutoff(null);
    reconcileSelection(sessions, true);
  }

  useEffect(() => {
    if (signedOut) return undefined;
    let cancelled = false;
    // One request at a time. On a backend slower than REFRESH_MS the interval
    // stacked requests, and an older answer landing after a newer one put the
    // list back in time.
    let inFlight = false;

    async function fetchSessions() {
      if (inFlight) return;
      inFlight = true;
      try {
        const res = await apiGet("/api/sessions");
        if (res.status === 401) throw new UnauthorizedError();
        if (!res.ok) throw new PanoError(`Oturumlar alınamadı${await coreDetail(res)}`);
        const data = await res.json();
        if (cancelled) return;
        const firstLoad = seenRef.current === null;
        setNewIds(trackNew(data));
        setSessions(data);
        setLoadedOnce(true);
        setLastUpdated(Date.now());
        setError(null);
        // The session the page opens onto is not news; every later move of
        // live follow is.
        reconcileSelection(data, !firstLoad);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) handleAuthFailure();
        else setError(panoMesaji(err));
      } finally {
        inFlight = false;
      }
    }

    fetchSessions();
    const timer = window.setInterval(fetchSessions, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [signedOut]);

  useEffect(() => {
    // A new selection must not be described by the previous session's detail
    // for the length of a fetch; the panels show their loading shape instead.
    setSelectedDetail(null);
    setDetailError(null);
    if (!selectedId || signedOut) return undefined;
    let cancelled = false;
    let inFlight = false;

    async function fetchDetail() {
      if (inFlight) return;
      inFlight = true;
      try {
        // The id came from soc-api's own list, and soc-api checks its shape
        // again before it reaches the core; encoded anyway, so no id can
        // change the path it is put in.
        const res = await apiGet(`/api/score/${encodeURIComponent(selectedId)}`);
        if (res.status === 401) throw new UnauthorizedError();
        if (res.status === 404) {
          throw new PanoError("Bu oturum artık sunucuda yok - saklama süresi dolmuş olabilir.");
        }
        if (!res.ok) throw new PanoError(`Oturum detayı alınamadı.${await coreDetail(res)}`);
        const data = await res.json();
        if (cancelled) return;
        setSelectedDetail(data);
        setDetailError(null);
        if (announceRef.current === selectedId) {
          announceRef.current = null;
          setAnnouncement(describeFollowSwitch(data));
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) handleAuthFailure();
        else setDetailError(panoMesaji(err));
      } finally {
        inFlight = false;
      }
    }

    fetchDetail();
    const timer = window.setInterval(fetchDetail, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId, signedOut]);

  // Everything below -- metrics, list, live follow -- reads the presenter
  // view, so a hidden session can neither be counted nor followed.
  const visibleSessions = useMemo(() => visibleSince(sessions, cutoff), [sessions, cutoff]);
  const hiddenCount = sessions.length - visibleSessions.length;

  // The selected session as GET /api/score last described it. It is the only
  // session whose windows this page has, so the only one it can know the
  // server would not decide on yet: /api/sessions carries no window counts.
  // Only ever the SELECTED session's: in the render that changes the
  // selection, before the detail effect has cleared it, the state still holds
  // the previous session's detail, which would mark that one undecided and
  // describe the new one with its stamps.
  const detail = selectedDetail !== null && selectedDetail.session_id === selectedId ? selectedDetail : null;
  const history = Array.isArray(detail?.history) ? detail.history : [];
  const observed = observedWindows(history);
  // Not yet decidable by the server's own rule (see MIN_OBSERVED_WINDOWS). The
  // number stays visible, labelled for what it is; the band does not.
  const provisional = Boolean(detail) && observed < MIN_OBSERVED_WINDOWS;
  // Live follow has just moved to a session this page first listed moments
  // ago (it still carries "Yeni"), and that session's windows have not
  // arrived yet. Its stored score is then the one this page knows least about
  // -- the first poll that lists a session comes within seconds of its first
  // window -- and drawing it in its band for the length of the detail fetch
  // put a red "Bot Tespit Edildi" card and "Tespit Edilen Bot: 1" on the
  // projector before both turned into "Değerlendiriliyor". Until the detail
  // says otherwise it is treated as undecided. Not every selection waiting on
  // its detail: a click on a session already listed would make that card
  // jump groups for a frame.
  const awaitingNew = detail === null && followLive && selectedId !== null && newIds.has(selectedId);
  const provisionalId = provisional ? detail.session_id : awaitingNew ? selectedId : null;
  const provisionalIds = useMemo(() => (provisionalId ? new Set([provisionalId]) : undefined), [provisionalId]);

  // The four cards are figures a jury reads as "what the system saw", so a
  // simulated session (backend/demo_seed.py --simulate) is left out of all of
  // them: synthetic data may be SHOWN, labelled, but never counted into a
  // number. The list below still shows those sessions, with their badge, and
  // the note under the cards says how many were left out.
  //
  // A session the server would not decide on yet is left out of the bot count
  // and both averages, and each of those cards says so. Its band is a verdict
  // nobody made -- a window in which nothing was measurable scores 99.1 on
  // neutral fallbacks alone (main._decide_on_evidence) -- and counted, it put
  // "Tespit Edilen Bot: 1" beside a selected card reading "Değerlendiriliyor".
  // Both averages leave it out, not only the risk one, so the two are taken
  // over the same sessions. "Toplam Oturum" still counts it: it is a session,
  // whatever its score. Only the selected session can be known to be
  // undecided, so another one is counted by its stored label, as the list
  // files it -- and so is a selected one whose detail has not arrived yet,
  // unless live follow has just moved to it (awaitingNew above).
  const metrics = useMemo(() => {
    const counted = visibleSessions.filter((s) => s.is_synthetic !== true);
    const syntheticCount = visibleSessions.length - counted.length;
    const total = counted.length;
    const decided = counted.filter((s) => s.session_id !== provisionalId);
    const provisionalCount = total - decided.length;
    const scored = decided.length;
    const avgRisk = scored ? decided.reduce((sum, s) => sum + (s.risk_score || 0), 0) / scored : 0;
    const botCount = decided.filter((s) => s.label === "Bot Tespit Edildi").length;
    const responseValues = decided.filter((s) => typeof s.response_time_ms === "number");
    const avgResponse = responseValues.length
      ? responseValues.reduce((sum, s) => sum + s.response_time_ms, 0) / responseValues.length
      : 0;
    return { total, scored, avgRisk, botCount, avgResponse, syntheticCount, provisionalCount };
  }, [visibleSessions, provisionalId]);

  if (signedOut) {
    // App.jsx replaces this page with the login screen in the same update;
    // this only shows if nothing above it does.
    return (
      <AppShell>
        <div className="mx-auto max-w-md px-4 py-20">
          <Alert tone="info">{MESSAGES.sessionEnded}</Alert>
        </div>
      </AppShell>
    );
  }

  const level = detail && Number.isFinite(detail.risk_score) ? riskLevelFor(detail.risk_score) : null;
  const shap = Array.isArray(detail?.shap_explanation) ? detail.shap_explanation : [];
  // Whether the followed session is happening now, read from the stamp the
  // selected-session badge reads (ActivityBadge), so the list card and that
  // badge cannot disagree about it. Until the detail arrives there is no badge
  // and the list row's stamp stands in.
  const followedRow = visibleSessions.find((s) => s.session_id === selectedId);
  const followedActive = isActive(detail ? detail.last_seen_at : followedRow?.last_seen_at);
  // Colour of the average is the band that average falls in -- the same table
  // the badges and the chart read, never a second opinion about what is high.
  const avgLevel = riskLevelFor(metrics.avgRisk);
  // Said on each card that leaves the undecided session out, under its own
  // hint: a juror reads a figure together with the line beneath it.
  const notCounted =
    metrics.provisionalCount > 0 ? `${metrics.provisionalCount} oturum değerlendiriliyor, sayılmadı` : null;
  const metricHint = (text) =>
    notCounted ? (
      <>
        {text}
        <span className="block">{notCounted}</span>
      </>
    ) : (
      text
    );
  // Until /api/sessions has answered once, the four cards have no figures:
  // a shape while the answer may still arrive, an em dash once the request has
  // failed. Never a zero standing in for an unknown.
  const pending = { loading: !loadedOnce && !error, unavailable: !loadedOnce && Boolean(error) };
  // Sessions are listed but every one of them is still undecided -- the human
  // act's opening seconds after "Görünümü sıfırla". Both averages are then
  // taken over nothing; an em dash, not a 0.0 nobody measured beside
  // "Toplam Oturum 1". The hint under each card says why.
  const noneDecided = loadedOnce && metrics.total > 0 && metrics.scored === 0;
  const since = cutoff !== null ? sinceClock(cutoff) : null;

  // The top bar's controls. Outside the list on purpose: the list's cards are
  // its only pressed-state buttons.
  const controls = (
    <>
      <Button
        variant={followLive ? "primary" : "secondary"}
        size="sm"
        aria-pressed={followLive}
        onClick={toggleFollow}
        title="Açıkken pano, en son başlayan oturumu kendiliğinden seçer. Bir oturuma tıklamak takibi kapatır."
      >
        <RadarIcon className="h-4 w-4 shrink-0" />
        {followLive ? "Canlı takip: Açık" : "Canlı takip: Kapalı"}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        onClick={resetView}
        title="Bu andan önce başlayan oturumları bu sekmede gizler; bunlardan sonradan yeniden etkinleşen oturum yine gösterilir. Sunucuda hiçbir veri silinmez."
      >
        <FilterIcon className="h-4 w-4 shrink-0" />
        Görünümü sıfırla
      </Button>
      <Button variant="ghost" size="sm" onClick={logout} loading={loggingOut}>
        {!loggingOut && <LogOutIcon className="h-4 w-4 shrink-0" />}
        Oturumu Kapat
      </Button>
    </>
  );

  return (
    <AppShell
      status={<LiveStatus lastUpdated={lastUpdated} error={error} refreshMs={REFRESH_MS} />}
      actions={controls}
    >
    <div className="mx-auto max-w-[100rem] space-y-5 px-4 py-5 sm:px-6 sm:py-6">
      <SectionHeading
        level={1}
        size="page"
        eyebrow={`Oturumlar ${REFRESH_MS / 1000} sn’de bir yenilenir`}
        description="Skoru ve kararı sunucu üretir; bu pano yalnızca sunucunun gönderdiğini gösterir."
      >
        Canlı Oturum İzleme
      </SectionHeading>

      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>

      {error && (
        <Alert tone="danger" role="alert" title="Pano verisi alınamadı">
          {error}
        </Alert>
      )}

      {since !== null && (
        <Alert
          tone="info"
          actions={
            <Button variant="secondary" size="sm" onClick={showAll}>
              Tümünü göster
            </Button>
          }
        >
          {`Sunum görünümü: ${since} beri - ${hiddenCount} eski oturum gizlendi (veriler silinmedi)`}
        </Alert>
      )}

      <section aria-label="Özet metrikler" className="space-y-2">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard
            label="Toplam Oturum"
            icon={UsersIcon}
            value={metrics.total}
            decimals={0}
            hint={
              since !== null
                ? `${since} beri başlayan ya da yeniden etkinleşen oturumlar`
                : "Sunucunun döndürdüğü son oturumlar"
            }
            {...pending}
          />
          <MetricCard
            label="Ortalama Risk Skoru"
            icon={GaugeIcon}
            value={metrics.avgRisk}
            decimals={1}
            hint={metricHint("Oturum başına yumuşatılmış skor")}
            {...pending}
            unavailable={pending.unavailable || noneDecided}
            accent={metrics.scored ? avgLevel.text : undefined}
          />
          <MetricCard
            label="Tespit Edilen Bot"
            icon={BanIcon}
            value={metrics.botCount}
            decimals={0}
            hint={metricHint("Etiketi 80-100 bandında olan oturum")}
            {...pending}
            tone={metrics.botCount > 0 ? "blocked" : "muted"}
          />
          <MetricCard
            label="Ortalama Yanıt Süresi"
            icon={ClockIcon}
            value={metrics.avgResponse}
            decimals={1}
            suffix=" ms"
            hint={metricHint("Sunucunun ölçtüğü skorlama süresi")}
            {...pending}
            unavailable={pending.unavailable || noneDecided}
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
            description="Bir ödeme sayfası (TechStore) tarayıcıda açıldığında SDK davranış göndermeye başlar ve oturumlar birkaç saniye içinde burada listelenir."
          />
        </Card>
      ) : visibleSessions.length === 0 ? (
        <Card padding="lg">
          <EmptyState
            icon={PulseIcon}
            title="Bu görünümde henüz oturum yok"
            description={`Sunum görünümü yalnızca ${since} sonra başlayan ya da yeniden etkinleşen oturumları gösterir. Ödeme sayfasında biri formu doldurmaya başladığında oturum birkaç saniye içinde burada belirir${
              followLive ? " ve canlı takip onu kendiliğinden seçer" : ""
            }.`}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
          {/* Master. Bounded and sticky on a wide screen (below the sticky top
              bar) so the detail beside it governs how long the page is, and
              the list stays reachable without scrolling back up past a chart;
              a long list scrolls inside its own card. */}
          <div className="min-w-0 xl:sticky xl:top-[5.5rem] xl:col-span-4">
            <Card
              as="section"
              aria-labelledby="oturum-listesi-baslik"
              className="flex flex-col gap-4 xl:max-h-[calc(100vh-7rem)]"
            >
              <SectionHeading
                level={2}
                size="card"
                id="oturum-listesi-baslik"
                eyebrow="Risk sırasına göre"
                actions={
                  <Badge tone="neutral" size="sm">
                    Listede {visibleSessions.length}
                  </Badge>
                }
              >
                Oturum Akışı
              </SectionHeading>
              <div className="-mx-1 min-h-0 flex-1 px-1 xl:overflow-y-auto">
                {/* Only the selected session can be known to be undecided:
                    /api/sessions carries no window counts (provisionalId). */}
                <SessionTable
                  sessions={visibleSessions}
                  selectedId={selectedId}
                  onSelect={chooseSession}
                  layout="rail"
                  newIds={newIds}
                  followedId={followLive ? selectedId : null}
                  followedActive={followedActive}
                  provisionalIds={provisionalIds}
                />
              </div>
            </Card>
          </div>

          {/* Detail. Three bands: what this session is, what it did over time,
              and the two explanations of why. The first band pairs the session
              summary with the SHAP card because the two are about the same
              moment -- the latest behaviour window -- and they come out the
              same height, so neither column is left half empty. */}
          <div className="min-w-0 space-y-4 xl:col-span-8">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
            <Card
              as="section"
              aria-labelledby="secili-oturum-baslik"
              className="space-y-5 lg:col-span-7"
            >
              <SectionHeading
                level={2}
                size="card"
                id="secili-oturum-baslik"
                eyebrow={followLive ? "Canlı takip - en son başlayan oturum" : "İzlenen oturum"}
                actions={
                  detail && (
                    <>
                      <ActivityBadge lastSeenAt={detail.last_seen_at} />
                      {detail.is_synthetic === true && <SyntheticBadge title={SIMULATED_SESSION_TITLE} />}
                    </>
                  )
                }
              >
                Seçili Oturum
              </SectionHeading>

              {detailError && (
                <Alert tone="danger" role="alert" title="Seçili oturum güncellenemedi">
                  {detailError}
                </Alert>
              )}

              {detail ? (
                <>
                  {detail.is_synthetic === true && (
                    <Alert tone="synthetic">{SIMULATED_SESSION_TITLE}</Alert>
                  )}

                  <div className="flex overflow-hidden rounded-field border border-line bg-canvas-sunken">
                    <span
                      aria-hidden="true"
                      className={`w-1.5 shrink-0 ${provisional ? "bg-line-strong" : (level?.fill ?? "bg-line-strong")}`}
                    />
                    <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-6 gap-y-4 px-4 py-4">
                      <div className="min-w-0">
                        <p className="eyebrow">{provisional ? "Ön skor" : "Risk skoru"}</p>
                        <p className="mt-1 flex items-baseline gap-3">
                          <span
                            className={`num text-metric-lg font-semibold leading-none ${
                              provisional ? "text-ink-muted" : (level?.text ?? "")
                            }`}
                          >
                            {formatFixed(detail.risk_score, 1)}
                          </span>
                          {provisional ? (
                            <span className="flex items-center gap-1.5 text-ink-muted">
                              <PulseIcon className="h-4 w-4 shrink-0" />
                              <span className="text-lead font-semibold">Değerlendiriliyor</span>
                            </span>
                          ) : (
                            level && (
                              <span className={`flex items-center gap-1.5 ${level.text}`}>
                                <level.Icon className="h-4 w-4 shrink-0" />
                                <span className="text-lead font-semibold">{level.label}</span>
                              </span>
                            )
                          )}
                        </p>
                      </div>
                      {provisional ? (
                        <p className="max-w-[34ch] text-caption leading-relaxed text-ink-faint">
                          <span className="font-medium text-ink-muted">
                            {`Karar için en az ${MIN_OBSERVED_WINDOWS} gözlenen pencere gerekir (${observed}/${MIN_OBSERVED_WINDOWS}).`}
                          </span>{" "}
                          O zamana kadar sunucu bu skorla ne onaylar ne engeller; ödeme isteğine ek doğrulama ile
                          yanıt verir.
                        </p>
                      ) : (
                        level && (
                          <p className="max-w-[34ch] text-caption leading-relaxed text-ink-faint">
                            Bandın yayımlanmış karşılığı:{" "}
                            <span className="font-medium text-ink-muted">{level.action}</span>. Kararı ödeme
                            isteğinde sunucu verir.
                          </p>
                        )
                      )}
                    </div>
                  </div>

                  {/* Present only when the backend sends the field, which it
                      always does: null means "nothing recorded". */}
                  {"last_decision" in detail && <LastDecision decision={detail.last_decision} />}

                  <div>
                    <p className="eyebrow">Oturum kimliği</p>
                    <p className="num mt-1 break-all text-caption text-ink-muted">{detail.session_id}</p>
                  </div>

                  <dl className="grid grid-cols-2 gap-2">
                    <Fact term="Son pencere kesinliği" hint="max(p, 1−p) - kalibre edilmiş doğruluk değil">
                      {Number.isFinite(detail.confidence) ? `${(detail.confidence * 100).toFixed(0)}%` : "-"}
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
                !detailError && (
                  <div className="space-y-4">
                    <Skeleton className="h-20 w-full" />
                    <SkeletonText lines={2} />
                    <p className="sr-only">Oturum detayı yükleniyor.</p>
                  </div>
                )
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
    </AppShell>
  );
}
