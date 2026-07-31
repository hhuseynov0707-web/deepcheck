import useAnimatedNumber from "../hooks/useAnimatedNumber.js";

const LEVELS = [
  { max: 40, label: "Gerçek Kullanıcı", classes: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", dot: "bg-emerald-400" },
  { max: 60, label: "Şüpheli", classes: "bg-amber-500/10 text-amber-400 border-amber-500/20", dot: "bg-amber-400" },
  { max: 80, label: "Yüksek Risk", classes: "bg-orange-500/10 text-orange-400 border-orange-500/20", dot: "bg-orange-400" },
  { max: 101, label: "Bot Tespit Edildi", classes: "bg-rose-500/10 text-rose-400 border-rose-500/20", dot: "bg-rose-400" },
];

function getLevel(score) {
  return LEVELS.find((l) => score < l.max) ?? LEVELS[LEVELS.length - 1];
}

export default function RiskBadge({ riskScore = 0, size = "md", live = true }) {
  const level = getLevel(riskScore);
  const animatedScore = useAnimatedNumber(riskScore, 500);
  const sizeClasses = size === "lg" ? "px-4 py-2 text-sm" : "px-2.5 py-1 text-xs";

  return (
    <div
      className={`inline-flex items-center gap-2 rounded-full border font-mono font-bold transition-colors duration-200 ease-out ${level.classes} ${sizeClasses}`}
    >
      {live && (
        <span className="relative flex h-1.5 w-1.5">
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${level.dot} opacity-75`} />
          <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${level.dot}`} />
        </span>
      )}
      <span className="uppercase tracking-wide">{level.label}</span>
      <span className="tabular-nums">{animatedScore.toFixed(1)}</span>
    </div>
  );
}
