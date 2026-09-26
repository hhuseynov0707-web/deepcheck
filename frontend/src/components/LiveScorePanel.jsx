import Badge from "./Badge.jsx";
import Card from "./Card.jsx";
import Skeleton from "./Skeleton.jsx";
import { AlertTriangleIcon, RadarIcon } from "./icons.jsx";
import { RISK_LEVELS, riskLevelFor } from "./riskLevels.js";
import useAnimatedNumber from "../hooks/useAnimatedNumber.js";

// The live output of the SDK, as the checkout page's visual anchor.
//
// DISPLAY ONLY, in the strongest sense: this component renders numbers that
// POST /api/analyze sent back and nothing else. It computes no score, holds no
// threshold that decides anything, and has no interactive element -- whether a
// payment goes through is settled by POST /api/demo/charge on the server, and
// this panel never learns of it.
//
// Every figure below is traceable:
//   score            -> AnalyzeResponse.risk_score (smoothed, server-side)
//   band + colour    -> riskLevels.riskLevelFor(score), the one published table
//   measured / 12    -> AnalyzeResponse.measured_features, out of FEATURE_NAMES
//   response time    -> AnalyzeResponse.response_time_ms
//   window count     -> analyze responses this page has been handed by the SDK
// Anything the server did not send is drawn as an em dash, never as a
// stand-in figure: a placeholder number on a fraud screen is a fabricated
// measurement, and this project does not publish those.

// backend/lstm_model.py FEATURE_NAMES. The denominator of measured_features,
// and the only thing the checkout says about the feature set: how many of the
// twelve this window managed to measure -- never which, and never what they
// are. Naming them to the scored party is the tuning signal SHAP is withheld
// for (CLAUDE.md, Əsas Qaydalar 3).
export const FEATURE_COUNT = 12;

const TICKS = [40, 60, 80];

function bandWidth(level, index) {
  const lower = index === 0 ? 0 : RISK_LEVELS[index - 1].upperBound;
  const upper = Number.isFinite(level.upperBound) ? level.upperBound : 100;
  return upper - lower;
}

// The ladder drawn to scale, with the live score marked on it. aria-hidden:
// the same two facts are in the sr-only sentence beside it, and a screen
// reader does not need a description of coloured rectangles.
function ScoreMeter({ score, level }) {
  const position = Math.min(Math.max(score, 0), 100);

  return (
    <div aria-hidden="true">
      <div className="relative">
        <div className="relative flex h-2.5 overflow-hidden rounded-full bg-canvas-sunken">
          {RISK_LEVELS.map((band, i) => (
            <span
              key={band.key}
              style={{ width: `${bandWidth(band, i)}%` }}
              className={`${band.fill} transition-opacity duration-500 ${
                band.key === level.key ? "opacity-90" : "opacity-20"
              }`}
            />
          ))}
          {/* Hairlines ON TOP of the bands rather than gaps BETWEEN them: a
              gap would eat width and shift the marker off the value it is
              reporting. Here every band still occupies exactly its share of
              the axis. */}
          {TICKS.map((tick) => (
            <span
              key={tick}
              style={{ left: `${tick}%` }}
              className="absolute inset-y-0 w-px -translate-x-1/2 bg-canvas-sunken"
            />
          ))}
        </div>
        {/* The marker is the only thing on this page that moves on its own. It
            moves because the score moved, which is the one change here worth
            animating. */}
        <span
          style={{ left: `${position}%` }}
          className="absolute -top-1 h-[1.125rem] w-[3px] -translate-x-1/2 rounded-full bg-ink
                     shadow-[0_0_0_2px_var(--panel)] transition-[left] duration-500 ease-out"
        />
      </div>
      <div className="relative mt-2 h-3.5 text-[0.625rem] leading-none text-ink-faint">
        <span className="num absolute left-0">0</span>
        {TICKS.map((tick) => (
          <span key={tick} style={{ left: `${tick}%` }} className="num absolute -translate-x-1/2">
            {tick}
          </span>
        ))}
        <span className="num absolute right-0">100</span>
      </div>
    </div>
  );
}

