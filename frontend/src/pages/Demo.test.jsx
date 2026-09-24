import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PROFILE_STATE_LABELS } from "../components/ProfilePanel.jsx";
import { SYNTHETIC_DEMO_CUSTOMERS } from "../demoCustomers.js";
import Demo from "./Demo.jsx";

// backend/lstm_model.py FEATURE_NAMES, in order. Pinned there by
// test_frontend_profile_labels_match_the_backend, so a renamed feature cannot
// slip past the "never renders a feature name" test below.
const FEATURE_NAMES = [
  "scroll_hizi_varyansi",
  "tereddut_skoru",
  "etkilesim_entropisi",
  "ivme_degisimi",
  "tiklama_yogunlugu",
  "odak_degisimi",
  "hiz_otokorelasyonu",
  "yon_tutarliligi",
  "zaman_kuantasyonu",
  "duraklama_dagilimi",
  "tiklama_oncesi_hareket",
  "kanal_gecis_gecikmesi",
];

// The page holds no decision of its own: pressing Onayla asks
// POST /api/demo/charge and renders whatever came back. These tests pin that
// down, because the whole security argument rests on this file being unable to
// approve a payment by itself.
function stubSdk(overrides = {}) {
  const sdk = {
    init: vi.fn(),
    stop: vi.fn(),
    flush: vi.fn().mockResolvedValue(undefined),
    getSessionId: () => "test-session",
    getToken: () => "test-token",
    ...overrides,
  };
  vi.stubGlobal("DeepCheck", sdk);
  return sdk;
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function stubCharge(body) {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function submit(container) {
  fireEvent.submit(container.querySelector("form"));
}

function chargeBody(fetchMock, call = 0) {
  return JSON.parse(fetchMock.mock.calls[call][1].body);
}

// What the server sends for a profile escalation -- and for cluster,
// conformal and ambiguous -- once _public_verdict has collapsed the reason.
const STEP_UP = {
  status: "declined",
  decision: {
    action: "verify",
    reason: "step_up",
    risk_score: 18.2,
    label: "Gerçek Kullanıcı",
    message: "Islemi tamamlamak icin ek dogrulama gerekiyor",
  },
};

const INSUFFICIENT = {
  status: "declined",
  decision: {
    action: "verify",
    reason: "insufficient_evidence",
    message: "Karar için yeterli davranış verisi yok",
  },
};

beforeEach(() => {
  stubSdk();
});

afterEach(() => {
  // Unmount before removing the stub: Demo's effect cleanup calls
  // DeepCheck.stop(), and vitest runs this hook before the global cleanup
  // registered in vitest.setup.js.
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Demo", () => {
  it("labels every card field and gives it the right autocomplete token", () => {
    render(<Demo />);

    // Without these a password manager cannot fill the form and a screen
    // reader announces four unlabelled boxes.
    for (const [label, token] of [
      ["Kart Numarası", "cc-number"],
      ["Kart Üzerindeki İsim", "cc-name"],
      ["Son Kullanma Tarihi", "cc-exp"],
      ["CVV", "cc-csc"],
    ]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("autocomplete", token);
    }
  });

  it("makes no transport-security claim the demo cannot back", () => {
    // The page is served over plain http://localhost; "256-bit SSL" in front
    // of a fintech jury is a false statement, not decoration.
    render(<Demo />);

    expect(screen.queryByText(/SSL/)).not.toBeInTheDocument();
    expect(screen.getByText(/gerçek ödeme alınmaz/)).toBeInTheDocument();
  });

  it("announces a declined payment to assistive technology", async () => {
    stubCharge({
      status: "declined",
      decision: { action: "block", message: "İşlem Reddedildi — Şüpheli Davranış Tespit Edildi" },
    });

    const { container } = render(<Demo />);
    submit(container);

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/İşlem Reddedildi/);
  });

  it("never reports success on its own when the server declines", async () => {
    // A "declined" body with no block action still must not produce a charge
    // message. The page renders the server's verdict, it does not derive one.
    stubCharge({ status: "declined", decision: { action: "verify", reason: "score" } });

    const { container } = render(<Demo />);
    submit(container);

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("routes an unreachable backend to step-up rather than through", async () => {
    // Fail closed: a charge we could not complete is not a completed charge.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<Demo />);
    submit(container);

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("sends the latest behaviour before asking for a charge", async () => {
    // Without this the decision is made on a buffer up to one flush interval
    // old, which is exactly the input typed right before pressing Onayla.
    let finishFlush;
    const flush = vi.fn(
      () =>
        new Promise((resolve) => {
          finishFlush = resolve;
        }),
    );
    stubSdk({ flush });
    const fetchMock = stubCharge({ status: "charged", decision: { action: "allow", reason: "score" } });

    const { container } = render(<Demo />);
    submit(container);

    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));
    // Give the page every chance to jump ahead of the flush.
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(fetchMock).not.toHaveBeenCalled();

    await act(async () => finishFlush());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/demo\/charge$/);
    expect(await screen.findByText(/başarıyla alındı/)).toBeInTheDocument();
  });

  it("does not let a flush that never finishes hold the checkout forever", async () => {
    vi.useFakeTimers();
    stubSdk({ flush: vi.fn(() => new Promise(() => {})) });
    const fetchMock = stubCharge({ status: "declined", decision: { action: "verify", reason: "stale" } });

    const { container } = render(<Demo />);
    submit(container);

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(fetchMock).not.toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(3500));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("still charges with an SDK that predates flush()", async () => {
    stubSdk({ flush: undefined });
    const fetchMock = stubCharge({ status: "charged", decision: { action: "allow", reason: "score" } });

    const { container } = render(<Demo />);
    submit(container);

    expect(await screen.findByText(/başarıyla alındı/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a wait hint instead of an OTP prompt when evidence is thin", async () => {
    // Asking a real customer for a one-time code two seconds after they
    // arrived is a worse experience than telling them to carry on.
    stubCharge(INSUFFICIENT);

    const { container } = render(<Demo />);
    submit(container);

    expect(await screen.findByText(/yeterli davranış verisi yok/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers step-up when evidence is still thin on the very next attempt", async () => {
    // The hint once is courteous; the same hint on every press is a checkout
    // the customer can never finish.
    const fetchMock = stubCharge(INSUFFICIENT);

    const { container } = render(<Demo />);
    submit(container);
    expect(await screen.findByText(/yeterli davranış verisi yok/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    submit(container);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/yeterli davranış verisi yok/)).not.toBeInTheDocument();
  });

  it("starts the hint over when a different outcome comes in between", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(INSUFFICIENT))
      .mockResolvedValueOnce(jsonResponse({ status: "declined", decision: { action: "block" } }))
      .mockResolvedValueOnce(jsonResponse(INSUFFICIENT));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<Demo />);
    submit(container);
    expect(await screen.findByText(/yeterli davranış verisi yok/)).toBeInTheDocument();

    submit(container);
    expect(await screen.findByText(/İşlem Reddedildi/)).toBeInTheDocument();

    submit(container);
    expect(await screen.findByText(/yeterli davranış verisi yok/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks for a reload, not a code, when the server never observed the session", async () => {
    // /api/demo/verify answers 404 for such a session, so the modal could
    // only fail and the next charge would open it again.
    stubCharge({
      status: "declined",
      decision: { action: "verify", reason: "unknown_session", message: "Oturum bulunamadi" },
    });

    const { container } = render(<Demo />);
    submit(container);

    expect(await screen.findByText(/davranış verisi toplanamadı/)).toBeInTheDocument();
    expect(screen.getByText(/sayfayı yenileyip/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sayfayı Yenile" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Pressing Onayla again gives the same answer, still without a modal.
    submit(container);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/davranış verisi toplanamadı/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks for a reload when the SDK never obtained a session token", async () => {
    stubSdk({ getSessionId: () => null, getToken: () => null });
    const fetchMock = stubCharge({ status: "charged", decision: { action: "allow" } });
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<Demo />);
    submit(container);

    expect(await screen.findByText(/davranış verisi toplanamadı/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("asks for a reload when the server rejects the session token", async () => {
    // A rejected token is rejected by /api/demo/verify too, so the modal could
    // only fail.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Gecersiz oturum" }, 401)));
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<Demo />);
    submit(container);

    expect(await screen.findByText(/davranış verisi toplanamadı/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not reopen the code prompt after the server accepted a code", async () => {
    // Verify -> charge -> verify again would ask for a code the server has
    // already accepted, and the customer could go round that indefinitely.
    const declined = jsonResponse({ status: "declined", decision: { action: "verify", reason: "score" } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(declined)
      .mockResolvedValueOnce(jsonResponse({ verified: true, message: "ok" }))
      .mockResolvedValueOnce(declined);
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<Demo />);
    submit(container);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/doğrulama kodu/i), { target: { value: "482913" } });
    fireEvent.click(screen.getByRole("button", { name: "Doğrula" }));

    expect(await screen.findByText(/Doğrulama kaydedildi/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/demo\/verify$/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks for a reload, not another code, when the token expires while the prompt is open", async () => {
    // Tokens live 30 minutes. A 401 from /api/demo/verify means no code can
    // help; showing the server's detail inside the still-open prompt left the
    // customer typing codes into a dead session.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(STEP_UP))
      .mockResolvedValueOnce(jsonResponse({ detail: "Oturum jetonunun suresi doldu" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<Demo />);
    submit(container);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/doğrulama kodu/i), { target: { value: "482913" } });
    fireEvent.click(screen.getByRole("button", { name: "Doğrula" }));

    expect(await screen.findByText(/davranış verisi toplanamadı/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/suresi doldu/)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("names the demo customer in a field that says it is a demo shortcut", () => {
    render(<Demo />);

    const field = screen.getByLabelText("Müşteri Referansı (demo)");
    expect(field).toHaveValue("demo-musteri-1");
    expect(field).toHaveAttribute("maxlength", "64");
    // Verbatim (spec 14.2): the jury must read that a real integration never
    // takes this value from the browser.
    const help = screen.getByText(
      "Demo kısayolu: gerçek entegrasyonda bu değeri tarayıcı değil, satıcının sunucusu gönderir. Lütfen gerçek kişisel bilgi girmeyin.",
    );
    expect(field.getAttribute("aria-describedby")).toContain(help.id);
    // Spec 5.7: the page says the reference lives in the reserved demo
    // namespace, where no real merchant's customer can be.
    const namespace = screen.getByText(/ayrılmış demo ad alanında tutulur/);
    expect(namespace).toHaveTextContent(/hiçbir gerçek satıcının müşterisiyle eşleşemez/);
    // And how long it stays: the backend sweeps the demo namespace on the
    // session's clock.
    expect(namespace).toHaveTextContent(/24 saat içinde silinir/);
    expect(field.getAttribute("aria-describedby").split(" ")).toContain(namespace.id);
    // The notice is served by the app itself (pages/KvkkNotice.jsx), so the
    // link works offline and does not depend on a public repository.
    expect(screen.getByRole("link", { name: "KVKK Aydınlatma Metni" })).toHaveAttribute("href", "/kvkk");
  });

  it("sends the demo customer reference with the charge, trimmed", async () => {
    const fetchMock = stubCharge({ status: "charged", decision: { action: "allow", reason: "score" } });

    const { container } = render(<Demo />);
    fireEvent.change(screen.getByLabelText("Müşteri Referansı (demo)"), { target: { value: "  demo-musteri-2 " } });
    submit(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(chargeBody(fetchMock)).toEqual({
      session_id: "test-session",
      amount: expect.any(Number),
      customer_ref: "demo-musteri-2",
    });
  });

  it("names no customer at all when the field is empty", async () => {
    const fetchMock = stubCharge({ status: "charged", decision: { action: "allow", reason: "score" } });

    const { container } = render(<Demo />);
    fireEvent.change(screen.getByLabelText("Müşteri Referansı (demo)"), { target: { value: "   " } });
    submit(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(chargeBody(fetchMock)).not.toHaveProperty("customer_ref");
  });

  it("explains a reference the server would refuse instead of sending it", async () => {
    // The server's shape rule is printable ASCII. Without this a customer
    // typing Turkish letters gets a 400, which the page would otherwise treat
    // as a failed charge and answer with an OTP prompt.
    const flush = vi.fn().mockResolvedValue(undefined);
    stubSdk({ flush });
    const fetchMock = stubCharge({ status: "charged", decision: { action: "allow", reason: "score" } });

    const { container } = render(<Demo />);
    const field = screen.getByLabelText("Müşteri Referansı (demo)");
    fireEvent.change(field, { target: { value: "ayşe-öztürk" } });
    submit(container);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Geçersiz müşteri referansı/);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(flush).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Editing the field clears the complaint.
    fireEvent.change(field, { target: { value: "demo-musteri-1" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("points at the reference, not an OTP prompt, when the server refuses it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Gecersiz musteri referansi" }, 400)));
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<Demo />);
    submit(container);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Geçersiz müşteri referansı/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("still fails closed on an unexplained 400 when no customer was named", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "?" }, 400)));
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<Demo />);
    fireEvent.change(screen.getByLabelText("Müşteri Referansı (demo)"), { target: { value: "" } });
    submit(container);

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lists the synthetic demo customers and says on every one of them that it is synthetic", () => {
    // The prototype runs on synthetic customers because there is no customer
    // base; what is not negotiable is that the page says so where a juror
    // picks one. backend/test_demo.py pins the roster to demo_seed.py.
    render(<Demo />);

    const select = screen.getByLabelText("Demo Müşterisi");
    const options = within(select).getAllByRole("option");
    expect(options).toHaveLength(SYNTHETIC_DEMO_CUSTOMERS.length + 1);
    SYNTHETIC_DEMO_CUSTOMERS.forEach((customer, i) => {
      expect(options[i]).toHaveValue(customer.ref);
      expect(options[i]).toHaveTextContent(customer.name);
      expect(options[i]).toHaveTextContent(/ — sentetik geçmiş \(fare 20, klavye 20\)$/);
    });
    expect(options.at(-1)).toHaveTextContent(/Serbest referans/);

    // One line, tied to the selector, naming them and what they are.
    const note = screen.getByText(/sentetik demo müşterileridir/);
    expect(note).toHaveTextContent("Ayşe, Mehmet ve Zeynep sentetik demo müşterileridir");
    expect(note).toHaveTextContent(/simülatörle üretildi, gerçek kişi değildir/);
    expect(select).toHaveAttribute("aria-describedby", note.id);

    // Free entry is the default: nobody is compared with a synthetic history
    // unless the presenter picks one.
    expect(select).toHaveValue("serbest");
    expect(screen.getByLabelText("Müşteri Referansı (demo)")).toHaveValue("demo-musteri-1");
  });

  it("pays as a synthetic customer under that customer's own reference", async () => {
    const fetchMock = stubCharge(STEP_UP);

    const { container } = render(<Demo />);
    fireEvent.change(screen.getByLabelText("Demo Müşterisi"), { target: { value: "sentetik-ayse" } });
    // The field shows what will be sent, and the reference itself says synthetic.
    expect(screen.getByLabelText("Müşteri Referansı (demo)")).toHaveValue("sentetik-ayse");
    submit(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(chargeBody(fetchMock).customer_ref).toBe("sentetik-ayse");
    // A step-up against the synthetic history is the same generic prompt as
    // every other one.
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
  });

  it("keeps the selector and the reference field in agreement", () => {
    render(<Demo />);
    const select = screen.getByLabelText("Demo Müşterisi");
    const field = screen.getByLabelText("Müşteri Referansı (demo)");

    fireEvent.change(field, { target: { value: " sentetik-mehmet " } });
    expect(select).toHaveValue("sentetik-mehmet");

    fireEvent.change(field, { target: { value: "sentetik-mehmet-2" } });
    expect(select).toHaveValue("serbest");

    fireEvent.change(select, { target: { value: "sentetik-zeynep" } });
    expect(field).toHaveValue("sentetik-zeynep");

    // Back to free entry: the field returns to the default demo customer
    // rather than silently keeping a synthetic reference.
    fireEvent.change(select, { target: { value: "serbest" } });
    expect(field).toHaveValue("demo-musteri-1");
  });

  it("opens the code prompt for step_up, the reason a profile escalation arrives as", async () => {
    stubCharge(STEP_UP);

    const { container } = render(<Demo />);
    submit(container);

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    // Not mistaken for thin evidence or for an unknown session.
    expect(screen.queryByText(/yeterli davranış verisi yok/)).not.toBeInTheDocument();
    expect(screen.queryByText(/davranış verisi toplanamadı/)).not.toBeInTheDocument();
    expect(screen.queryByText(/başarıyla alındı/)).not.toBeInTheDocument();
  });

  it("never renders a profile state, a deviation or a feature name", async () => {
    // Spec 13 / 14.2. The checkout is the scored party: naming the check that
    // fired, or confirming that this customer has a mature profile, is the
    // tuning signal the server collapses into "step_up". The page must show
    // none of it even if a response carried it, so both the live score and the
    // charge answers here are deliberately stuffed with profile fields.
    const leakedProfile = {
      state: "evaluated",
      modality: "mouse",
      reference_n: 20,
      probation_n: 3,
      deviation: 4.731,
      p_value: 0.0476,
      p_value_low: 0.9524,
      top_features: FEATURE_NAMES.map((feature, i) => ({ feature, z: 5.24 - i / 10 })),
      escalated: true,
      shadow: false,
    };
    const sdk = stubSdk();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          ...STEP_UP,
          decision: { ...STEP_UP.decision, internal_reason: "profile_deviation", profile: leakedProfile },
          profile: leakedProfile,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          status: "charged",
          decision: { action: "allow", reason: "score", profile: leakedProfile },
          profile: leakedProfile,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<Demo />);
    // Paying as a synthetic customer -- the jury demo itself -- so the one
    // flow a profile escalation is expected in is the one proven not to leak.
    fireEvent.change(screen.getByLabelText("Demo Müşterisi"), { target: { value: "sentetik-ayse" } });
    act(() =>
      sdk.init.mock.calls[0][0].onUpdate({
        risk_score: 18.2,
        provisional: false,
        label: "Gerçek Kullanıcı",
        response_time_ms: 21,
        shap_explanation: FEATURE_NAMES.map((feature) => ({ feature, value: 0.5, impact: 7.77 })),
        profile: leakedProfile,
      }),
    );

    const forbidden = [
      ...Object.values(PROFILE_STATE_LABELS),
      ...Object.keys(PROFILE_STATE_LABELS),
      ...FEATURE_NAMES,
      "profile_deviation",
      "Müşteri Profili",
      "Referans oturum",
      "Sapma",
      "sapma",
      "p-değeri",
      "p_value",
      "deviation",
      "Gölge modu",
      "Ek doğrulama istendi",
      "Onay bekleyen",
      "probation",
      "/ 19",
      "4.73",
      "0.047",
      "5.2",
    ];
    const assertNothingLeaked = () => {
      const text = document.body.textContent;
      for (const item of forbidden) expect(text, `page shows "${item}"`).not.toContain(item);
    };

    assertNothingLeaked();

    submit(container);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    assertNothingLeaked();

    fireEvent.click(screen.getByRole("button", { name: "Kapat" }));
    submit(container);
    expect(await screen.findByText(/başarıyla alındı/)).toBeInTheDocument();
    assertNothingLeaked();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
