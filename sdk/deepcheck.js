/**
 * DeepCheck Behavior SDK
 * Usage:
 *   <script src="/deepcheck.js"></script>
 *   <script>
 *     DeepCheck.init({
 *       apiUrl: "http://localhost:8000",
 *       intervalMs: 2000,
 *       onUpdate: (result) => console.log(result),
 *     });
 *   </script>
 *
 * The session id is issued by the server during init() and cannot be supplied
 * by the caller -- see the handshake below.
 */
(function (window) {
  "use strict";

  const DEFAULT_INTERVAL_MS = 2000;
  const HESITATION_THRESHOLD_MS = 400; // gaps longer than this count as "hesitation"
  // How much history each flush carries, so a couple of quiet seconds (e.g.
  // the user is only typing, not moving the mouse) don't reset every feature
  // to "no data" -- only mouse/click/scroll/focus/key are rolled; hesitation
  // gaps are one-shot events, sent once and not re-sent.
  const ROLLING_WINDOW_MS = 10000;

  function createState() {
    return {
      mouseTrajectory: [],
      clickTiming: [],
      scrollEvents: [],
      hesitationIntervals: [],
      focusChanges: [],
      keyEvents: [],
      lastEventAt: null,
      lastScrollY: typeof window.scrollY === "number" ? window.scrollY : 0,
    };
  }

  let state = createState();
  let config = {
    sessionId: null,
    apiUrl: "http://localhost:8000",
    intervalMs: DEFAULT_INTERVAL_MS,
    onUpdate: null,
  };
  let timerId = null;
  let started = false;
  // Session identity now comes from the server. The SDK used to invent (or
  // accept) a session id and put it in the request body, which meant any
  // caller could write behaviour under any id -- including someone else's.
  // The server mints the id and signs a token for it; this holds that token.
  let sessionToken = null;
  let handshakePromise = null;

  function handshake() {
    // Single-flight: several flushes can land while the first handshake is
    // still in the air, and each one starting its own would create a pile of
    // orphan sessions.
    if (handshakePromise) return handshakePromise;

    handshakePromise = fetch(`${config.apiUrl}/api/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then((res) => {
        if (!res.ok) throw new Error(`oturum alınamadı (HTTP ${res.status})`);
        return res.json();
      })
      .then((data) => {
        sessionToken = data.token;
        config.sessionId = data.session_id;
        return data.token;
      })
      .catch((err) => {
        // Clear so the next flush retries rather than latching a failure.
        handshakePromise = null;
        throw err;
      });

    return handshakePromise;
  }

  function ensureToken() {
    return sessionToken ? Promise.resolve(sessionToken) : handshake();
  }

  function now() {
    return Date.now();
  }

  function recordHesitation() {
    const t = now();
    if (state.lastEventAt !== null) {
      const gap = t - state.lastEventAt;
      if (gap >= HESITATION_THRESHOLD_MS) {
        state.hesitationIntervals.push(gap);
      }
    }
    state.lastEventAt = t;
  }

  function onMouseMove(e) {
    recordHesitation();
    state.mouseTrajectory.push({ x: e.clientX, y: e.clientY, t: now() });
  }

  function onClick(e) {
    recordHesitation();
    state.clickTiming.push({ x: e.clientX, y: e.clientY, t: now() });
  }

  function onScroll() {
    recordHesitation();
    const y = window.scrollY;
    state.scrollEvents.push({ scrollY: y, t: now() });
    state.lastScrollY = y;
  }

  function onVisibilityChange() {
    if (document.hidden) {
      state.focusChanges.push(now());
    }
  }

  // Typing rhythm and pre-typing hesitation matter for bot detection, but we
  // must never capture *what* was typed (card numbers, CVV, names). Only the
  // timestamp of the keydown is recorded -- never e.key, e.code, or any
  // field value. Do not add anything here that reads input content.
  function onKeyDown() {
    recordHesitation();
    state.keyEvents.push({ t: now() });
  }

  function pruneToWindow(list, tNow, getT) {
    const cutoff = tNow - ROLLING_WINDOW_MS;
    return list.filter((item) => getT(item) >= cutoff);
  }

  function flushBuffer() {
    const t = now();

    // If the user has gone quiet since their last tracked event, that
    // silence is itself a hesitation signal -- but recordHesitation() only
    // measures a gap when a NEW event arrives to "close" it. A burst of
    // activity followed by pure idle time for the rest of the window (no
    // further event at all) was previously invisible: nothing ever closed
    // the gap, so it was never recorded. Check for pending silence at every
    // flush and record it directly. lastEventAt is advanced to "now" so the
    // next real event measures its gap from this checkpoint, not from the
    // original stale event -- otherwise the same silence would be counted
    // twice (once here, once when the next event finally fires).
    if (state.lastEventAt !== null) {
      const idleGap = t - state.lastEventAt;
      if (idleGap >= HESITATION_THRESHOLD_MS) {
        state.hesitationIntervals.push(idleGap);
        state.lastEventAt = t;
      }
    }

    const payload = {
      mouse_trajectory: state.mouseTrajectory,
      click_timing: state.clickTiming,
      scroll_events: state.scrollEvents,
      hesitation_intervals: state.hesitationIntervals,
      focus_changes: state.focusChanges,
      key_events: state.keyEvents,
    };

    // Roll the continuous-signal buffers forward (keep last ROLLING_WINDOW_MS)
    // instead of wiping them, so a brief quiet tick doesn't zero out the next
    // request's feature vector. Hesitation gaps are one-shot: sent once, then
    // cleared, since they don't carry their own timestamp to prune by.
    state.mouseTrajectory = pruneToWindow(state.mouseTrajectory, t, (m) => m.t);
    state.clickTiming = pruneToWindow(state.clickTiming, t, (c) => c.t);
    state.scrollEvents = pruneToWindow(state.scrollEvents, t, (s) => s.t);
    state.keyEvents = pruneToWindow(state.keyEvents, t, (k) => k.t);
    state.focusChanges = pruneToWindow(state.focusChanges, t, (f) => f);
    state.hesitationIntervals = [];

    // Nothing collected yet at all — skip the request
    const hasData =
      payload.mouse_trajectory.length ||
      payload.click_timing.length ||
      payload.scroll_events.length ||
      payload.hesitation_intervals.length ||
      payload.focus_changes.length ||
      payload.key_events.length;
    if (!hasData) return;

    ensureToken()
      .then((token) =>
        fetch(`${config.apiUrl}/api/analyze`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(payload),
        })
      )
      .then((res) => {
        if (res.status === 401) {
          // Token expired or the server restarted with a new signing key.
          // Drop it and re-handshake on the next flush.
          sessionToken = null;
          handshakePromise = null;
          throw new Error("oturum belirteci geçersiz, yeniden alınacak");
        }
        if (res.status === 429) throw new Error("istek sınırı aşıldı");
        if (!res.ok) throw new Error(`analiz başarısız (HTTP ${res.status})`);
        return res.json();
      })
      .then((result) => {
        if (result.session_id) config.sessionId = result.session_id;
        if (typeof config.onUpdate === "function") config.onUpdate(result);
        window.dispatchEvent(new CustomEvent("deepcheck:update", { detail: result }));
      })
      .catch((err) => {
        console.error("[DeepCheck] analyze isteği başarısız:", err);
      });
  }

  /**
   * Calls a DeepCheck endpoint with the session's bearer token attached.
   * The demo's payment flow uses this so the *server* can tie the request to
   * the session it scored, rather than the page asserting its own verdict.
   */
  function authorizedFetch(path, body) {
    return ensureToken().then((token) =>
      fetch(`${config.apiUrl}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body || {}),
      })
    );
  }

  function attachListeners() {
    window.addEventListener("mousemove", onMouseMove, { passive: true });
    window.addEventListener("click", onClick, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("visibilitychange", onVisibilityChange);
    document.addEventListener("keydown", onKeyDown, { passive: true });
  }

  function detachListeners() {
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("click", onClick);
    window.removeEventListener("scroll", onScroll);
    document.removeEventListener("visibilitychange", onVisibilityChange);
    document.removeEventListener("keydown", onKeyDown);
  }

  function init(options) {
    if (started) return;
    config = { ...config, ...(options || {}) };
    state = createState();
    started = true;

    // Start the handshake immediately so the first flush has a token ready.
    handshake().catch((err) =>
      console.error("[DeepCheck] oturum başlatılamadı:", err)
    );

    attachListeners();
    timerId = window.setInterval(flushBuffer, config.intervalMs);
  }

  function stop() {
    if (!started) return;
    detachListeners();
    if (timerId) window.clearInterval(timerId);
    timerId = null;
    started = false;
    sessionToken = null;
    handshakePromise = null;
  }

  function getSessionId() {
    return config.sessionId;
  }

  window.DeepCheck = { init, stop, getSessionId, authorizedFetch };
})(window);