// One measured figure. `value` null means the response did not carry it.
function Measure({ label, value, suffix, hint, after, className = "" }) {
  const known = value !== null && value !== undefined;
  return (
    <div className={`min-w-0 ${className}`}>
      <dt className="eyebrow">{label}</dt>
      <dd className={`num mt-1.5 text-lead font-semibold leading-none ${known ? "text-ink" : "text-ink-faint"}`}>
        {known ? value : "—"}
        {known && suffix && <span className="ml-1 text-caption font-medium text-ink-faint">{suffix}</span>}
        <span className="mt-1.5 block text-[0.625rem] font-normal leading-none text-ink-faint">{hint}</span>
        {after && <span className="mt-2.5 block pr-4">{after}</span>}
      </dd>
    </div>
  );
}

// Twelve ticks, of which `measured` are lit. Drawn rather than written as a
// percentage because the point is that the feature set is small and countable.
function FeatureTicks({ measured }) {
  // Spans, not divs: this renders inside a <dd>'s inline run.
  return (
    <span aria-hidden="true" className="flex gap-1">
      {Array.from({ length: FEATURE_COUNT }, (_, i) => (
        <span
          key={i}
          className={`h-1.5 flex-1 rounded-full transition-colors ${i < measured ? "bg-accent" : "bg-line-strong"}`}
        />
      ))}
    </span>
  );
}

// Every state of this panel is the same shape: a headline slot on the left, a
// wide slot on the right, a measurement strip underneath. The card therefore
// does not resize as the session goes from "registering" to "scored", which is
// the moment a jumping layout would be most distracting.
function Panel({ state, left, right, strip }) {
  return (
    <Card padding="lg">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden="true"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-field border border-accent/35
                       bg-accent/10 text-accent"
          >
            <RadarIcon className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <h2 className="text-h3 font-semibold leading-tight text-ink">Canlı Analiz</h2>
            <p className="eyebrow mt-0.5">DeepCheck SDK</p>
          </div>
        </div>
        {state}
      </div>

      <div className="mt-6 grid gap-x-10 gap-y-6 lg:grid-cols-[minmax(0,19rem)_minmax(0,1fr)] lg:items-center">
        <div className="min-w-0">{left}</div>
        <div className="min-w-0">{right}</div>
      </div>

      {strip}
    </Card>
  );
}

// Evenly divided rather than packed to the left: three figures clumped in the
// corner of a wide card read as leftovers, three columns read as an instrument
// panel. The dividers are decorative and drawn on the columns that have one.
function MeasureStrip({ measured, responseMs, windowCount }) {
  return (
    <dl className="mt-6 grid grid-cols-2 gap-y-5 border-t border-line pt-5 sm:grid-cols-3">
      <Measure
        label="Ölçülen özellik"
        value={measured === null ? null : `${measured}/${FEATURE_COUNT}`}
        hint="son pencerede"
        after={measured === null ? null : <FeatureTicks measured={measured} />}
      />
      <Measure
        label="Yanıt süresi"
        value={responseMs}
        suffix="ms"
        hint="sunucu tarafı"
        className="sm:border-l sm:border-line sm:pl-6"
      />
      <Measure
        label="Analiz yanıtı"
        value={windowCount > 0 ? windowCount : null}
        hint="2 sn'de bir"
        className="sm:border-l sm:border-line sm:pl-6"
      />
    </dl>
  );
}

