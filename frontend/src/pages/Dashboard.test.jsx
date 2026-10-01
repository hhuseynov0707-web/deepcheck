import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProfilePanel, { MODALITY_LABELS, PROFILE_STATE_LABELS } from "../components/ProfilePanel.jsx";
import Dashboard, { sinceClock } from "./Dashboard.jsx";

// The metric cards count up to every new figure over 600 ms, driven by
// requestAnimationFrame and performance.now() -- neither of which vitest's
// fake timers fake (only the timer functions and Date). So the count-up ran on
// the wall clock, and a test that waited for a figure waited on real time:
// under a loaded full run the presenter-reset test, which waits for twelve
// figures, outlasted its timeout. useAnimatedNumber shows a figure at once to
// someone who prefers reduced motion (hooks/useAnimatedNumber.test.js covers
// the count-up itself), so every test here does, and a figure is on screen in
// the render that set it. Stubbed again by a test that unstubs its globals.
function preferReducedMotion() {
  vi.stubGlobal("matchMedia", (query) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  }));
}

// The SOC endpoints expose every customer's live session, so the dashboard
// asks the analyst for the key rather than shipping one in the bundle. These
// tests pin that down: a key compiled into the bundle is served to every
// visitor and makes the header check decoration.
beforeEach(() => {
  window.sessionStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  preferReducedMotion();
});

afterEach(async () => {
  // Unmount while the fake clock is still installed, then let it run out.
  // jsdom drives requestAnimationFrame from one 60 Hz interval, which is a
  // FAKE setInterval here. Uninstalled with a frame still requested -- D3's,
  // from RiskChart -- that interval is gone for good and jsdom never starts
  // another, so every later count-up (MetricCard) froze at its first value.
  // RiskChart interrupts its transitions on unmount; this lets the frames
  // already requested fire before the clock goes.
  cleanup();
  await vi.advanceTimersByTimeAsync(1000);
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("Dashboard", () => {
  it("asks for the access key before fetching anything", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<Dashboard />);

    expect(screen.getByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
    // Nothing may be requested until a key exists.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the typed key as a header and never persists it beyond the tab", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);

    render(<Dashboard />);
    await userEvent.type(screen.getByLabelText(/Pano Erişim Anahtarı/i), "gizli-anahtar");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, options] = fetchMock.mock.calls[0];
    // Same origin by default (src/apiBase.js): no host compiled into the
    // bundle, so the panel works at whatever address it was opened from.
    expect(url).toBe("/api/sessions");
    expect(options.headers["X-Dashboard-Key"]).toBe("gizli-anahtar");
    // sessionStorage, not localStorage: it dies with the tab.
    expect(window.localStorage.getItem("deepcheck.dashboardKey")).toBeNull();
  });

  it("drops back to the prompt when the key is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
    );

    render(<Dashboard />);
    await userEvent.type(screen.getByLabelText(/Pano Erişim Anahtarı/i), "yanlis");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    // A rejected key must not leave a dead page polling every three seconds.
    expect(await screen.findByText(/Yetkisiz erişim/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Pano Erişim Anahtarı/i)).toBeInTheDocument();
  });

  it("reports an unreachable server in Turkish, never in the browser's own words", async () => {
    // fetch() rejects with a TypeError whose message the BROWSER wrote:
    // "Failed to fetch" in Chrome. Rendering err.message put that English in
    // front of a Turkish-speaking analyst.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<Dashboard />);
    await userEvent.type(screen.getByLabelText(/Pano Erişim Anahtarı/i), "gizli-anahtar");
    await userEvent.click(screen.getByRole("button", { name: /Panoya Gir/i }));

    expect(await screen.findByText(/Sunucuya ulaşılamadı/i)).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/i)).not.toBeInTheDocument();
  });
});

// GET /api/score/{id} always carries this block with these keys; only the
// values change (backend main._profile_block).
const PROFILE_OFF = {
  state: "disabled",
  modality: null,
  reference_n: 0,
  probation_n: 0,
  deviation: null,
  p_value: null,
  p_value_low: null,
  top_features: [],
  escalated: false,
  shadow: false,
};

function openDashboardWith(profile) {
  window.sessionStorage.setItem("deepcheck.dashboardKey", "gizli-anahtar");
  const session = {
    session_id: "oturum-0001-aaaa",
    risk_score: 12.4,
    label: "Gerçek Kullanıcı",
    last_seen_at: "2026-09-16T10:00:00Z",
    response_time_ms: 21,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (url.endsWith("/api/sessions")) return { ok: true, status: 200, json: async () => [session] };
      if (url.endsWith(`/api/score/${session.session_id}`)) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ ...session, confidence: 0.91, history: [], shap_explanation: [], profile }),
        };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    }),
  );
  render(<Dashboard />);
}

async function profileCard() {
  const heading = await screen.findByRole("heading", { name: "Müşteri Profili" });
  return within(heading.closest("section"));
}

describe("Dashboard simulated sessions", () => {
  // backend/demo_seed.py --simulate drives a session of a synthetic identity
  // through the real API; /api/sessions and /api/score mark it is_synthetic.
  const simulated = {
    session_id: "sim-0001-bbbbbb",
    risk_score: 90,
    label: "Bot Tespit Edildi",
    last_seen_at: "2026-09-16T10:01:00Z",
    response_time_ms: 900,
    is_synthetic: true,
  };
  const real = {
    session_id: "oturum-0001-aaaa",
    risk_score: 12.4,
    label: "Gerçek Kullanıcı",
    last_seen_at: "2026-09-16T10:00:00Z",
    response_time_ms: 21,
    is_synthetic: false,
  };

  function openWith(sessions) {
    window.sessionStorage.setItem("deepcheck.dashboardKey", "gizli-anahtar");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (url.endsWith("/api/sessions")) return { ok: true, status: 200, json: async () => sessions };
        const session = sessions.find((s) => url.endsWith(`/api/score/${s.session_id}`));
        if (session) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ ...session, confidence: 0.9, history: [], shap_explanation: [], profile: PROFILE_OFF }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );
    render(<Dashboard />);
  }

  const metric = (label) => screen.getByText(label).nextElementSibling.textContent;

  it("badges a simulated session in the list and in the selected-session card", async () => {
    openWith([simulated, real]);

    const heading = await screen.findByRole("heading", { name: "Seçili Oturum" });
    const selected = within(heading.closest("section"));
    await waitFor(() => expect(selected.getByText("Sentetik demo verisi")).toBeInTheDocument());
    expect(selected.getByText(/Simüle edilmiş oturum — gerçek bir kişi değil/)).toBeInTheDocument();

    // One badge in the list: on the simulated card, not the real one.
    const cards = screen.getAllByRole("button");
    const simCard = cards.find((c) => c.textContent.includes("sim-0001-bbbb"));
    const realCard = cards.find((c) => c.textContent.includes("oturum-0001-a"));
    expect(within(simCard).getByText("Sentetik demo verisi")).toBeInTheDocument();
    expect(within(realCard).queryByText("Sentetik demo verisi")).not.toBeInTheDocument();
  });

  it("keeps simulated sessions out of every metric and says how many were left out", async () => {
    openWith([simulated, real]);

    expect(await screen.findByText(/Metrikler 1 sentetik demo oturumunu/)).toBeInTheDocument();
    // Counted in, the simulated bot would make it 2 sessions, 1 bot, an
    // average risk of 51.2 and an average response time of 460.5 ms.
    await waitFor(() => {
      expect(metric("Toplam Oturum")).toBe("1");
      expect(metric("Tespit Edilen Bot")).toBe("0");
      expect(metric("Ortalama Risk Skoru")).toBe("12.4");
      expect(metric("Ortalama Yanıt Süresi")).toBe("21.0 ms");
    });
  });

  it("counts everything and shows no note when nothing is synthetic", async () => {
    openWith([real]);

    await waitFor(() => expect(metric("Toplam Oturum")).toBe("1"));
    expect(screen.queryByText(/sentetik demo oturumunu/)).not.toBeInTheDocument();
    expect(screen.queryByText("Sentetik demo verisi")).not.toBeInTheDocument();
  });
});

