import { useState } from "react";

const DEFAULT_VISIBLE = 5;

// Highest risk first: operators/judges need to see the dangerous sessions
// immediately, not scroll past a pile of clean ones to find them.
const CATEGORIES = [
  { key: "bot", label: "Bot Tespit Edildi", match: (s) => s.risk_score >= 80, bar: "bg-rose-500", text: "text-rose-400" },
  { key: "yuksek", label: "Yüksek Risk", match: (s) => s.risk_score >= 60 && s.risk_score < 80, bar: "bg-orange-500", text: "text-orange-400" },
  { key: "supheli", label: "Şüpheli", match: (s) => s.risk_score >= 40 && s.risk_score < 60, bar: "bg-amber-500", text: "text-amber-400" },
  { key: "gercek", label: "Gerçek Kullanıcı", match: (s) => s.risk_score < 40, bar: "bg-emerald-500", text: "text-emerald-400" },
];

function formatTime(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleTimeString("tr-TR");
}

function SessionCard({ session, accent, isSelected, onSelect }) {
  return (
    <button
      onClick={() => onSelect?.(session.session_id)}
      className={`text-left overflow-hidden bg-[#18181b] border rounded-lg transition-colors duration-200 ease-out ${
        isSelected ? "border-zinc-600" : "border-zinc-800 hover:border-zinc-700"
      }`}
    >
      <div className={`h-1 w-full ${accent.bar}`} />
      <div className="p-3">
        <div className="flex items-center justify-between mb-1.5">
          <span className="font-mono text-xs text-zinc-500 truncate">{session.session_id.slice(0, 13)}…</span>
          <span className="font-mono text-xs text-zinc-400">{formatTime(session.last_seen_at)}</span>
        </div>
        <div className="flex items-end justify-between">
          <span className="text-sm text-zinc-300">{session.label}</span>
          <span className={`text-xl font-semibold font-mono tabular-nums ${accent.text}`}>
            {session.risk_score?.toFixed(1)}
          </span>
        </div>
      </div>
    </button>
  );
}

function CategorySection({ category, items, selectedId, onSelect, expanded, onToggle }) {
  const isExpanded = Boolean(expanded);
  const visible = isExpanded ? items : items.slice(0, DEFAULT_VISIBLE);

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className={`h-2 w-2 rounded-full ${category.bar}`} />
        <h3 className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
          {category.label} ({items.length})
        </h3>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {visible.map((s) => (
          <SessionCard
            key={s.session_id}
            session={s}
            accent={category}
            isSelected={selectedId === s.session_id}
            onSelect={onSelect}
          />
        ))}
      </div>
      {items.length > DEFAULT_VISIBLE && (
        <button
          onClick={onToggle}
          className="mt-3 w-full text-center bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 hover:border-zinc-700 text-zinc-300 hover:text-zinc-100 text-xs font-medium uppercase tracking-wider rounded-md py-2 transition-colors duration-200 ease-out"
        >
          {isExpanded ? "Daralt" : `Tümünü Göster (${items.length})`}
        </button>
      )}
    </div>
  );
}

export default function SessionTable({ sessions = [], selectedId, onSelect }) {
  const [expandedCategories, setExpandedCategories] = useState({});

  if (sessions.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-[#18181b] px-4 py-10 text-center text-sm text-zinc-400">
        Henüz session bulunmuyor.
      </div>
    );
  }

  const grouped = CATEGORIES.map((category) => ({
    category,
    items: sessions
      .filter(category.match)
      .sort((a, b) => new Date(b.last_seen_at) - new Date(a.last_seen_at)),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-5">
      {grouped.map(({ category, items }) => (
        <CategorySection
          key={category.key}
          category={category}
          items={items}
          selectedId={selectedId}
          onSelect={onSelect}
          expanded={expandedCategories[category.key]}
          onToggle={() =>
            setExpandedCategories((prev) => ({ ...prev, [category.key]: !prev[category.key] }))
          }
        />
      ))}
    </div>
  );
}
