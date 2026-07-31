import useAnimatedNumber from "../hooks/useAnimatedNumber.js";

export default function MetricCard({ label, value, suffix = "", decimals = 0, accent = "text-zinc-50" }) {
  const animated = useAnimatedNumber(Number.isFinite(value) ? value : 0, 600);

  return (
    <div className="bg-[#18181b] border border-zinc-800 rounded-lg p-5 shadow-xl shadow-black/50">
      <p className="text-xs font-medium text-zinc-400 uppercase tracking-wider mb-2">{label}</p>
      <p className={`text-3xl font-semibold font-mono tabular-nums ${accent}`}>
        {animated.toFixed(decimals)}
        {suffix}
      </p>
    </div>
  );
}
