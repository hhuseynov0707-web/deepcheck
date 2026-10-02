import { useEffect, useState } from "react";

// The top bar's answer to "is what I am looking at current?" -- about the
// SOC's own data feed, never about a session (that is "Canlı" / "Etkinlik
// yok" on the selected session, decided in pages/Dashboard.jsx).
//
// It says "güncel" only while backed by a current source: the last
// /api/sessions answer arrived within three poll intervals. Past that, with no
// error yet (a request still hanging behind a slow core), it says the data is
// late and when it last arrived; after an error, that the connection is down
// and, again, when the data on screen is from. A wall-clock time is printed
// every time, so a figure is never presented without its age.
//
// Deliberately NOT a live region: the page already has one (the follow
// announcement), and a pill re-announced every three seconds would drown it.

function formatClock(ms) {
  return new Date(ms).toLocaleTimeString("tr-TR");
}

const TONES = {
  live: { box: "border-status-live/35 bg-status-live/10 text-status-live", dot: "bg-status-live", pulse: true },
  stale: { box: "border-line-strong bg-panel-raised text-ink-muted", dot: "bg-ink-faint", pulse: false },
  offline: { box: "border-danger-strong/45 bg-danger-strong/10 text-danger", dot: "bg-danger-strong", pulse: false },
  connecting: { box: "border-line-strong bg-panel-raised text-ink-muted", dot: "bg-ink-faint", pulse: true },
};

export default function LiveStatus({ lastUpdated, error, refreshMs }) {
  const staleAfter = refreshMs * 3;
  // The lastUpdated value that has gone stale. One timer per answer, fired
  // only if no newer answer replaced it in time -- instead of a one-second
  // ticker re-rendering the whole bar to find out.
  const [staleFor, setStaleFor] = useState(null);
  useEffect(() => {
    if (lastUpdated == null) return undefined;
    const wait = Math.max(0, lastUpdated + staleAfter - Date.now());
    const timer = window.setTimeout(() => setStaleFor(lastUpdated), wait);
    return () => window.clearTimeout(timer);
  }, [lastUpdated, staleAfter]);

  let state = "connecting";
  if (error) state = "offline";
  else if (lastUpdated != null) state = staleFor === lastUpdated ? "stale" : "live";
  const tone = TONES[state];

  const when = lastUpdated != null ? formatClock(lastUpdated) : null;
  let text;
  if (state === "live") text = "Veri güncel";
  else if (state === "stale") text = "Veri gecikiyor";
  else if (state === "offline") text = "Bağlantı yok";
  else text = "Bağlanıyor";

  return (
    <span
      className={`inline-flex min-h-[2.25rem] max-w-full items-center gap-2 rounded-full border px-3 text-caption font-medium ${tone.box}`}
      title={`Pano ${refreshMs / 1000} sn’de bir yenilenir; ${staleAfter / 1000} sn yanıt gelmezse “gecikiyor” yazar.`}
    >
      <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
        {tone.pulse && <span className={`absolute inline-flex h-full w-full animate-pulse-ring rounded-full ${tone.dot}`} />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${tone.dot}`} />
      </span>
      <span className="truncate">{text}</span>
      {when && (
        <span className="num shrink-0 text-ink-muted">
          {state === "live" ? when : `son güncelleme ${when}`}
        </span>
      )}
    </span>
  );
}
