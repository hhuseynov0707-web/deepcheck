import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/navigation.js", () => ({ reloadPage: vi.fn() }));

import { reloadPage } from "../lib/navigation.js";
import Checkout from "./Checkout.jsx";

// The checkout holds no decision of its own: "Öde" asks the store's server and
// renders the answer. These tests pin down what the payer can and cannot see,
// and what the page sends.

const CART = {
  items: [{ name: "Mekanik Klavye - RGB Aydınlatmalı", unit_price: 1699 }],
  subtotal: 1699,
  vat: 339.8,
  total: 2038.8,
  currency: "TRY",
};

const PAN = "4111 1111 1111 1111";
const CVV = "739";

const DECLINED_TEXT =
  "Ödemeniz tamamlanamadı. Kartınızdan çekim yapılmadı. Sorun devam ederse mağazayla iletişime geçin.";
const UNAVAILABLE_TEXT = "Ödeme şu anda işlenemiyor. Lütfen birkaç dakika sonra tekrar deneyin.";
const RELOAD_TEXT = "Ödeme sayfası tam olarak yüklenemedi. Lütfen sayfayı yenileyip tekrar deneyin.";

// Anything that would tell the payer they were scored, or how. Checked against
// the WHOLE rendered document in every state, not just the notice.
const SCORING_WORDS =
  /skor|score|risk|\bbot\b|şüpheli|gerçek kullanıcı|tespit|analiz|deepcheck|güven(ilir)? puan|yüksek risk|engellendi|\bband/i;

function stubSdk(overrides = {}) {
  const sdk = {
    init: vi.fn(),
    stop: vi.fn(),
    ready: vi.fn().mockResolvedValue(null),
    flush: vi.fn().mockResolvedValue(undefined),
    getSessionId: () => "sess-123",
    getToken: () => "1759320000.tok",
    ...overrides,
  };
  vi.stubGlobal("DeepCheck", sdk);
  return sdk;
}

function reply(body, status = 200) {
  return { status, json: async () => body };
}

