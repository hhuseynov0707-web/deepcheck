import Card from "./Card.jsx";
import SectionHeading from "./SectionHeading.jsx";
import { RISK_LEVELS } from "./riskLevels.js";

// The published score ladder, printed so a juror can read what a number on the
// live panel is supposed to mean instead of inferring it from a colour.
//
// It is a LEGEND, not a control. The boundaries and the action words come from
// riskLevels.js, the same table the badge and the meter read; nothing here
// decides anything, and the card says so in its own words. The real decision
// is POST /api/decision on the server, which applies this ladder plus the
// checks the page is never told about.
//
// `activeKey` only marks the band the live score currently falls in, using the
// level the panel already resolved -- this component never looks at a score.
export default function DecisionLadder({ activeKey = null }) {
  return (
    <Card padding="lg">
      <SectionHeading level={2} size="card" eyebrow="Yayımlanmış eşik merdiveni">
        Karar bantları
      </SectionHeading>

      <ul className="mt-4 space-y-1">
        {RISK_LEVELS.map((level, i) => {
          const from = i === 0 ? 0 : RISK_LEVELS[i - 1].upperBound;
          const to = Number.isFinite(level.upperBound) ? level.upperBound : 100;
          const active = level.key === activeKey;
          return (
            <li
              key={level.key}
              className={`flex items-start gap-3 rounded-field border px-3 py-2.5 transition-colors ${
                active ? `${level.tint} ${level.border}` : "border-transparent"
              }`}
            >
              <level.Icon className={`mt-0.5 h-4 w-4 shrink-0 ${active ? level.text : "text-ink-faint"}`} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className={`truncate text-body font-medium ${active ? level.text : "text-ink"}`}>
                    {level.label}
                  </span>
                  <span className="num shrink-0 text-caption text-ink-faint">
                    {from}–{to}
                  </span>
                </div>
                <p className="mt-0.5 text-caption text-ink-faint">{level.action}</p>
              </div>
              {/* The active band is stated in words as well as in colour, for
                  greyscale and for anyone who cannot separate the two warm
                  hues at the top of the ladder. */}
              {active && <span className="sr-only">— şu anki bant</span>}
            </li>
          );
        })}
      </ul>

      {/* Deliberately does not name the additional checks. Which check fired is
          the tuning signal the server collapses into a single "step_up"; a page
          the scored party reads has no business listing them either. */}
      <p className="mt-4 text-caption leading-relaxed text-ink-faint">
        Eşikleri sunucu uygular ve ek kontroller sonucu değiştirebilir. Nihai kararı ödeme isteğinde sunucu
        verir.
      </p>
    </Card>
  );
}