export default function LiveScorePanel({ risk = null, hasScore = false, unavailable = false, windowCount = 0 }) {
  const score = hasScore ? risk.risk_score : 0;
  const level = riskLevelFor(score);
  const animated = useAnimatedNumber(hasScore ? score : 0, 500);

  const measured = Number.isInteger(risk?.measured_features) ? risk.measured_features : null;
  const responseMs = Number.isFinite(risk?.response_time_ms) ? Math.round(risk.response_time_ms) : null;
  const strip = <MeasureStrip measured={measured} responseMs={responseMs} windowCount={windowCount} />;

  // Telemetry never reached the server, or came back as an error. The server
  // answers that state with step-up rather than approval, so the panel says so
  // instead of leaving a blank where a score belongs.
  if (unavailable) {
    return (
      <Panel
        state={
          <Badge tone="suspect" size="sm" icon={AlertTriangleIcon}>
            Veri yok
          </Badge>
        }
        left={
          <>
            <p className="eyebrow">Risk skoru</p>
            <p className="num mt-1.5 text-metric font-bold leading-none text-ink-faint">—</p>
          </>
        }
        right={
          <div className="max-w-[64ch] rounded-field border border-risk-suspect/30 bg-risk-suspect/10 px-4 py-3.5">
            <p className="text-body font-semibold text-risk-suspect">Risk skoru alınamadı</p>
            <p className="mt-1.5 text-caption leading-relaxed text-ink-muted">
              Davranış verisi sunucuya ulaşmadı. Skoru olmayan bir oturum onaylanmaz; sunucu ek
              doğrulama ister.
            </p>
          </div>
        }
        strip={strip}
      />
    );
  }

  // Nothing has come back yet: the SDK is registering the session, or the
  // first window is still in flight.
  if (!risk) {
    return (
      <Panel
        state={
          <Badge tone="accent" size="sm" dot pulse>
            Ölçülüyor
          </Badge>
        }
        left={
          <>
            <p className="eyebrow">Risk skoru</p>
            <Skeleton className="mt-2 h-9 w-32" />
          </>
        }
        right={
          <p className="max-w-[58ch] text-body leading-relaxed text-ink-muted">
            Oturum kaydediliyor. SDK her 2 saniyede bir davranış penceresi gönderir; ilk skor birkaç saniye
            içinde görünür.
          </p>
        }
        strip={strip}
      />
    );
  }

  // The server measured too little to stand behind the number. A red badge
  // here would accuse someone who arrived two seconds ago, so the score is
  // withheld and what IS known -- how much of the feature set was measured --
  // takes its place.
  if (!hasScore) {
    return (
      <Panel
        state={
          <Badge tone="accent" size="sm" dot pulse>
            Ön ölçüm
          </Badge>
        }
        left={
          <>
            <p className="eyebrow">Risk skoru</p>
            <p className="num mt-1.5 text-metric font-bold leading-none text-ink-faint">—</p>
            <p className="mt-2 text-caption text-ink-muted">Veri yeterli olana kadar gösterilmez</p>
          </>
        }
        right={
          <p className="max-w-[58ch] text-body leading-relaxed text-ink-muted">
            Sunucu bu pencereyi karar için yeterli bulmadı. Forma devam edin; ölçüm doldukça skor görünür.
            Aşağıdaki çizgiler, on iki davranış özelliğinden kaçının ölçülebildiğini gösterir.
          </p>
        }
        strip={strip}
      />
    );
  }

  return (
    <Panel
      state={
        <Badge tone="accent" size="sm" dot pulse>
          Canlı
        </Badge>
      }
      left={
        <>
          <p className="eyebrow">Risk skoru</p>
          <p className={`num mt-1.5 text-metric-lg font-bold leading-none ${level.text}`}>
            {animated.toFixed(1)}
            <span className="ml-1.5 text-h3 font-medium text-ink-faint">/100</span>
          </p>
          <span
            className={`mt-3 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 ${level.tint} ${level.border} ${level.text}`}
          >
            <level.Icon className="h-4 w-4 shrink-0" />
            <span className="text-caption font-semibold uppercase tracking-[0.06em]">{level.label}</span>
          </span>
          {/* The one sentence a screen reader gets for the figure above; the
              meter repeats it visually and is hidden from it. */}
          <span className="sr-only">
            Risk skoru {score.toFixed(1)}, 100 üzerinden. Bant: {level.label}.
          </span>
        </>
      }
      right={
        <div>
          <ScoreMeter score={score} level={level} />
          <p className="mt-5 text-caption leading-relaxed text-ink-muted">
            Bu bandın yayımlanmış karşılığı:{" "}
            <span className={`font-semibold ${level.text}`}>{level.action}</span>
            <span aria-hidden="true" className="mx-2 text-ink-faint">·</span>
            <span className="text-ink-faint">Kararı ödeme isteğinde sunucu verir</span>
          </p>
        </div>
      }
      strip={strip}
    />
  );
}