describe("Dashboard customer profile card", () => {
  it("shows a profile being built as n / 19 for the session's input type", async () => {
    // Maturity cannot be reached on stage (19 sessions per input type), so
    // this count is what lets a jury watch a profile being built.
    openDashboardWith({ ...PROFILE_OFF, state: "immature", modality: "mouse", reference_n: 7 });
    const card = await profileCard();

    expect(card.getByText("Profil olgunlaşmadı")).toBeInTheDocument();
    expect(card.getByText("Fare")).toBeInTheDocument();
    expect(card.getByText("Fare: 7 / 19")).toBeInTheDocument();
    expect(card.getByText("Olgunlaşıyor")).toBeInTheDocument();
    const bar = card.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "7");
    expect(bar).toHaveAttribute("aria-valuemax", "19");
    // No comparison below maturity, so nothing to show and nothing escalated.
    expect(card.getByText("Sapma p-değeri").nextElementSibling).toHaveTextContent("—");
    expect(card.queryByText("Ek doğrulama istendi")).not.toBeInTheDocument();
  });

  it("shows the evidence behind an evaluated session and says when it only ran in shadow mode", async () => {
    openDashboardWith({
      ...PROFILE_OFF,
      state: "evaluated",
      modality: "keyboard",
      reference_n: 20,
      deviation: 4.731,
      p_value: 0.0476,
      p_value_low: 0.9524,
      top_features: [
        { feature: "tereddut_skoru", z: 5.24 },
        { feature: "odak_degisimi", z: 3.1 },
        { feature: "zaman_kuantasyonu", z: 2.05 },
      ],
      shadow: true,
    });
    const card = await profileCard();

    expect(card.getByText("Değerlendirildi")).toBeInTheDocument();
    expect(card.getByText("Klavye: 20 / 19")).toBeInTheDocument();
    expect(card.getByText("Olgun")).toBeInTheDocument();
    expect(card.getByText("0.0476")).toBeInTheDocument();
    expect(card.getByText("4.73")).toBeInTheDocument();
    // The deviating features are rendered as visible text -- the backend's own
    // feature name beside its Turkish gloss, and the z value in its own
    // column -- rather than as a bar chart with an sr-only list beside it.
    for (const [feature, z] of [
      ["tereddut_skoru", "z 5.2"],
      ["odak_degisimi", "z 3.1"],
      ["zaman_kuantasyonu", "z 2.0"],
    ]) {
      expect(card.getByText(feature)).toBeInTheDocument();
      expect(card.getByText(z)).toBeInTheDocument();
    }
    expect(card.getByText("Gölge modu — karar etkilenmedi")).toBeInTheDocument();
    // Shadow mode computed a deviation and asked nobody for anything.
    expect(card.queryByText("Ek doğrulama istendi")).not.toBeInTheDocument();
  });

  it("marks a decision the layer turned into a verification", async () => {
    openDashboardWith({
      ...PROFILE_OFF,
      state: "evaluated",
      modality: "mouse",
      reference_n: 19,
      deviation: 6.2,
      p_value: 0.05,
      top_features: [{ feature: "hiz_otokorelasyonu", z: 6.2 }],
      escalated: true,
    });
    const card = await profileCard();

    expect(card.getByText("Ek doğrulama istendi")).toBeInTheDocument();
    expect(card.queryByText("Gölge modu — karar etkilenmedi")).not.toBeInTheDocument();
  });

  it("says when too many decisions named the customer and the profile was not read", async () => {
    // The per-customer decision bucket ran out: the backend read nothing and,
    // enforcing, asked for step-up instead of approving. No count, no p-value:
    // nothing was compared.
    openDashboardWith({ ...PROFILE_OFF, state: "rate_limited", escalated: true });
    const card = await profileCard();

    expect(card.getByText("Bu müşteri için karar sınırı aşıldı — profil okunmadı")).toBeInTheDocument();
    expect(card.getByText("Ek doğrulama istendi")).toBeInTheDocument();
    expect(card.getByText("Sapma p-değeri").nextElementSibling).toHaveTextContent("—");
    expect(card.queryByText(/\/ 19/)).not.toBeInTheDocument();
  });

  it("counts sessions awaiting confirmation apart from the references", async () => {
    // A session rescued by a passed step-up is stored on probation: the
    // statistic did not use it, so it is not one of the 20 and a jury watching
    // the grandchild be challenged again needs to see why.
    openDashboardWith({
      ...PROFILE_OFF,
      state: "evaluated",
      modality: "mouse",
      reference_n: 20,
      probation_n: 1,
      p_value: 0.0476,
      escalated: true,
    });
    const card = await profileCard();

    expect(card.getByText("Fare: 20 / 19")).toBeInTheDocument();
    expect(card.getByText("Onay bekleyen oturum: 1")).toBeInTheDocument();
    expect(card.getByText(/karşılaştırmaya ve olgunluğa katılmaz/)).toBeInTheDocument();
    expect(card.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "19");
  });

  it("says nothing about sessions awaiting confirmation when there are none or the count was never read", async () => {
    openDashboardWith({ ...PROFILE_OFF, state: "evaluated", modality: "mouse", reference_n: 20, probation_n: 0 });
    let card = await profileCard();
    expect(card.queryByText(/Onay bekleyen/)).not.toBeInTheDocument();
    cleanup();
    vi.unstubAllGlobals();
    preferReducedMotion();

    openDashboardWith({ ...PROFILE_OFF, state: "thin_session", modality: "mouse", reference_n: 0, probation_n: 2 });
    card = await profileCard();
    expect(card.queryByText(/Onay bekleyen/)).not.toBeInTheDocument();
  });

  it("does not print a reference count the decision never read", async () => {
    // thin_session stops before the customer's vectors are read, so its
    // reference_n of 0 is "not read" -- "0 / 19" would claim the customer has
    // no history at all.
    openDashboardWith({ ...PROFILE_OFF, state: "thin_session", modality: "mouse", reference_n: 0 });
    const card = await profileCard();

    expect(card.getByText("Oturum verisi yetersiz")).toBeInTheDocument();
    expect(card.queryByText(/\/ 19/)).not.toBeInTheDocument();
    expect(card.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("prints no count for a profile stored under another feature schema or key version", async () => {
    // The backend answers immature without reading a single vector, so the
    // block has no input type and its 0 is "not compared", not "no history".
    openDashboardWith({ ...PROFILE_OFF, state: "immature", modality: null, reference_n: 0 });
    const card = await profileCard();

    expect(card.getByText("Profil olgunlaşmadı")).toBeInTheDocument();
    expect(card.getByText("Giriş türü").nextElementSibling).toHaveTextContent("—");
    expect(card.queryByText(/\/ 19/)).not.toBeInTheDocument();
    expect(card.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("does not call a profile mature when its calibration set was too small", async () => {
    // 20 stored sessions, but too few of them could be scored on enough
    // features for the conformal rank, so the layer abstained as immature.
    openDashboardWith({ ...PROFILE_OFF, state: "immature", modality: "mouse", reference_n: 20 });
    const card = await profileCard();

    expect(card.getByText("Fare: 20 / 19")).toBeInTheDocument();
    expect(card.getByText("Karşılaştırılabilir referans yetersiz")).toBeInTheDocument();
    expect(card.queryByText("Olgun")).not.toBeInTheDocument();
  });

  it("shows the card in the same place when the layer is off", async () => {
    // The block's shape never depends on whether the customer is profiled,
    // so neither does the card.
    openDashboardWith(PROFILE_OFF);
    const card = await profileCard();

    expect(card.getByText("Profilleme kapalı")).toBeInTheDocument();
    expect(card.queryByRole("progressbar")).not.toBeInTheDocument();
    // With the layer off nothing is read, so "read from the last decision"
    // would be untrue.
    expect(card.queryByText(/son ödeme kararından okunur/)).not.toBeInTheDocument();
  });

  it("says a session with no customer decision yet reads as no profile", async () => {
    // The block comes from the newest decision audit row with a profile
    // opinion. Watched before Onayla is pressed, a mature customer's session
    // still says "Profil yok"; the card must not leave that unexplained.
    openDashboardWith({ ...PROFILE_OFF, state: "no_profile" });
    const card = await profileCard();

    expect(card.getByText("Profil yok")).toBeInTheDocument();
    expect(card.getByText(/son ödeme kararından okunur/)).toHaveTextContent(/“Profil yok” görünür/);
    expect(card.queryByText(/\/ 19/)).not.toBeInTheDocument();
  });

  it("labels a decision made against a synthetic demo customer, with its maturity and deviating features", async () => {
    // The jury demo: a real person pays as a seeded synthetic customer. The
    // card must say, in the badge and in words, that the history compared
    // against was simulated -- and still show what the jury came to see.
    openDashboardWith({
      ...PROFILE_OFF,
      state: "evaluated",
      modality: "mouse",
      reference_n: 20,
      deviation: 5.9,
      p_value: 0.0476,
      top_features: [
        { feature: "hiz_otokorelasyonu", z: 6.4 },
        { feature: "tereddut_skoru", z: 4.1 },
        { feature: "yon_tutarliligi", z: 3.3 },
      ],
      escalated: true,
      synthetic: true,
    });
    const card = await profileCard();

    expect(card.getByText("Sentetik demo verisi")).toBeInTheDocument();
    expect(card.getByText(/sentetik demo verisi katıldı/)).toHaveTextContent(/gerçek bir kişiye ait değil/);
    expect(card.getByText(/mekanizmayı gösterir, gerçek kişilerdeki doğruluğu değil/)).toBeInTheDocument();
    expect(card.getByText("Fare: 20 / 19")).toBeInTheDocument();
    expect(card.getByText("Olgun")).toBeInTheDocument();
    expect(card.getByText("hiz_otokorelasyonu")).toBeInTheDocument();
    expect(card.getByText("z 6.4")).toBeInTheDocument();
    expect(card.getByText("Ek doğrulama istendi")).toBeInTheDocument();
  });

  it("says nothing synthetic about a real profile, or when the server does not send the flag", async () => {
    openDashboardWith({ ...PROFILE_OFF, state: "evaluated", modality: "mouse", reference_n: 20, synthetic: false });
    let card = await profileCard();
    expect(card.queryByText(/[Ss]entetik demo/)).not.toBeInTheDocument();
    cleanup();
    vi.unstubAllGlobals();
    preferReducedMotion();

    // A backend without the field: no badge rather than a guess.
    openDashboardWith({ ...PROFILE_OFF, state: "evaluated", modality: "mouse", reference_n: 20 });
    card = await profileCard();
    expect(card.queryByText(/[Ss]entetik demo/)).not.toBeInTheDocument();
    expect(screen.queryByText("Sentetik demo verisi")).not.toBeInTheDocument();
  });

  it("has a Turkish label for every state and input type and never shows the raw key", () => {
    for (const [state, label] of Object.entries(PROFILE_STATE_LABELS)) {
      for (const [modality, modalityLabel] of Object.entries(MODALITY_LABELS)) {
        render(<ProfilePanel profile={{ ...PROFILE_OFF, state, modality, reference_n: 19 }} />);
        expect(screen.getByText(label)).toBeInTheDocument();
        expect(screen.getByText(modalityLabel)).toBeInTheDocument();
        expect(screen.queryByText("Bilinmeyen durum")).not.toBeInTheDocument();
        expect(document.body.textContent).not.toContain(state);
        expect(document.body.textContent).not.toContain(modality);
        cleanup();
      }
    }
  });
});

// The live jury demo: the dashboard runs on the presenter's laptop while a
// teammate pays from a second laptop and a bot then attacks the same page. The
// panel has to find each new session on its own, say what it does and does not
// know yet, and show what the server actually did.
describe("Dashboard live follow", () => {
  const POLL_MS = 3000;
  const at = (minute, second = 0) => new Date(Date.UTC(2026, 8, 16, 10, minute, second)).toISOString();

  // Whether a session is "Canlı" and which ones the presenter view hides both
  // read this browser's clock against the server's stamps, so the clock is set
  // to the stage below: two seconds after the bot's last window, two minutes
  // after the human's. It still moves forward (shouldAdvanceTime) and by
  // 3 s on every poll().
  beforeEach(() => {
    vi.setSystemTime(new Date(at(7, 6)));
  });

  // Cards print session_id.slice(0, 13) and the selected card the whole id,
  // so the first 13 characters are distinct.
  const old = {
    session_id: "eski-0000-zzzzzz",
    risk_score: 91.0,
    label: "Bot Tespit Edildi",
    created_at: at(-5),
    last_seen_at: at(6),
    response_time_ms: 30,
  };
  const human = {
    session_id: "insan-0001-aaaaa",
    risk_score: 12.4,
    label: "Gerçek Kullanıcı",
    created_at: at(0),
    last_seen_at: at(5),
    response_time_ms: 20,
  };
  const bot = {
    session_id: "bot-0002-bbbbbbb",
    risk_score: 93.4,
    label: "Bot Tespit Edildi",
    created_at: at(7),
    last_seen_at: at(7, 4),
    response_time_ms: 25,
  };

  // Stored windows as GET /api/score sends them; `observed` per window when
  // given, absent (an older backend) when not.
  function windows(count, observed) {
    return Array.from({ length: count }, (_, i) => ({
      timestamp: at(7, i * 2),
      risk_score: 40,
      ...(observed ? { observed: observed[i] } : {}),
    }));
  }

  // A backend whose answers a test can change between polls.
  function liveBackend(initial) {
    const server = { sessions: initial, details: {} };
    const fetchMock = vi.fn(async (url) => {
      if (url.endsWith("/api/sessions")) {
        const rows = server.sessions.map((s) => ({ ...s }));
        return { ok: true, status: 200, json: async () => rows };
      }
      const session = server.sessions.find((s) => url.endsWith(`/api/score/${s.session_id}`));
      if (session) {
        const body = {
          ...session,
          confidence: 0.9,
          shap_explanation: [],
          profile: PROFILE_OFF,
          history: windows(5),
          ...server.details[session.session_id],
        };
        return { ok: true, status: 200, json: async () => body };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    window.sessionStorage.setItem("deepcheck.dashboardKey", "gizli-anahtar");
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard />);
    return { server, fetchMock };
  }

  const poll = () => act(() => vi.advanceTimersByTimeAsync(POLL_MS));
  const followButton = () => screen.getByRole("button", { name: /Canlı takip/ });
  const selectedCard = () => within(screen.getByRole("heading", { name: "Seçili Oturum" }).closest("section"));
  const card = (session) =>
    screen.queryAllByRole("button").find((c) => c.textContent.includes(session.session_id.slice(0, 13)));
  const scoreFetched = (fetchMock, session) =>
    fetchMock.mock.calls.some(([url]) => url.endsWith(`/api/score/${session.session_id}`));
  const sessionPolls = (fetchMock) => fetchMock.mock.calls.filter(([url]) => url.endsWith("/api/sessions")).length;
  // A metric card's figure, and the hint line(s) beneath it.
  const metric = (label) => screen.getByText(label).nextElementSibling.textContent;
  const metricHint = (label) => screen.getByText(label).parentElement.lastElementChild.textContent;
  const resetButton = () => screen.getByRole("button", { name: "Görünümü sıfırla" });
  const storedCutoff = () => Number(window.sessionStorage.getItem("deepcheck.dashboardCutoff"));

  async function expectSelected(session) {
    await waitFor(() => expect(selectedCard().getByText(session.session_id)).toBeInTheDocument());
    expect(card(session)).toHaveAttribute("aria-pressed", "true");
  }

  it("follows the session that started last and moves when a newer one starts", async () => {
    // `old` was seen last, but `human` STARTED last: follow goes by start.
    const { server, fetchMock } = liveBackend([old, human]);
    await expectSelected(human);
    expect(followButton()).toHaveAttribute("aria-pressed", "true");
    expect(followButton()).toHaveTextContent("Canlı takip: Açık");

    server.sessions = [bot, old, human];
    await poll();
    await expectSelected(bot);
    expect(scoreFetched(fetchMock, bot)).toBe(true);
    expect(within(card(bot)).getByText("Canlı")).toBeInTheDocument();
  });

  it("stops following on a click, stays put for a newer session, and jumps at once when turned back on", async () => {
    const { server, fetchMock } = liveBackend([old, human]);
    await expectSelected(human);

    fireEvent.click(card(old));
    await expectSelected(old);
    expect(followButton()).toHaveAttribute("aria-pressed", "false");
    expect(followButton()).toHaveTextContent("Canlı takip: Kapalı");

    server.sessions = [bot, old, human];
    await poll();
    await waitFor(() => expect(card(bot)).toBeDefined());
    expect(selectedCard().getByText(old.session_id)).toBeInTheDocument();
    expect(scoreFetched(fetchMock, bot)).toBe(false);

    // Back on: the live session is shown now, not at the next poll.
    const polls = sessionPolls(fetchMock);
    fireEvent.click(followButton());
    expect(followButton()).toHaveAttribute("aria-pressed", "true");
    await expectSelected(bot);
    expect(sessionPolls(fetchMock)).toBe(polls);
  });

  it("does not bounce between two sessions that take turns being the most recently seen", async () => {
    // The teammate's tab keeps posting while the bot runs. /api/sessions
    // orders by last_seen_at, so the two swap places on every poll; the
    // selection must stay on the one that started last.
    const { server, fetchMock } = liveBackend([human, { ...bot, last_seen_at: at(7, 1) }]);
    await expectSelected(bot);
    for (let i = 0; i < 4; i += 1) {
      const humanAhead = i % 2 === 0;
      server.sessions = humanAhead
        ? [{ ...human, last_seen_at: at(8, i * 3 + 2) }, { ...bot, last_seen_at: at(8, i * 3 + 1) }]
        : [{ ...bot, last_seen_at: at(8, i * 3 + 2) }, { ...human, last_seen_at: at(8, i * 3 + 1) }];
      await poll();
      expect(selectedCard().getByText(bot.session_id)).toBeInTheDocument();
    }
    expect(scoreFetched(fetchMock, human)).toBe(false);
  });

  it("re-polls the selected session, so its window count grows without a click", async () => {
    const { server } = liveBackend([human]);
    server.details[human.session_id] = { history: windows(2) };
    expect(await screen.findByText("2 pencere")).toBeInTheDocument();

    server.details[human.session_id] = { history: windows(3) };
    await poll();
    expect(await screen.findByText("3 pencere")).toBeInTheDocument();
  });

  it("marks only sessions that appeared after the page opened as new, and only for a few seconds", async () => {
    const { server } = liveBackend([old, human]);
    await expectSelected(human);
    // The backlog the page opened onto is not news.
    expect(screen.queryByText("Yeni")).not.toBeInTheDocument();

    server.sessions = [bot, old, human];
    await poll();
    await waitFor(() => expect(within(card(bot)).getByText("Yeni")).toBeInTheDocument());
    expect(within(card(old)).queryByText("Yeni")).not.toBeInTheDocument();
    expect(within(card(human)).queryByText("Yeni")).not.toBeInTheDocument();
    // A span, not a control: still one button per session card.
    expect(within(card(bot)).queryAllByRole("button")).toHaveLength(0);

    for (let i = 0; i < 4; i += 1) await poll();
    expect(screen.queryByText("Yeni")).not.toBeInTheDocument();
  });

  it("announces a switch to a new session once, in Turkish, and not on every poll", async () => {
    const { server } = liveBackend([old, human]);
    await expectSelected(human);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    // Opening the page is not a switch.
    expect(region).toBeEmptyDOMElement();

    server.sessions = [bot, old, human];
    await poll();
    const said = "Canlı takip yeni oturuma geçti: bot-0002. Bot Tespit Edildi, risk skoru 93.4.";
    await waitFor(() => expect(region).toHaveTextContent(said));

    // The followed session's score moves; the announcement does not repeat.
    server.sessions = [{ ...bot, risk_score: 97.1 }, old, human];
    await poll();
    await poll();
    await waitFor(() => expect(selectedCard().getByText("97.1")).toBeInTheDocument());
    expect(region).toHaveTextContent(said);
  });

  it("calls a session the server cannot decide on yet 'Değerlendiriliyor', not by its band", async () => {
    // Three stored windows, one of them observed: the server's gate holds the
    // decision at verify until three are (main._decide_on_evidence).
    const { server } = liveBackend([bot]);
    server.details[bot.session_id] = { history: windows(3, [false, true, false]) };
    await waitFor(() => expect(selectedCard().getByText("Değerlendiriliyor")).toBeInTheDocument());
    expect(selectedCard().getByText("Ön skor")).toBeInTheDocument();
    expect(selectedCard().getByText(/Karar için en az 3 gözlenen pencere gerekir \(1\/3\)/)).toBeInTheDocument();
    expect(selectedCard().queryByText("Bot Tespit Edildi")).not.toBeInTheDocument();
    expect(selectedCard().queryByText("Oturum engellenir")).not.toBeInTheDocument();
    // The list files it apart from the bands too.
    const undecided = screen.getByRole("region", { name: "Değerlendiriliyor (1)" });
    expect(within(undecided).getByText("Ön skor")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /Bot Tespit Edildi/ })).not.toBeInTheDocument();

    // A third observed window: now it is decidable and shown in its band.
    server.details[bot.session_id] = { history: windows(4, [false, true, true, true]) };
    await poll();
    await waitFor(() => expect(selectedCard().getByText("Bot Tespit Edildi")).toBeInTheDocument());
    expect(selectedCard().queryByText("Değerlendiriliyor")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Bot Tespit Edildi (1)" })).toBeInTheDocument();
  });

  it("falls back to the number of windows when the backend sends no per-window verdict", async () => {
    const { server } = liveBackend([bot]);
    server.details[bot.session_id] = { history: windows(2) };
    await waitFor(() =>
      expect(selectedCard().getByText(/Karar için en az 3 gözlenen pencere gerekir \(2\/3\)/)).toBeInTheDocument(),
    );
  });

  it("leaves a session the server cannot decide on yet out of the bot count and both averages, and says so", async () => {
    // The teammate's opening window landed in the block band -- the earlier
    // hand-filled run stored 99.1 first. Live follow is on it, and the
    // selected card says "Değerlendiriliyor"; the cards above must not call it
    // a detected bot at the same moment.
    const opening = { ...bot, session_id: "acilis-0006-fffff", risk_score: 99.1, response_time_ms: 25 };
    const { server } = liveBackend([human, opening]);
    server.details[opening.session_id] = { history: windows(1, [false]) };
    await expectSelected(opening);
    await waitFor(() => expect(selectedCard().getByText("Değerlendiriliyor")).toBeInTheDocument());

    // Counted in, it would be 1 bot, 55.8 and 22.5 ms.
    expect(metric("Toplam Oturum")).toBe("2");
    expect(metric("Tespit Edilen Bot")).toBe("0");
    expect(metric("Ortalama Risk Skoru")).toBe("12.4");
    expect(metric("Ortalama Yanıt Süresi")).toBe("20.0 ms");
    // Each card that leaves it out says so under its own hint; the total,
    // which still counts it, does not.
    expect(metricHint("Tespit Edilen Bot")).toBe("Etiketi 80-100 bandında olan oturum1 oturum değerlendiriliyor, sayılmadı");
    expect(metricHint("Ortalama Risk Skoru")).toBe("Oturum başına yumuşatılmış skor1 oturum değerlendiriliyor, sayılmadı");
    expect(metricHint("Ortalama Yanıt Süresi")).toBe(
      "Sunucunun ölçtüğü skorlama süresi1 oturum değerlendiriliyor, sayılmadı",
    );
    expect(metricHint("Toplam Oturum")).toBe("Sunucunun döndürdüğü son oturumlar");

    // A third observed window: decided, so counted by its label like any other.
    server.details[opening.session_id] = { history: windows(3, [true, true, true]) };
    await poll();
    await waitFor(() => expect(metric("Tespit Edilen Bot")).toBe("1"));
    expect(metric("Ortalama Risk Skoru")).toBe("55.8");
    expect(metric("Ortalama Yanıt Süresi")).toBe("22.5 ms");
    expect(screen.queryByText(/değerlendiriliyor, sayılmadı/)).not.toBeInTheDocument();
    // The four labels are the same in both states.
    for (const label of ["Toplam Oturum", "Ortalama Risk Skoru", "Tespit Edilen Bot", "Ortalama Yanıt Süresi"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("shows no average while every listed session is still undecided, instead of a 0.0 nobody measured", async () => {
    // The human act's opening seconds after "Görünümü sıfırla": the teammate's
    // session is the only one, and the server would not decide on it yet.
    const opening = { ...bot, session_id: "acilis-0007-ggggg", risk_score: 99.1, response_time_ms: 25 };
    const { server } = liveBackend([opening]);
    server.details[opening.session_id] = { history: windows(1, [false]) };
    await expectSelected(opening);
    await waitFor(() => expect(selectedCard().getByText("Değerlendiriliyor")).toBeInTheDocument());

    expect(metric("Toplam Oturum")).toBe("1");
    expect(metric("Tespit Edilen Bot")).toBe("0");
    expect(metric("Ortalama Risk Skoru")).toBe("—");
    expect(metric("Ortalama Yanıt Süresi")).toBe("—");
    expect(metricHint("Ortalama Risk Skoru")).toBe("Oturum başına yumuşatılmış skor1 oturum değerlendiriliyor, sayılmadı");

    // Decided: the figures come back.
    server.details[opening.session_id] = { history: windows(3, [true, true, true]) };
    await poll();
    await waitFor(() => expect(metric("Ortalama Risk Skoru")).toBe("99.1"));
    expect(metric("Ortalama Yanıt Süresi")).toBe("25.0 ms");
  });

  it("files a session live follow just moved to as undecided until its windows arrive", async () => {
    // The first poll that lists a session comes seconds after its first
    // window. Drawn in its stored band for the length of the detail fetch, the
    // bot's card was red and counted before it turned into "Değerlendiriliyor".
    const { server, fetchMock } = liveBackend([human]);
    await expectSelected(human);
    const respond = fetchMock.getMockImplementation();
    let release = null;
    fetchMock.mockImplementation((url) => {
      if (!url.endsWith(`/api/score/${bot.session_id}`)) return respond(url);
      return new Promise((resolve) => {
        release = () => resolve(respond(url));
      });
    });

    server.sessions = [bot, human];
    await poll();
    await waitFor(() => expect(card(bot)).toHaveAttribute("aria-pressed", "true"));
    expect(release).not.toBeNull();
    const undecided = screen.getByRole("region", { name: "Değerlendiriliyor (1)" });
    expect(within(undecided).getByRole("button")).toHaveTextContent(bot.session_id.slice(0, 13));
    expect(screen.queryByRole("region", { name: /Bot Tespit Edildi/ })).not.toBeInTheDocument();
    expect(metric("Tespit Edilen Bot")).toBe("0");
    expect(metric("Ortalama Risk Skoru")).toBe("12.4");
    expect(metricHint("Tespit Edilen Bot")).toContain("1 oturum değerlendiriliyor, sayılmadı");

    // The windows arrive and three were observed: now it is in its band.
    server.details[bot.session_id] = { history: windows(3, [true, true, true]) };
    await act(async () => release());
    await waitFor(() => expect(screen.getByRole("region", { name: "Bot Tespit Edildi (1)" })).toBeInTheDocument());
    expect(screen.queryByRole("region", { name: /Değerlendiriliyor/ })).not.toBeInTheDocument();
    expect(metric("Tespit Edilen Bot")).toBe("1");
  });

  it("does not file a clicked session as undecided while its detail loads", async () => {
    // Only live follow's move to a just-listed session is treated so; a card
    // the presenter clicks stays in its band instead of jumping groups.
    const { fetchMock } = liveBackend([old, human]);
    await expectSelected(human);
    const respond = fetchMock.getMockImplementation();
    let release = null;
    fetchMock.mockImplementation((url) => {
      if (!url.endsWith(`/api/score/${old.session_id}`)) return respond(url);
      return new Promise((resolve) => {
        release = () => resolve(respond(url));
      });
    });

    fireEvent.click(card(old));
    expect(card(old)).toHaveAttribute("aria-pressed", "true");
    expect(release).not.toBeNull();
    expect(screen.getByRole("region", { name: "Bot Tespit Edildi (1)" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /Değerlendiriliyor/ })).not.toBeInTheDocument();
    await act(async () => release());
    await expectSelected(old);
  });

  it("calls the followed card 'Canlı' only while its session is active, as the selected-session badge does", async () => {
    // The bot's last window came 2 s before the clock below.
    liveBackend([bot]);
    await expectSelected(bot);
    expect(within(card(bot)).getByText("Canlı")).toBeInTheDocument();
    expect(selectedCard().getByText(/^Canlı — son etkinlik/)).toBeInTheDocument();

    // Nothing new arrives. Fifteen seconds of polls later the session has gone
    // quiet: the card and the badge beside it say so together, and live follow
    // is still on.
    for (let i = 0; i < 5; i += 1) await poll();
    await waitFor(() => expect(within(card(bot)).getByText("Takipte")).toBeInTheDocument());
    expect(within(card(bot)).queryByText("Canlı")).not.toBeInTheDocument();
    expect(selectedCard().getByText(/^Etkinlik yok — son görülme/)).toBeInTheDocument();
    expect(selectedCard().queryByText(/^Canlı — son etkinlik/)).not.toBeInTheDocument();
    expect(followButton()).toHaveTextContent("Canlı takip: Açık");
  });

  it("puts the Turkish ablative after the cutoff time as the time is read aloud", () => {
    // Local-time constructor: the banner prints the cutoff in local time.
    const clock = (hours, minutes) => sinceClock(new Date(2026, 8, 16, hours, minutes).getTime());
    expect(clock(14, 5)).toBe("14:05’ten"); // beş
    expect(clock(14, 10)).toBe("14:10’dan"); // on
    expect(clock(14, 20)).toBe("14:20’den"); // yirmi
    expect(clock(14, 30)).toBe("14:30’dan"); // otuz
    expect(clock(14, 40)).toBe("14:40’tan"); // kırk
    expect(clock(14, 50)).toBe("14:50’den"); // elli
    expect(clock(14, 3)).toBe("14:03’ten"); // üç
    expect(clock(14, 6)).toBe("14:06’dan"); // altı
    expect(clock(14, 9)).toBe("14:09’dan"); // dokuz
    expect(clock(14, 1)).toBe("14:01’den"); // bir
    // On the hour the hour is the last word read.
    expect(clock(14, 0)).toBe("14:00’ten"); // on dört
    expect(clock(10, 0)).toBe("10:00’dan"); // on
    expect(clock(20, 0)).toBe("20:00’den"); // yirmi
    expect(clock(0, 0)).toBe("00:00’dan"); // sıfır
  });

  it("hides sessions that started before the presenter reset the view, from the list and every metric", async () => {
    // A fixed clock after both rehearsal runs: `old` ran from 09:55 to 10:06,
    // `human` from 10:00 to 10:05, and the presenter resets at 10:30. The
    // figures need no waiting on the wall clock (preferReducedMotion).
    vi.setSystemTime(new Date(at(30)));
    const rehearsalBot = old;
    const rehearsalHuman = human;
    const { server } = liveBackend([rehearsalBot, rehearsalHuman]);
    // The selected session's detail is in before anything is clicked, so no
    // answer lands outside act() halfway through the steps below.
    await expectSelected(rehearsalHuman);
    await waitFor(() => expect(metric("Toplam Oturum")).toBe("2"), { timeout: 2000 });

    fireEvent.click(resetButton());
    const cutoff = storedCutoff();
    expect(cutoff).toBeGreaterThanOrEqual(Date.parse(at(30)));
    expect(
      screen.getByText(`Sunum görünümü: ${sinceClock(cutoff)} beri — 2 eski oturum gizlendi (veriler silinmedi)`),
    ).toBeInTheDocument();
    expect(screen.getByText("Bu görünümde henüz oturum yok")).toBeInTheDocument();
    expect(metric("Toplam Oturum")).toBe("0");
    expect(metric("Tespit Edilen Bot")).toBe("0");
    expect(metric("Ortalama Risk Skoru")).toBe("0.0");
    expect(metric("Ortalama Yanıt Süresi")).toBe("0.0 ms");
    expect(metricHint("Toplam Oturum")).toBe(`${sinceClock(cutoff)} beri başlayan ya da yeniden etkinleşen oturumlar`);

    // The teammate starts paying after the reset.
    const live = {
      ...human,
      session_id: "canli-0003-ccccc",
      risk_score: 33.3,
      response_time_ms: 45,
      created_at: new Date(cutoff + 1000).toISOString(),
      last_seen_at: new Date(cutoff + 2000).toISOString(),
    };
    server.sessions = [live, rehearsalBot, rehearsalHuman];
    await poll();
    await expectSelected(live);
    expect(metric("Toplam Oturum")).toBe("1");
    expect(metric("Tespit Edilen Bot")).toBe("0");
    expect(metric("Ortalama Risk Skoru")).toBe("33.3");
    expect(metric("Ortalama Yanıt Süresi")).toBe("45.0 ms");
    expect(card(rehearsalBot)).toBeUndefined();
    expect(card(rehearsalHuman)).toBeUndefined();
    expect(screen.getByText(/— 2 eski oturum gizlendi \(veriler silinmedi\)/)).toBeInTheDocument();

    // A second reset moves the line to now: all three are behind it, and the
    // live session's last window came before it.
    fireEvent.click(resetButton());
    expect(screen.getByText(/— 3 eski oturum gizlendi \(veriler silinmedi\)/)).toBeInTheDocument();
    expect(metric("Toplam Oturum")).toBe("0");

    fireEvent.click(screen.getByRole("button", { name: "Tümünü göster" }));
    expect(screen.queryByText(/Sunum görünümü/)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("deepcheck.dashboardCutoff")).toBeNull();
    expect(metric("Toplam Oturum")).toBe("3");
  });

  it("shows a session that started before the reset once it is active again after it, and still follows the newest start", async () => {
    // A rehearsal /demo tab left open: the SDK keeps one session per page
    // load, so filling the form in it again on stage adds windows to a
    // session whose created_at is from before the reset.
    vi.setSystemTime(new Date(at(30)));
    const rehearsal = { ...human, session_id: "prova-0004-ddddd", created_at: at(20), last_seen_at: at(21) };
    const { server } = liveBackend([rehearsal]);
    await expectSelected(rehearsal);

    fireEvent.click(resetButton());
    const after = (ms) => new Date(storedCutoff() + ms).toISOString();
    expect(screen.getByText("Bu görünümde henüz oturum yok")).toBeInTheDocument();

    // Windows re-sent up to 10 s after input that came before the reset are
    // the SDK's tail, not new input: still hidden, and not followed.
    server.sessions = [{ ...rehearsal, last_seen_at: after(10_000) }];
    await poll();
    expect(screen.getByText("Bu görünümde henüz oturum yok")).toBeInTheDocument();
    expect(screen.getByText(/— 1 eski oturum gizlendi/)).toBeInTheDocument();

    // The same tab used again: seen well past that tail, so shown and followed.
    server.sessions = [{ ...rehearsal, last_seen_at: after(20_000) }];
    await poll();
    await expectSelected(rehearsal);
    expect(screen.getByText(/— 0 eski oturum gizlendi/)).toBeInTheDocument();
    expect(metric("Toplam Oturum")).toBe("1");

    // A session that STARTS after the reset is followed ahead of it, even
    // though the resumed one was seen more recently; both stay listed.
    const fresh = { ...bot, session_id: "taze-0005-eeeee", created_at: after(21_000), last_seen_at: after(22_000) };
    server.sessions = [{ ...rehearsal, last_seen_at: after(26_000) }, fresh];
    await poll();
    await expectSelected(fresh);
    expect(card(rehearsal)).toBeDefined();
    expect(metric("Toplam Oturum")).toBe("2");
  });

  it("shows the server's last decision with its internal reason, and says when nothing was recorded", async () => {
    const { server } = liveBackend([bot]);
    server.details[bot.session_id] = {
      last_decision: { action: "block", reason: "score", public_reason: "score", decided_at: at(7, 5), risk_score: 93.4 },
    };
    await waitFor(() => expect(selectedCard().getByText("Engellendi")).toBeInTheDocument());
    // The newest RECORDED decision: a later allow naming no customer writes no
    // row (main._learn_and_audit), so "Son karar" would claim more.
    expect(selectedCard().getByText("Son kaydedilen karar")).toBeInTheDocument();
    expect(selectedCard().queryByText("Son karar")).not.toBeInTheDocument();
    expect(selectedCard().getByText("Yumuşatılmış risk skoru, 40/60/80 eşik merdivenine göre")).toBeInTheDocument();
    expect(selectedCard().getByText("karar anındaki skor 93.4")).toBeInTheDocument();
    // Nothing was collapsed for the client, so there is nothing to explain.
    expect(selectedCard().queryByText(/Ödeme sayfasına giden gerekçe/)).not.toBeInTheDocument();

    // A collapsed reason: the analyst sees the real one and what the page was told.
    server.details[bot.session_id] = {
      last_decision: { action: "verify", reason: "sequential", public_reason: "step_up", decided_at: at(7, 9), risk_score: 31.0 },
    };
    await poll();
    await waitFor(() => expect(selectedCard().getByText("Ek doğrulama istendi")).toBeInTheDocument());
    expect(selectedCard().getByText("Oturum boyunca biriken davranış kanıtı otomasyona işaret ediyor")).toBeInTheDocument();
    expect(selectedCard().getByText(/Ödeme sayfasına giden gerekçe: “Ek doğrulama gerekiyor”/)).toBeInTheDocument();

    server.details[bot.session_id] = { last_decision: null };
    await poll();
    await waitFor(() =>
      expect(selectedCard().getByText("Karar kaydı yok — kayıt yalnızca profil katmanı açıkken tutulur")).toBeInTheDocument(),
    );
    expect(selectedCard().queryByText("Engellendi")).not.toBeInTheDocument();
    expect(selectedCard().getByText("Son kaydedilen karar")).toBeInTheDocument();
    // "No record" is not "no decision": with the layer on, an approval naming
    // no customer is not written either.
    expect(selectedCard().getByText(/müşteri referansı taşımayan bir “Onaylandı” kararı kaydedilmez/)).toBeInTheDocument();
  });

  it("does not call a verify given for too little, inconclusive or old behaviour a step-up", async () => {
    // Onayla pressed early: two of five windows observed. The server records
    // verify/unobserved and tells the page insufficient_evidence, which
    // Demo.jsx answers with a "few more seconds" hint, not a code box.
    const { server } = liveBackend([bot]);
    server.details[bot.session_id] = {
      history: windows(5, [false, false, true, false, true]),
      last_decision: {
        action: "verify",
        reason: "unobserved",
        public_reason: "insufficient_evidence",
        decided_at: at(7, 5),
        risk_score: 99.1,
      },
    };
    await waitFor(() => expect(selectedCard().getByText("Karar ertelendi")).toBeInTheDocument());
    expect(selectedCard().queryByText("Ek doğrulama istendi")).not.toBeInTheDocument();
    expect(selectedCard().getByText(/^Gözlenen davranış kanıtı yetersiz/)).toBeInTheDocument();
    expect(
      // The page's own sentence, not the analyst label: "unobserved" is told as
      // insufficient_evidence, and that label's two causes did not apply here.
      selectedCard().getByText(/Ödeme sayfasına giden gerekçe: “Karar için yeterli davranış verisi yok, lütfen birkaç saniye/),
    ).toBeInTheDocument();
    expect(selectedCard().getByText(/bu bir davranış tespiti\s+değil/)).toBeInTheDocument();

    // Six observed windows and a sequential test still inconclusive: the
    // server's other insufficient_evidence branch. The words cover both, so
    // the panel does not say windows are missing beside "6 pencere".
    server.details[bot.session_id] = {
      history: windows(6, [true, true, true, true, true, true]),
      last_decision: {
        action: "verify",
        reason: "insufficient_evidence",
        public_reason: "insufficient_evidence",
        decided_at: at(7, 9),
        risk_score: 52.0,
      },
    };
    await poll();
    await waitFor(() =>
      expect(
        selectedCard().getByText(
          "Davranış kanıtı henüz karar için yeterli değil (kayıtlı pencere az ya da ardışık test henüz sonuçsuz)",
        ),
      ).toBeInTheDocument(),
    );
    expect(selectedCard().getByText("Karar ertelendi")).toBeInTheDocument();
    expect(screen.getByText("6 pencere")).toBeInTheDocument();

    server.details[bot.session_id] = {
      last_decision: { action: "verify", reason: "stale", public_reason: "stale", decided_at: at(7, 9), risk_score: 93.4 },
    };
    await poll();
    await waitFor(() => expect(selectedCard().getByText("Oturumun davranış verisi güncel değil")).toBeInTheDocument());
    expect(selectedCard().getByText("Karar ertelendi")).toBeInTheDocument();
    expect(selectedCard().queryByText("Ek doğrulama istendi")).not.toBeInTheDocument();

    // A verify the evidence asked for is still a step-up, in its band's colour.
    server.details[bot.session_id] = {
      last_decision: { action: "verify", reason: "score", public_reason: "score", decided_at: at(7, 9), risk_score: 72.5 },
    };
    await poll();
    await waitFor(() => expect(selectedCard().getByText("Ek doğrulama istendi")).toBeInTheDocument());
    expect(selectedCard().queryByText("Karar ertelendi")).not.toBeInTheDocument();
    expect(selectedCard().queryByText(/bu bir davranış tespiti/)).not.toBeInTheDocument();
  });

  it("keeps a failing detail fetch inside the selected-session card instead of flashing the page alert", async () => {
    const { fetchMock } = liveBackend([human]);
    fetchMock.mockImplementation(async (url) => {
      if (url.endsWith("/api/sessions")) return { ok: true, status: 200, json: async () => [human] };
      return { ok: false, status: 500, json: async () => ({}) };
    });
    await waitFor(() => expect(selectedCard().getByText("Seçili oturum güncellenemedi")).toBeInTheDocument());
    for (let i = 0; i < 2; i += 1) {
      await poll();
      expect(selectedCard().getByText("Seçili oturum güncellenemedi")).toBeInTheDocument();
      expect(screen.queryByText("Pano verisi alınamadı")).not.toBeInTheDocument();
    }
  });

  it("does not start a second list request while the previous one is still waiting", async () => {
    const { fetchMock } = liveBackend([human]);
    await expectSelected(human);
    let release;
    fetchMock.mockImplementation(async (url) => {
      if (url.endsWith("/api/sessions")) {
        return new Promise((resolve) => {
          release = () => resolve({ ok: true, status: 200, json: async () => [human] });
        });
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ ...human, confidence: 0.9, shap_explanation: [], profile: PROFILE_OFF, history: [] }),
      };
    });
    const before = sessionPolls(fetchMock);
    await poll();
    await poll();
    await poll();
    // One request went out and is still pending; the ticks behind it waited.
    expect(sessionPolls(fetchMock)).toBe(before + 1);
    await act(async () => release());
    await poll();
    expect(sessionPolls(fetchMock)).toBe(before + 2);
  });
});
