// Calls to the store's own server (apps/checkout-server), on this origin.
//
// Every answer is reduced to an outcome kind before the page sees it, and the
// page branches on that kind only. The store never sends a score, label or
// reason; this module would not pass one on if it did.

// The store's server may wait up to 3 x 2 s for more behaviour before it
// answers (its hidden insufficient-evidence retry), plus the core calls
// themselves. Past this the payer is told to try again; a late answer is
// dropped rather than turned into a receipt nobody is looking at.
export const CHECKOUT_TIMEOUT_MS = 30000;
export const VERIFY_TIMEOUT_MS = 15000;
export const CART_TIMEOUT_MS = 10000;

const STATUSES = new Set(["paid", "requires_action", "declined"]);

async function request(path, { method = "GET", body, timeoutMs }) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    return { status: response.status, data };
  } catch {
    return { status: 0, data: null };
  } finally {
    window.clearTimeout(timer);
  }
}

// -> { kind: "paid", receipt } | { kind: "requires_action" } | { kind: "declined" }
//  | { kind: "wrong_code" } | { kind: "session" } | { kind: "rate_limited" }
//  | { kind: "invalid" } | { kind: "unavailable" }
function outcome({ status, data }) {
  if (status === 429) return { kind: "rate_limited" };
  if (status === 409) return { kind: "session" };
  if (status === 422) return { kind: "invalid" };
  if (status !== 200 || !data || !STATUSES.has(data.status)) return { kind: "unavailable" };
  if (data.status === "paid") {
    return {
      kind: "paid",
      receipt: {
        orderId: String(data.order_id ?? ""),
        amount: Number(data.amount),
        last4: String(data.last4 ?? ""),
        brand: String(data.brand ?? ""),
      },
    };
  }
  if (data.status === "requires_action") {
    return data.error === "code" ? { kind: "wrong_code" } : { kind: "requires_action" };
  }
  return { kind: "declined" };
}

export async function fetchCart() {
  const { status, data } = await request("/api/cart", { timeoutMs: CART_TIMEOUT_MS });
  const valid =
    status === 200 &&
    data &&
    Array.isArray(data.items) &&
    data.items.length > 0 &&
    [data.subtotal, data.vat, data.total].every((n) => typeof n === "number" && Number.isFinite(n));
  return valid ? data : null;
}

// Only the display fields of the card. The full number and the CVV stay in
// this page's memory and are never serialised.
export async function submitCheckout({ sessionId, token, card }) {
  return outcome(
    await request("/api/checkout", {
      method: "POST",
      timeoutMs: CHECKOUT_TIMEOUT_MS,
      body: {
        session_id: sessionId,
        token,
        card: { last4: card.last4, brand: card.brand, exp_month: card.expMonth, exp_year: card.expYear },
      },
    }),
  );
}

export async function submitVerification({ sessionId, token, code }) {
  return outcome(
    await request("/api/checkout/verify", {
      method: "POST",
      timeoutMs: VERIFY_TIMEOUT_MS,
      body: { session_id: sessionId, token, code },
    }),
  );
}