// routes: { "/api/cart": reply | reply[] | (init) => reply }. Arrays are
// consumed one answer per call.
function stubServer(routes) {
  const fetchMock = vi.fn(async (url, init) => {
    let answer = routes[url];
    if (Array.isArray(answer)) answer = answer.shift();
    if (typeof answer === "function") answer = answer(init);
    if (answer instanceof Error) throw answer;
    if (!answer) return reply({ detail: "no route" }, 404);
    return answer;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock, url) {
  return fetchMock.mock.calls.filter(([u]) => u === url);
}

function bodyOf(call) {
  return JSON.parse(call[1].body);
}

async function renderReady() {
  const view = render(<Checkout />);
  await screen.findByRole("button", { name: "₺2.038,80 Öde" });
  return view;
}

function fill({
  number = PAN,
  name = "ayşe yılmaz",
  expiry = "12/30",
  cvv = CVV,
} = {}) {
  fireEvent.change(screen.getByLabelText("Kart numarası"), { target: { value: number } });
  fireEvent.change(screen.getByLabelText("Kart üzerindeki isim"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("Son kullanma (AA/YY)"), { target: { value: expiry } });
  fireEvent.change(screen.getByLabelText("CVV"), { target: { value: cvv } });
}

function pay() {
  fireEvent.click(screen.getByRole("button", { name: "₺2.038,80 Öde" }));
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function pageText() {
  return document.body.textContent;
}

// jsdom does not implement the HTML "focus fixup rule": a focused control that
// becomes disabled keeps the focus there, while Chromium moves it to <body>.
// Emulated where a test depends on it, so the test sees what a browser does.
function emulateFocusFixup() {
  const observer = new MutationObserver(() => {
    const el = document.activeElement;
    if (el && el !== document.body && (el.disabled || el.closest?.("fieldset[disabled]"))) {
      // jsdom's blur() ignores an element that is no longer focusable, so the
      // focus is moved to <body> explicitly (focusable only for this call).
      document.body.setAttribute("tabindex", "-1");
      document.body.focus();
      document.body.removeAttribute("tabindex");
    }
  });
  observer.observe(document.body, { attributes: true, subtree: true, attributeFilter: ["disabled"] });
  return () => observer.disconnect();
}

let sdk;
let stopFixup = null;

beforeEach(() => {
  sdk = stubSdk();
});

afterEach(() => {
  stopFixup?.();
  stopFixup = null;
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Checkout", () => {
  it("renders the order from GET /api/cart", async () => {
    stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    const summary = screen.getByRole("region", { name: "Sipariş özeti" });
    // Only the amounts: no product line, no illustration.
    expect(within(summary).queryByText(/Mekanik Klavye/)).not.toBeInTheDocument();
    expect(within(summary).queryByRole("img")).not.toBeInTheDocument();
    expect(within(summary).getByText("₺1.699,00")).toBeInTheDocument(); // "Ara toplam"
    expect(within(summary).getByText("KDV (%20)")).toBeInTheDocument();
    expect(within(summary).getByText("₺339,80")).toBeInTheDocument();
    expect(within(summary).getByText("₺2.038,80")).toBeInTheDocument();
    // The amount, large, above the order: the page's h1 names it.
    const hero = screen.getByRole("heading", { level: 1, name: "Ödenecek tutar" });
    expect(hero.nextElementSibling).toHaveTextContent("₺2.038,80");
  });

  it("starts the SDK on its proxied path and gives it no callback to render", async () => {
    stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    expect(sdk.init).toHaveBeenCalledWith({ apiUrl: "/deepcheck", intervalMs: 2000 });
    const options = sdk.init.mock.calls[0][0];
    expect(options.onUpdate).toBeUndefined();
    expect(options.onError).toBeUndefined();
  });

  it("offers a retry when the cart cannot be loaded, and keeps the pay button off", async () => {
    stubServer({ "/api/cart": [reply({ detail: "x" }, 503), reply(CART)] });
    render(<Checkout />);

    expect(await screen.findByText("Sipariş bilgileri yüklenemedi.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Öde" })).toBeDisabled();
    // No figure is shown rather than a guessed one.
    expect(screen.getByText("Tutar yüklenemedi")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/₺/);

    fireEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(await screen.findByRole("button", { name: "₺2.038,80 Öde" })).toBeEnabled();
  });

  it("labels every field and gives it the right autocomplete token", async () => {
    stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    for (const [label, token] of [
      ["Kart numarası", "cc-number"],
      ["Kart üzerindeki isim", "cc-name"],
      ["Son kullanma (AA/YY)", "cc-exp"],
      ["CVV", "cc-csc"],
    ]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("autocomplete", token);
    }
  });

  it("asks for no e-mail address: a guest checkout", async () => {
    stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    expect(screen.queryByLabelText(/e-posta/i)).not.toBeInTheDocument();
    expect(document.querySelector('input[type="email"]')).toBeNull();
  });

  it("never lets a digit into the cardholder name", async () => {
    stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    const name = screen.getByLabelText("Kart üzerindeki isim");
    fireEvent.change(name, { target: { value: "ay3şe y1lmaz 99" } });
    expect(name).toHaveValue("AYŞE YLMAZ ");
    expect(name.value).not.toMatch(/\d/);
  });

  it("shows each validation error next to its field and sends nothing", async () => {
    const fetchMock = stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    pay();

    for (const [label, message] of [
      ["Kart numarası", "Kart numaranızı girin."],
      ["Kart üzerindeki isim", "Kart üzerindeki ismi girin."],
      ["Son kullanma (AA/YY)", "Son kullanma tarihini girin."],
      ["CVV", "Güvenlik kodunu (CVV) girin."],
    ]) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(input).toHaveAccessibleDescription(new RegExp(escapeRegExp(message)));
    }
    // Focus goes to the first field that needs attention.
    expect(screen.getByLabelText("Kart numarası")).toHaveFocus();
    expect(callsTo(fetchMock, "/api/checkout")).toHaveLength(0);
  });

  it("checks the card number, expiry and CVV before sending", async () => {
    const fetchMock = stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    fill({ number: "4111 1111 1111 1112", expiry: "01/20", cvv: "12" });
    pay();

    expect(screen.getByText("Kart numarası geçersiz. Lütfen kontrol edin.")).toBeInTheDocument();
    expect(screen.getByText("Kartınızın son kullanma tarihi geçmiş.")).toBeInTheDocument();
    expect(screen.getByText("CVV 3 haneli olmalıdır.")).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/checkout")).toHaveLength(0);

    // Fixing a field clears its error as the payer types.
    fireEvent.change(screen.getByLabelText("Kart numarası"), { target: { value: PAN } });
    expect(screen.queryByText("Kart numarası geçersiz. Lütfen kontrol edin.")).not.toBeInTheDocument();
  });

  it("formats the number 4-4-4-4 and shows the detected brand", async () => {
    stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    fireEvent.change(screen.getByLabelText("Kart numarası"), { target: { value: "4111111111111111" } });
    expect(screen.getByLabelText("Kart numarası")).toHaveValue(PAN);
    expect(screen.getAllByText("VISA").length).toBeGreaterThanOrEqual(2); // accepted list + the field
  });

  it("sends session id, token and only the card's display fields -- never the number or CVV", async () => {
    const fetchMock = stubServer({
      "/api/cart": reply(CART),
      "/api/checkout": reply({ status: "declined" }),
    });
    await renderReady();

    fill();
    pay();
    await screen.findByText(DECLINED_TEXT);

    const [call] = callsTo(fetchMock, "/api/checkout");
    expect(call[1].method).toBe("POST");
    const raw = call[1].body;
    expect(raw).not.toContain("4111111111111111");
    expect(raw).not.toContain(PAN);
    expect(raw).not.toContain(CVV);
    expect(raw.toLowerCase()).not.toContain("ayşe");
    const body = JSON.parse(raw);
    expect(body).toEqual({
      session_id: "sess-123",
      token: "1759320000.tok",
      card: { last4: "1111", brand: "visa", exp_month: 12, exp_year: 2030 },
    });
    // The latest behaviour is flushed before the store is asked to decide.
    expect(sdk.flush.mock.invocationCallOrder[0]).toBeLessThan(fetchMock.mock.invocationCallOrder.at(-1));
  });

  it("shows İşleniyor… and a disabled form while the store decides", async () => {
    let answer;
    stubServer({
      "/api/cart": reply(CART),
      "/api/checkout": () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    await renderReady();

    fill();
    pay();

    const button = await screen.findByRole("button", { name: "İşleniyor…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("Kart numarası")).toBeDisabled();

    await waitFor(() => expect(answer).toBeTypeOf("function"));
    await act(async () => answer(reply({ status: "declined" })));
    expect(screen.getByRole("button", { name: "₺2.038,80 Öde" })).toBeEnabled();
  });

  it("paid: a receipt with order number, amount and card", async () => {
    stubServer({
      "/api/cart": reply(CART),
      "/api/checkout": reply({ status: "paid", order_id: "TS-1A2B3C4D5E", amount: 2038.8, last4: "1111", brand: "visa" }),
    });
    await renderReady();

    fill();
    pay();

    expect(await screen.findByRole("heading", { name: "Ödeme alındı" })).toHaveFocus();
    expect(screen.getByText("TS-1A2B3C4D5E")).toBeInTheDocument();
    expect(screen.getByText("Visa •••• 1111")).toBeInTheDocument();
    expect(screen.getByText("Gerçek tahsilat yapılmadı (demo).")).toBeInTheDocument();
    expect(screen.queryByLabelText("Kart numarası")).not.toBeInTheDocument();
    // Nothing is left to decide: the SDK stops measuring on the receipt.
    expect(sdk.stop).toHaveBeenCalled();
    // The amount above the order now reads as paid.
    expect(screen.getByRole("heading", { level: 1, name: "Ödenen tutar" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Yeni ödeme" }));
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  it("declined: a calm, generic message and the form back for another try", async () => {
    stubServer({ "/api/cart": reply(CART), "/api/checkout": reply({ status: "declined" }) });
    await renderReady();

    fill();
    pay();

    expect(await screen.findByText(DECLINED_TEXT)).toBeInTheDocument();
    expect(screen.getByLabelText("Kart numarası")).toBeEnabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // Focus returns to where the payer pressed, once the button is usable.
    await waitFor(() => expect(screen.getByRole("button", { name: "₺2.038,80 Öde" })).toHaveFocus());
  });

  it.each([
    ["503", () => reply({ status: "error" }, 503)],
    ["a network failure", () => new TypeError("Failed to fetch")],
    ["an unexpected answer", () => reply({ status: "mystery" })],
  ])("never shows success on %s", async (_name, answer) => {
    stubServer({ "/api/cart": reply(CART), "/api/checkout": answer });
    await renderReady();

    fill();
    pay();

    expect(await screen.findByText(UNAVAILABLE_TEXT)).toBeInTheDocument();
    expect(screen.queryByText("Ödeme alındı")).not.toBeInTheDocument();
  });

  it("asks for a reload when the SDK never registered, without calling the store", async () => {
    stubSdk({ getSessionId: () => null, getToken: () => null });
    const fetchMock = stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    fill();
    pay();

    expect(await screen.findByText(RELOAD_TEXT)).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/checkout")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Sayfayı yenile" }));
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  it("asks for a reload when the SDK script did not load at all", async () => {
    vi.stubGlobal("DeepCheck", undefined);
    const fetchMock = stubServer({ "/api/cart": reply(CART) });
    await renderReady();

    fill();
    pay();

    expect(await screen.findByText(RELOAD_TEXT)).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/checkout")).toHaveLength(0);
  });

  it("says to wait when rate limited", async () => {
    stubServer({ "/api/cart": reply(CART), "/api/checkout": reply({ status: "error", error: "rate_limited" }, 429) });
    await renderReady();

    fill();
    pay();

    expect(await screen.findByText("Çok fazla deneme yapıldı. Lütfen biraz bekleyip tekrar deneyin.")).toBeInTheDocument();
  });
});

describe("Checkout step-up (OTP)", () => {
  async function openChallenge(fetchRoutes = {}) {
    const fetchMock = stubServer({
      "/api/cart": reply(CART),
      "/api/checkout": reply({ status: "requires_action", challenge: "otp" }),
      ...fetchRoutes,
    });
    await renderReady();
    fill();
    pay();
    const dialog = await screen.findByRole("dialog", { name: "Bu ödeme için ek doğrulama gerekiyor" });
    return { fetchMock, dialog };
  }

  function typeCode(code) {
    fireEvent.change(screen.getByLabelText("Doğrulama kodu"), { target: { value: code } });
    fireEvent.click(screen.getByRole("button", { name: "Doğrula" }));
  }

  it("opens an accessible dialog with the honest demo hint", async () => {
    const { dialog } = await openChallenge();

    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByText(/tutarındaki ödemeyi onaylamak için/)).toHaveTextContent("₺2.038,80");
    expect(within(dialog).getByText(/Demo ortamı: SMS kodu/)).toHaveTextContent("Demo ortamı: SMS kodu 482913");
    expect(screen.getByLabelText("Doğrulama kodu")).toHaveFocus();
    expect(screen.getByLabelText("Doğrulama kodu")).toHaveAttribute("autocomplete", "one-time-code");
  });

  it("a wrong code stays in the dialog with a field error, the right one pays", async () => {
    const { fetchMock } = await openChallenge({
      "/api/checkout/verify": [
        reply({ status: "requires_action", error: "code" }),
        reply({ status: "paid", order_id: "TS-00AA11BB22", amount: 2038.8, last4: "1111", brand: "visa" }),
      ],
    });

    typeCode("000000");
    expect(await screen.findByText("Kod hatalı. Lütfen tekrar deneyin.")).toBeInTheDocument();
    expect(screen.getByLabelText("Doğrulama kodu")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    typeCode("482913");
    expect(await screen.findByRole("heading", { name: "Ödeme alındı" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const verifyCalls = callsTo(fetchMock, "/api/checkout/verify");
    expect(verifyCalls.map(bodyOf)).toEqual([
      { session_id: "sess-123", token: "1759320000.tok", code: "000000" },
      { session_id: "sess-123", token: "1759320000.tok", code: "482913" },
    ]);
  });

  it("after a wrong code the field has focus again, so the next code typed is the one sent", async () => {
    // Typed through the keyboard, not set with fireEvent: the field is
    // disabled while the code is checked, and a focus call made before the
    // render that re-enables it used to do nothing -- the right code then
    // went nowhere and the payer was stuck in the dialog.
    const user = userEvent.setup();
    stopFixup = emulateFocusFixup();
    // The answer arrives a moment later, as over a network: the dialog renders
    // its busy (disabled) state first, which is what the bug needed.
    const later = (body) => () => new Promise((resolve) => setTimeout(() => resolve(reply(body)), 30));
    const { fetchMock } = await openChallenge({
      "/api/checkout/verify": [
        later({ status: "requires_action", error: "code" }),
        later({ status: "paid", order_id: "TS-00AA11BB22", amount: 2038.8, last4: "1111", brand: "visa" }),
      ],
    });

    await user.keyboard("000000{Enter}");
    await screen.findByText("Kod hatalı. Lütfen tekrar deneyin.");
    await waitFor(() => expect(screen.getByLabelText("Doğrulama kodu")).toHaveFocus());

    await user.keyboard("482913{Enter}");
    expect(await screen.findByRole("heading", { name: "Ödeme alındı" })).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/checkout/verify").map(bodyOf).map((b) => b.code)).toEqual(["000000", "482913"]);
  });

  it("does not send a code that is not six digits", async () => {
    const { fetchMock } = await openChallenge();

    typeCode("12a4");
    expect(screen.getByText("6 haneli kodu girin.")).toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/checkout/verify")).toHaveLength(0);
  });

  it("a code followed by a decline closes the dialog and shows the generic message", async () => {
    await openChallenge({ "/api/checkout/verify": reply({ status: "declined" }) });

    typeCode("482913");

    expect(await screen.findByText(DECLINED_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a temporary failure keeps the dialog open with the message", async () => {
    await openChallenge({ "/api/checkout/verify": reply({ status: "error" }, 503) });

    typeCode("482913");

    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(UNAVAILABLE_TEXT)).toBeInTheDocument();
  });

  it("a lost challenge asks for a reload", async () => {
    await openChallenge({ "/api/checkout/verify": reply({ status: "error", error: "session" }, 409) });

    typeCode("482913");

    expect(await screen.findByText(RELOAD_TEXT)).toBeInTheDocument();
  });

  it("Vazgeç closes the dialog and says nothing was charged", async () => {
    await openChallenge();

    fireEvent.click(screen.getByRole("button", { name: "Vazgeç" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByText("Doğrulama tamamlanmadı; kartınızdan çekim yapılmadı. Dilediğinizde tekrar deneyebilirsiniz."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Kart numarası")).toBeEnabled();
  });

  it("Escape closes the dialog", async () => {
    await openChallenge();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Checkout never shows a score, band, label or reason", () => {
  // Even if the store's server were to leak the core's fields, the page reads
  // only `status` and the receipt fields.
  const LEAKY = { risk_score: 91.37, label: "Bot Tespit Edildi", reason: "score", message: "Supheli davranis" };

  it.each([
    ["paid", { status: "paid", order_id: "TS-1", amount: 2038.8, last4: "1111", brand: "visa", ...LEAKY }, 200],
    ["declined", { status: "declined", ...LEAKY }, 200],
    ["requires_action", { status: "requires_action", challenge: "otp", ...LEAKY }, 200],
    ["error", { status: "error", ...LEAKY }, 503],
    ["rate_limited", { status: "error", error: "rate_limited", ...LEAKY }, 429],
    ["session", { status: "error", error: "session", ...LEAKY }, 409],
  ])("in the %s state", async (_name, body, status) => {
    stubServer({ "/api/cart": reply(CART), "/api/checkout": reply(body, status) });
    await renderReady();
    expect(pageText()).not.toMatch(SCORING_WORDS);

    fill();
    pay();
    await waitFor(() => expect(screen.queryByRole("button", { name: "İşleniyor…" })).not.toBeInTheDocument());

    const text = pageText();
    expect(text).not.toMatch(SCORING_WORDS);
    expect(text).not.toContain("91.37");
    expect(text).not.toContain("91,37");
    expect(text).not.toContain("Supheli");
  });

  it("through the whole OTP flow, wrong code included", async () => {
    stubServer({
      "/api/cart": reply(CART),
      "/api/checkout": reply({ status: "requires_action", challenge: "otp", ...LEAKY }),
      "/api/checkout/verify": [
        reply({ status: "requires_action", error: "code", ...LEAKY }),
        reply({ status: "paid", order_id: "TS-2", amount: 2038.8, last4: "1111", brand: "visa", ...LEAKY }),
      ],
    });
    await renderReady();
    fill();
    pay();
    await screen.findByRole("dialog");
    expect(pageText()).not.toMatch(SCORING_WORDS);

    fireEvent.change(screen.getByLabelText("Doğrulama kodu"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Doğrula" }));
    await screen.findByText("Kod hatalı. Lütfen tekrar deneyin.");
    expect(pageText()).not.toMatch(SCORING_WORDS);

    fireEvent.change(screen.getByLabelText("Doğrulama kodu"), { target: { value: "482913" } });
    fireEvent.click(screen.getByRole("button", { name: "Doğrula" }));
    await screen.findByRole("heading", { name: "Ödeme alındı" });
    expect(pageText()).not.toMatch(SCORING_WORDS);
    expect(pageText()).not.toContain("91.37");
  });
});
