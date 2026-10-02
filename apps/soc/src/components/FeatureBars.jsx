import { featureLabel } from "./featureLabels.js";

// A short ranked list of named feature magnitudes: the SHAP explanation on the
// SOC dashboard, and the per-customer profile's most deviating features.
//
// It replaced a D3 bar chart that drew the feature names into the SVG, where a
// long name (kanal_gecis_gecikmesi) was clipped by the label gutter, the value
// of the longest bar was pushed off the right edge, and the whole thing
// vanished for a poll cycle whenever the container was re-measured. Laid out in
// HTML there is nothing to clip: the name wraps, the value sits in its own
// right-aligned tabular column, and a screen reader reads the numbers as text
// instead of needing a parallel sr-only list.
//
// The bar is a picture of a number that is already written next to it, so it is
// aria-hidden and it is never the only carrier of anything.

// The axis maximum: the smallest "round" number at or above the largest value,
// so the scale is a number a reviewer can name rather than "whatever the
// biggest bar happened to be". A bar drawn against max-of-the-data always fills
// the row and silently overstates the leader.
export function niceMax(value) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

const trimZero = (n) => String(Number(n.toFixed(2)));

export default function FeatureBars({
  items = [],
  // amount -> the exact string printed in the value column, unit included.
  // The caller owns it because the unit differs (SHAP points vs a z score) and
  // because the printed string is what a test can assert on.
  format = (amount) => amount.toFixed(1),
  axisCaption,
  className = "",
}) {
  const usable = items.filter((item) => typeof item?.feature === "string" && Number.isFinite(item?.amount));
  if (usable.length === 0) return null;

  const scaleMax = niceMax(Math.max(...usable.map((item) => Math.abs(item.amount))));

  return (
    <div className={className}>
      <ol className="space-y-3.5">
        {usable.map((item) => {
          const gloss = featureLabel(item.feature);
          const share = Math.min(Math.abs(item.amount) / scaleMax, 1) * 100;
          return (
            <li key={item.feature}>
              <div className="flex items-baseline justify-between gap-3">
                <p className="min-w-0 text-body font-medium leading-snug text-ink">{gloss ?? item.feature}</p>
                <p className="num shrink-0 text-h3 font-semibold leading-none text-ink">{format(item.amount)}</p>
              </div>
              <div className="mt-1 flex items-baseline justify-between gap-3">
                {/* The backend's own name for the feature, kept visible: it is
                    what backend/lstm_model.py, the docs and the logs call it. */}
                <p className="num min-w-0 truncate text-caption text-ink-faint">{item.feature}</p>
                {Number.isFinite(item.value) && (
                  <p className="num shrink-0 text-caption text-ink-faint">ölçülen {trimZero(item.value)}</p>
                )}
              </div>
              <div
                aria-hidden="true"
                className="mt-2 h-2 w-full overflow-hidden rounded-full bg-panel-raised ring-1 ring-inset ring-line"
              >
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
                  style={{ width: `${share}%` }}
                />
              </div>
            </li>
          );
        })}
      </ol>

      {/* The axis, written out. Without it the bars are only relative to each
          other and the reader has to take the ranking on trust. */}
      <div aria-hidden="true" className="mt-3 flex h-1.5 items-stretch justify-between border-t border-line">
        {[0, 1, 2].map((i) => (
          <span key={i} className="w-px bg-line" />
        ))}
      </div>
      <div className="mt-1 grid grid-cols-3 text-caption text-ink-faint">
        <span className="num text-left">0</span>
        <span className="num text-center">{trimZero(scaleMax / 2)}</span>
        <span className="num text-right">{trimZero(scaleMax)}</span>
      </div>
      {axisCaption && <p className="mt-2 text-caption leading-relaxed text-ink-faint">{axisCaption}</p>}
    </div>
  );
}
