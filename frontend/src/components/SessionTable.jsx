import { useState } from "react";

import EmptyState from "./EmptyState.jsx";
import { PulseIcon } from "./icons.jsx";
import { RISK_LEVELS } from "./riskLevels.js";
import SyntheticBadge from "./SyntheticBadge.jsx";

const DEFAULT_VISIBLE = 5;

// GET /api/sessions marks a session driven by backend/demo_seed.py --simulate
// with is_synthetic: true. Strictly true, so a server that does not send the
// field yet shows no badge rather than a wrong one.
export const SIMULATED_SESSION_TITLE = "Simüle edilmiş oturum — gerçek bir kişi değil; hiçbir ölçüme katılmaz.";

// Highest risk first: operators and judges need to see the dangerous sessions
// immediately, not scroll past a pile of clean ones to find them. The bands,
// their Turkish labels, their colours and their glyphs all come from
// riskLevels.js, so this list cannot drift from the badge or the chart.
const CATEGORIES = [...RISK_LEVELS].reverse().map((level, index, all) => {
  const lower = index === all.length - 1 ? -Infinity : all[index + 1].upperBound;
  return { ...level, match: (s) => s.risk_score >= lower && s.risk_score < level.upperBound };
});

function formatTime(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleTimeString("tr-TR");
}

function SessionCard({ session, level, isSelected, onSelect }) {
  const Icon = level.Icon;
  return (
    <button
      aria-pressed={isSelected}
      onClick={() => onSelect?.(session.session_id)}
      className={`group relative flex w-full cursor-pointer items-stretch overflow-hidden rounded-field border text-left
        transition-colors ${
          isSelected
            ? "border-accent/60 bg-panel-raised"
            : "border-line bg-panel hover:border-line-strong hover:bg-panel-raised/60"
        }`}
    >
      {/* Colour rail: the band, repeated for the eye. Never the only signal --
          the glyph and the Turkish label below say the same thing. */}
      <span aria-hidden="true" className={`w-1 shrink-0 ${level.fill}`} />
      <span className="min-w-0 flex-1 px-3 py-2.5">
        <span className="flex items-center justify-between gap-2">
          <span className="num truncate text-caption text-ink-faint">{session.session_id.slice(0, 13)}…</span>
          <span className="num shrink-0 text-caption text-ink-faint">{formatTime(session.last_seen_at)}</span>
        </span>
        {session.is_synthetic === true && (
          <span className="mt-1.5 flex">
            <SyntheticBadge size="sm" title={SIMULATED_SESSION_TITLE} />
          </span>
        )}
        <span className="mt-1.5 flex items-end justify-between gap-2">
          <span className={`flex min-w-0 items-center gap-1.5 ${level.text}`}>
            <Icon className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate text-caption font-medium">{session.label}</span>
          </span>
          <span className={`num shrink-0 text-h2 font-bold leading-none ${level.text}`}>
            {session.risk_score?.toFixed(1)}
          </span>
        </span>
      </span>
    </button>
  );
}

// How the cards flow. "rail" is the SOC dashboard's narrow left column, which
// is one card wide on a large screen and two wide below xl, where the list
// spans the page instead of sitting beside the detail view.
const LAYOUTS = {
  auto: "grid-cols-1 sm:grid-cols-2",
  rail: "grid-cols-1 sm:grid-cols-2 xl:grid-cols-1",
};

function CategorySection({ category, items, selectedId, onSelect, expanded, onToggle, layout }) {
  const isExpanded = Boolean(expanded);
  const visible = isExpanded ? items : items.slice(0, DEFAULT_VISIBLE);
  const Icon = category.Icon;

  return (
    <section aria-label={`${category.label} (${items.length})`}>
      <div className="mb-2 flex items-center gap-2">
        <Icon className={`h-3.5 w-3.5 shrink-0 ${category.text}`} />
        <h3 className={`text-eyebrow font-semibold uppercase tracking-[0.09em] ${category.text}`}>
          {category.label}
        </h3>
        <span className="num rounded-full border border-line bg-panel px-1.5 text-[0.625rem] leading-5 text-ink-faint">
          {items.length}
        </span>
        <span aria-hidden="true" className="h-px flex-1 bg-line" />
      </div>
      <div className={`grid gap-2 ${LAYOUTS[layout] ?? LAYOUTS.auto}`}>
        {visible.map((s) => (
          <SessionCard
            key={s.session_id}
            session={s}
            level={category}
            isSelected={selectedId === s.session_id}
            onSelect={onSelect}
          />
        ))}
      </div>
      {items.length > DEFAULT_VISIBLE && (
        <button
          onClick={onToggle}
          aria-expanded={isExpanded}
          className="mt-2 w-full cursor-pointer rounded-field border border-line bg-panel py-2 text-eyebrow font-semibold
                     uppercase tracking-[0.09em] text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
        >
          {isExpanded ? "Daralt" : `Tümünü Göster (${items.length})`}
        </button>
      )}
    </section>
  );
}

export default function SessionTable({ sessions = [], selectedId, onSelect, layout = "auto" }) {
  const [expandedCategories, setExpandedCategories] = useState({});

  if (sessions.length === 0) {
    return (
      <EmptyState
        icon={PulseIcon}
        title="Henüz oturum kaydı yok"
        description="Ödeme demosu bir tarayıcıda açıldığında oturumlar birkaç saniye içinde burada listelenir."
      />
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
          layout={layout}
          expanded={expandedCategories[category.key]}
          onToggle={() =>
            setExpandedCategories((prev) => ({ ...prev, [category.key]: !prev[category.key] }))
          }
        />
      ))}
    </div>
  );
}
