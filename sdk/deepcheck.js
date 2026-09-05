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
 * The session id is minted by the server (POST /api/session) together with a
 * signed token, and every flush carries that token in X-DeepCheck-Token. The
 * id is no longer generated in the browser: a client-chosen id let anyone
 * post telemetry under another customer's session, and let a bot skip the
 * SDK entirely and still have its id look legitimate at checkout.
 */
(function (window) {
  "use strict";

  const DEFAULT_INTERVAL_MS = 2000;
  const HESITATION_THRESHOLD_MS = 400; // gaps longer than this count as "hesitation"
  // How much history each flush carries, so a couple of quiet seconds (e.g.
  // the user is only typing, not moving the mouse) don't reset every feature
  // to "no data". EVERY buffer is rolled over this window, hesitation
  // included -- see the note on state.hesitationIntervals below.
  const ROLLING_WINDOW_MS = 10000;

  // Must stay <= the server-side max_length caps in backend/main.py's
  // AnalyzeRequest. Exceeding them makes FastAPI reject the entire flush with
  // 422, which used to be indistinguishable from a clean score on the client
  // (see the !res.ok handling in flushBuffer). Trimming here keeps a
  // high-polling-rate mouse -- or a bot deliberately flooding mousemove --
  // from silently blinding the detector.
  const LIMITS = {
    mouse: 2000,
    click: 500,
    scroll: 1000,
    hesitation: 500,
    focus: 200,
    key: 1000,
  };

  function createState() {
    return {
      mouseTrajectory: [],
      clickTiming: [],
      scrollEvents: [],
      // Stored as {gap, t} rather than bare numbers so this buffer can be
      // pruned to ROLLING_WINDOW_MS like every other channel. It used to be
      // cleared outright on each flush, which meant hesitation was measured
      // over a 2s window at serving time while train_model.py measures it
      // across a full ~10s session -- the single largest train/serve skew in
      // the pipeline, leaving tereddut_skoru pinned to its neutral fallback
      // for the large majority of real flushes. `t` is stripped before send;
      // the API contract is still a plain list of gap durations.
      hesitationIntervals: [],
      focusChanges: [],
      keyEvents: [],
      lastEventAt: null,
      lastScrollY: typeof window.scrollY === "number" ? window.scrollY : 0,
    };
  }

  let state = createState();
  let config = {
    apiUrl: "http://localhost:8000",
    intervalMs: DEFAULT_INTERVAL_MS,
    onUpdate: null,
    onError: null,
  };
  let sessionId = null;
  let sessionToken = null;
  let timerId = null;
  let started = false;
  // Resolves once the server has minted this page's session. Collection
  // starts immediately regardless -- the listeners are attached before this
  // request is even sent, so the behavior of the first two seconds is not
  // lost while the round trip is in flight.
  let registration = null;

  function register() {
    // React StrictMode mounts, unmounts and remounts in development, so
    // init() runs twice per page load. Minting a second session there would
    // orphan the first one and double the id count for every real visit.
    if (sessionId && sessionToken) return Promise.resolve({ session_id: sessionId, token: sessionToken });

    return fetch(`${config.apiUrl}/api/session`, { method: "POST" })
      .then((res) => {
        if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!data || typeof data.session_id !== "string" || typeof data.token !== "string") {
          throw new Error("DeepCheck oturum yanıtı geçersiz");
        }
        sessionId = data.session_id;
        sessionToken = data.token;
        return data;
      });
  }

  function now() {
    return Date.now();
  }

  function recordHesitation() {
    const t = now();
    if (state.lastEventAt !== null) {
      const gap = t - state.lastEventAt;
      if (gap >= HESITATION_THRESHOLD_MS) {
        state.hesitationIntervals.push({ gap, t });
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

  // Keep the most recent `max` entries: the newest behavior is the most
  // diagnostic, and dropping from the head preserves the tail the features
  // are actually computed over.
  function capTail(list, max) {
    return list.length > max ? list.slice(list.length - max) : list;
  }

  function flushBuffer() {
    // No token yet means the session has not been minted (the request is
    // still in flight, or it failed). Dropping the flush is correct: the
    // buffers are rolling, so the next tick re-sends this window's behavior
    // rather than losing it.
    if (!sessionId || !sessionToken) return;

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
        state.hesitationIntervals.push({ gap: idleGap, t });
        state.lastEventAt = t;
      }
    }

    // Roll every continuous-signal buffer forward (keep last
    // ROLLING_WINDOW_MS) instead of wiping it, so a brief quiet tick doesn't
    // zero out the next request's feature vector. This happens BEFORE the
    // payload is built so what goes on the wire is exactly the window the
    // features are meant to describe.
    state.mouseTrajectory = pruneToWindow(state.mouseTrajectory, t, (m) => m.t);
    state.clickTiming = pruneToWindow(state.clickTiming, t, (c) => c.t);
    state.scrollEvents = pruneToWindow(state.scrollEvents, t, (s) => s.t);
    state.keyEvents = pruneToWindow(state.keyEvents, t, (k) => k.t);
    state.focusChanges = pruneToWindow(state.focusChanges, t, (f) => f);
    state.hesitationIntervals = pruneToWindow(state.hesitationIntervals, t, (h) => h.t);

    const payload = {
      session_id: sessionId,
      mouse_trajectory: capTail(state.mouseTrajectory, LIMITS.mouse),
      click_timing: capTail(state.clickTiming, LIMITS.click),
      scroll_events: capTail(state.scrollEvents, LIMITS.scroll),
      hesitation_intervals: capTail(state.hesitationIntervals, LIMITS.hesitation).map((h) => h.gap),
      focus_changes: capTail(state.focusChanges, LIMITS.focus),
      key_events: capTail(state.keyEvents, LIMITS.key),
    };

    // Nothing collected yet at all — skip the request
    const hasData =
      payload.mouse_trajectory.length ||
      payload.click_timing.length ||
      payload.scroll_events.length ||
      payload.hesitation_intervals.length ||
      payload.focus_changes.length ||
      payload.key_events.length;
    if (!hasData) return;

    fetch(`${config.apiUrl}/api/analyze`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Signed by the server when the session was minted. Without it the
        // API returns 401: telemetry can only be posted under an id whose
        // token the poster actually holds.
        "X-DeepCheck-Token": sessionToken,
      },
      body: JSON.stringify(payload),
    })
      // An error response is JSON too, so parsing it unconditionally used to
      // hand FastAPI's error body straight to onUpdate as if it were a score.
      // The consumer then read `risk_score` off it, got undefined, and fell
      // back to a clean value -- a 422/500/503 rendered as "Gerçek Kullanıcı".
      // Any non-2xx is now an explicit failure and never reaches onUpdate.
      .then((res) => {
        if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
        return res.json();
      })
      .then((result) => {
        if (typeof result.risk_score !== "number" || !isFinite(result.risk_score)) {
          throw new Error("DeepCheck API geçersiz yanıt döndürdü");
        }
        if (typeof config.onUpdate === "function") config.onUpdate(result);
        window.dispatchEvent(new CustomEvent("deepcheck:update", { detail: result }));
      })
      .catch((err) => {
        console.error("[DeepCheck] analyze isteği başarısız:", err);
        // Surfaced so the host page can fail CLOSED. Silence here is what let
        // a dead backend read as a clean session.
        if (typeof config.onError === "function") config.onError(err);
        window.dispatchEvent(
          new CustomEvent("deepcheck:error", { detail: { message: String(err && err.message) } })
        );
      });
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

    // Listeners first, then registration: behavior from the very first
    // moment is buffered even though it cannot be sent yet.
    attachListeners();
    timerId = window.setInterval(flushBuffer, config.intervalMs);

    registration = register().catch((err) => {
      console.error("[DeepCheck] oturum kaydı başarısız:", err);
      // The host page must be able to fail CLOSED. A session that was never
      // registered can never be scored, and a missing score is not a clean
      // one.
      if (typeof config.onError === "function") config.onError(err);
      window.dispatchEvent(
        new CustomEvent("deepcheck:error", { detail: { message: String(err && err.message) } })
      );
      return null;
    });

    return registration;
  }

  function stop() {
    if (!started) return;
    detachListeners();
    if (timerId) window.clearInterval(timerId);
    timerId = null;
    started = false;
  }

  function getSessionId() {
    return sessionId;
  }

  function getToken() {
    return sessionToken;
  }

  // Resolves once the server-minted session is available (or immediately, if
  // registration already failed). A host page that needs the id before it can
  // do anything -- e.g. to call /api/decision -- awaits this.
  function ready() {
    return registration || Promise.resolve(null);
  }

  window.DeepCheck = { init, stop, getSessionId, getToken, ready };
})(window);
