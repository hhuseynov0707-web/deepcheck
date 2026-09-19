import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProfilePanel, { MODALITY_LABELS, PROFILE_STATE_LABELS } from "../components/ProfilePanel.jsx";
import Dashboard from "./Dashboard.jsx";

// The SOC endpoints expose every customer's live session, so the dashboard
// asks the analyst for the key rather than shipping one in the bundle. These
// tests pin that down: a key compiled into the bundle is served to every
// visitor and makes the header check decoration.
beforeEach(() => {
  window.sessionStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
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
    const [, options] = fetchMock.mock.calls[0];
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
    expect(card.getByText("tereddut_skoru: z 5.2")).toBeInTheDocument();
    expect(card.getByText("odak_degisimi: z 3.1")).toBeInTheDocument();
    expect(card.getByText("zaman_kuantasyonu: z 2.0")).toBeInTheDocument();
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
