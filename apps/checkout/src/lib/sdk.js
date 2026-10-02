// The one place this app touches the behaviour SDK (sdk/deepcheck.js).
//
// The SDK posts to "/deepcheck/api/..." on this origin; checkout-web's nginx
// strips the prefix and hands it to the core (nginx.conf). No callback is
// passed: the SDK's /api/analyze answers carry a score, and this page shows
// the payer nothing about one -- not even by accident, through a callback a
// later edit might start rendering.
export const SDK_API_BASE = "/deepcheck";

// Upper bounds on waiting for the SDK before asking the store to charge. The
// SDK's own fetch has no deadline, so a stalled request would otherwise leave
// the payer watching "İşleniyor…" indefinitely. The wait serves honest payers
// (their last seconds of input reach the core before it decides); it is not a
// control -- a client that wants its last seconds unrecorded can simply not
// send them, which is why freshness is enforced on the core.
export const READY_WAIT_MS = 4000;
export const FLUSH_WAIT_MS = 4000;

function sdk() {
  return typeof window !== "undefined" ? window.DeepCheck : undefined;
}

export function startSdk() {
  const instance = sdk();
  if (!instance?.init) return () => {};
  instance.init({ apiUrl: SDK_API_BASE, intervalMs: 2000 });
  return () => instance.stop?.();
}

// Once the payment is through there is nothing left to decide, so there is no
// reason to keep measuring the payer reading their receipt. "Yeni ödeme"
// reloads the page, which starts a fresh session.
export function stopSdk() {
  sdk()?.stop?.();
}

async function bounded(work, ms) {
  let timer;
  try {
    await Promise.race([
      Promise.resolve(work),
      new Promise((resolve) => {
        timer = window.setTimeout(resolve, ms);
      }),
    ]);
  } catch {
    // flush() and ready() never reject by contract; an SDK that breaks the
    // contract must not break the payment button with it.
  } finally {
    window.clearTimeout(timer);
  }
}

// Resolves true once the SDK holds a session id and token, false when it
// never got them: the script did not load, or registration failed (core not
// answering, mint limit full). The SDK never retries a failed registration on
// its own, so false means only a reload can help -- and the page says so at
// once instead of after the payer has filled in the whole form.
//
// Deliberately NOT bounded like the waits below. Registration includes a proof
// of work and two requests the SDK abandons after 10 s each, so ready() always
// settles; cutting it short would call a slow but healthy registration a
// failure and tell an honest payer to reload for nothing.
export async function sdkRegistered() {
  const instance = sdk();
  if (!instance?.ready) return false;
  try {
    await instance.ready();
  } catch {
    // ready() never rejects by contract; if it does, the token check decides.
  }
  return Boolean(instance.getSessionId?.() && instance.getToken?.());
}

// Waits for the session to exist and for the latest behaviour to be sent,
// then returns { sessionId, token } -- or null when the SDK never registered
// (blocked script, no network to the core). Read fresh every time: the SDK
// replaces its session when a token expires.
export async function sessionCredentials() {
  const instance = sdk();
  if (!instance) return null;
  await bounded(instance.ready?.(), READY_WAIT_MS);
  await bounded(instance.flush?.(), FLUSH_WAIT_MS);
  const sessionId = instance.getSessionId?.();
  const token = instance.getToken?.();
  return sessionId && token ? { sessionId, token } : null;
}
