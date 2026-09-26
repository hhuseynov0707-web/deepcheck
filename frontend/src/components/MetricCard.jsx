import Card from "./Card.jsx";
import Skeleton from "./Skeleton.jsx";
import Stat from "./Stat.jsx";
import useAnimatedNumber from "../hooks/useAnimatedNumber.js";

// One headline figure on the SOC dashboard.
//
// The number counts up to its new value rather than snapping: on a panel that
// repolls every three seconds, the movement is what tells an operator the
// figure changed at all. Someone with prefers-reduced-motion set gets the new
// value immediately instead (see hooks/useAnimatedNumber.js).
//
// `accent` is the legacy prop and is still a raw text colour class, so callers
// that pass one keep working; `tone` is the semantic one and should be used.
export default function MetricCard({
  label,
  value,
  suffix = "",
  decimals = 0,
  hint,
  tone = "default",
  accent,
  // Before the first /api/sessions answer there is no figure. A card showing
  // "0" would be a measurement nobody made; it shows the shape of the number
  // instead, and the label stays so the row does not reflow when it lands.
  loading = false,
  // The figure could not be fetched at all. An em dash, because a shimmer that
  // never resolves reads as "still coming" when nothing is coming.
  unavailable = false,
}) {
  const animated = useAnimatedNumber(Number.isFinite(value) ? value : 0, 600);
  const pending = loading || unavailable;

  let shown = animated.toFixed(decimals);
  if (unavailable) shown = "—";
  else if (loading) shown = <Skeleton className="mt-1 h-7 w-20" />;

  return (
    <Card className="min-w-0">
      <Stat
        label={label}
        value={shown}
        suffix={pending ? "" : suffix}
        hint={hint}
        tone={unavailable ? "muted" : accent ? null : tone}
        valueClassName={unavailable ? "" : (accent ?? "")}
      />
    </Card>
  );
}
