/**
 * DeepCheck Behavior SDK
 * Usage:
 *   <script src="/deepcheck.js"></script>
 *   <script>
 *     DeepCheck.init({
 *       apiUrl: "",              // same origin: POST /api/... on this page's host
 *       intervalMs: 2000,
 *       onUpdate: (result) => console.log(result),
 *     });
 *     // Before asking the server for a decision (resolves, never rejects):
 *     DeepCheck.flush().then(askServerForDecision);
 *   </script>
 *
 * apiUrl is the address the VISITOR'S browser uses to reach the API. An empty
 * string means the page's own origin, for a site that serves /api/ itself or
 * through a reverse proxy, as the bundled demo does (frontend/nginx.conf). Any
 * other value is an absolute base such as "https://api.example.com", and the
 * API must then allow this page's origin in CORS_ORIGINS. Trailing slashes are
 * dropped. Omitting apiUrl altogether keeps the old default,
 * "http://localhost:8000" -- which only works when the browser runs on the
 * same machine as the API.
 *
 * The session id is minted by the server (POST /api/session) together with a
 * signed token, and every flush carries that token in X-DeepCheck-Token. The
 * id is no longer generated in the browser: a client-chosen id let anyone
 * post telemetry under another customer's session, and let a bot skip the
 * SDK entirely and still have its id look legitimate at checkout.
 *
 * The token may expire server-side. When /api/analyze answers 401 the SDK
 * registers a NEW session once and carries on under it, so getSessionId() and
 * getToken() can change during the life of a page: read them when they are
 * needed, never cache them.
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
  // (see the !res.ok handling in postAnalyze). Trimming here keeps a
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
  // Same reason, for the ClientSignals counters (le=100_000 on the server).
  // pointermove fires continuously while the pointer moves and every one is
  // counted, so a long-lived tab can pass the cap -- after which, uncapped,
  // every flush of the session would be rejected with 422.
  const COUNTER_CAP = 100000;

  // An analyze request that never settles would hold the in-flight guard in
  // flushBuffer() forever, silencing the session without a single onError.
  // fetch() has no deadline of its own.
  const REQUEST_TIMEOUT_MS = 10000;

  function createState() {
    return {
      // Provenance counters, reported to the server but NOT scored -- see the
      // ClientSignals note in backend/main.py. They are collected now so their
      // value can be measured against real recorded sessions before anything
      // depends on them.
      //
      // untrustedEvents counts events whose isTrusted is false, i.e. events
      // synthesised by page JavaScript (element.click(), dispatchEvent). Note
      // that a browser driven by Playwright or Puppeteer emits TRUSTED events,
      // so this does not catch driven browsers; navigator.webdriver is the
      // signal for those, and it is trivially patched out. Neither is proof of
      // anything alone, which is exactly why neither is wired to the score.
      //
      // pointerTypes counts every event carrying a pointerType. Where Pointer
      // Events exist that includes every pointermove, not just clicks.
      untrustedEvents: 0,
      pointerTypes: { mouse: 0, pen: 0, touch: 0 },
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
  // The register() round trip currently in progress, if any. StrictMode's
  // init/stop/init used to start a second registration while the first was
  // still solving its proof of work, minting and orphaning an extra session.
  let registering = null;
  // The analyze request in progress (including a re-registration triggered by
  // its 401), as a promise that never rejects. At most one exists at a time:
  // two overlapping requests can land out of order, and the server rejects
  // the older window as "time going backwards" -- an onError on a healthy
  // session.
  let inFlight = null;
  // Set when a 401 has triggered a re-registration, cleared by the next
  // successful flush. A second 401 before then is reported rather than
  // answered with another registration, so a server that rejects every token
  // costs one /api/session call, not one per tick.
  let reauthAttempted = false;
  // The behaviour lists of the last flush the server accepted. The server's
  // replay check rejects a window whose events it has already stored, in ANY
  // session, so re-sending an unchanged window is a guaranteed 422.
  let lastSentKey = null;
  // Which event carries the trajectory; decided when listeners are attached
  // so detachListeners() removes exactly what was added.
  let moveEventName = "mousemove";

  // Smallest non-zero gap between consecutive performance.now() readings.
  //
  // Every engine deliberately clamps this -- roughly 100 microseconds in
  // Chrome, 1 millisecond in Firefox and Safari -- as a defence against timing
  // side channels. The value is a property of the browser and the machine, not
  // of this page, which is what makes it worth reporting. It is not a secret,
  // though: the clamps are documented, and a client fabricating telemetry can
  // simply report 100 microseconds. It rejects a script that reports nothing
  // plausible, not one that read the documentation.
  function measureClockResolution(samples) {
    var smallest = Infinity;
    var previous = performance.now();
    for (var i = 0; i < samples; i++) {
      var current = performance.now();
      var delta = current - previous;
      if (delta > 0 && delta < smallest) smallest = delta;
      previous = current;
    }
    // Milliseconds to microseconds. Infinity means the clock never advanced
    // across the whole sample, which the server treats as implausible.
    return isFinite(smallest) ? smallest * 1000 : 0;
  }

  // Median observed delay of setTimeout(..., 0). A real event loop never
  // schedules in zero milliseconds, and browsers additionally clamp nested
  // timers to about 4 ms.
  function measureTimerLag(samples) {
    return new Promise(function (resolve) {
      var observed = [];
      function step() {
        if (observed.length >= samples) {
          observed.sort(function (a, b) { return a - b; });
          resolve(observed[Math.floor(observed.length / 2)]);
          return;
        }
        var started = performance.now();
        window.setTimeout(function () {
          observed.push(performance.now() - started);
          step();
        }, 0);
      }
      step();
    });
  }

  // SHA-256 constants: the first 32 bits of the fractional parts of the cube
  // roots of the first 64 primes (FIPS 180-4, section 4.2.2).
  var SHA256_K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  function rotr(value, bits) {
    return (value >>> bits) | (value << (32 - bits));
  }

  // Pure-JavaScript SHA-256 of a byte array, as lowercase hex.
  //
  // Used ONLY when crypto.subtle is missing. Browsers expose crypto.subtle in
  // secure contexts alone, so a phone opening the demo over plain http on the
  // LAN (http://192.168.x.x:3000) could not solve the proof of work at all:
  // registration failed and every such visitor was sent to verification.
  // Which implementation produced the digest is irrelevant to the check --
  // the server recomputes it with hashlib.sha256.
  function sha256HexFallback(bytes) {
    var hash = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

    // Padding: 0x80, zeros, then the message length in bits as a 64-bit
    // big-endian integer, filling out a whole number of 64-byte blocks.
    var length = bytes.length;
    var paddedLength = Math.ceil((length + 9) / 64) * 64;
    var data = new Uint8Array(paddedLength);
    data.set(bytes);
    data[length] = 0x80;
    var bitsHigh = Math.floor(length / 0x20000000);
    var bitsLow = (length * 8) >>> 0;
    for (var p = 0; p < 4; p++) {
      data[paddedLength - 8 + p] = (bitsHigh >>> (24 - 8 * p)) & 0xff;
      data[paddedLength - 4 + p] = (bitsLow >>> (24 - 8 * p)) & 0xff;
    }

    var w = new Int32Array(64);
    for (var offset = 0; offset < paddedLength; offset += 64) {
      var i;
      for (i = 0; i < 16; i++) {
        var j = offset + i * 4;
        w[i] = (data[j] << 24) | (data[j + 1] << 16) | (data[j + 2] << 8) | data[j + 3];
      }
      for (i = 16; i < 64; i++) {
        // The trailing terms are plain shifts (>>> 3, >>> 10), not rotations.
        var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }

      var a = hash[0], b = hash[1], c = hash[2], d = hash[3];
      var e = hash[4], f = hash[5], g = hash[6], h = hash[7];
      for (i = 0; i < 64; i++) {
        var t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + SHA256_K[i] + w[i]) | 0;
        var t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        h = g;
        g = f;
        f = e;
        e = (d + t1) | 0;
        d = c;
        c = b;
        b = a;
        a = (t1 + t2) | 0;
      }
      hash[0] = (hash[0] + a) | 0;
      hash[1] = (hash[1] + b) | 0;
      hash[2] = (hash[2] + c) | 0;
      hash[3] = (hash[3] + d) | 0;
      hash[4] = (hash[4] + e) | 0;
      hash[5] = (hash[5] + f) | 0;
      hash[6] = (hash[6] + g) | 0;
      hash[7] = (hash[7] + h) | 0;
    }

    var out = "";
    for (var k = 0; k < 8; k++) {
      out += (hash[k] >>> 0).toString(16).padStart(8, "0");
    }
    return out;
  }

  function hasSubtleDigest() {
    return !!(window.crypto && window.crypto.subtle && typeof window.crypto.subtle.digest === "function");
  }

  function sha256Hex(text) {
    var bytes = new TextEncoder().encode(text);
    if (!hasSubtleDigest()) {
      return new Promise(function (resolve) {
        resolve(sha256HexFallback(bytes));
      });
    }
    return window.crypto.subtle.digest("SHA-256", bytes).then(function (buffer) {
      var out = "";
      var view = new Uint8Array(buffer);
      for (var i = 0; i < view.length; i++) {
        out += view[i].toString(16).padStart(2, "0");
      }
      return out;
    });
  }

  function leadingZeroBits(hex) {
    var bits = 0;
    for (var i = 0; i < hex.length; i++) {
      var nibble = parseInt(hex[i], 16);
      if (nibble === 0) {
        bits += 4;
        continue;
      }
      // 8->0, 4->1, 2->2, 1->3 leading zeros within the nibble.
      bits += nibble >= 8 ? 0 : nibble >= 4 ? 1 : nibble >= 2 ? 2 : 3;
      break;
    }
    return bits;
  }

  // Find a nonce whose SHA-256 starts with `difficulty` zero bits.
  //
  // A solved proof of work shows that SOME client executed the challenge. It
  // does not show that the client is a browser or that it ran this file: a
  // plain Python loop over hashlib solves the default 12 bits in about 4 ms
  // (median of 50 runs), and the runtime measurements sent next to the nonce
  // are self-reported numbers such a script can hardcode inside the server's
  // bounds. What the work does impose is a per-session cost, linear for
  // anyone minting sessions in bulk. Expected work is 2^difficulty hashes, a
  // few thousand at 12 bits.
  //
  // Yielding every YIELD_EVERY attempts keeps the page responsive; a solver
  // that blocks the main thread for half a second is a worse experience than
  // the attack it prevents.
  var POW_YIELD_EVERY = 512;

  function solveProofOfWork(challenge, difficulty) {
    return new Promise(function (resolve, reject) {
      var nonce = 0;
      function attempt() {
        var batch = 0;
        function next() {
          if (batch >= POW_YIELD_EVERY) {
            window.setTimeout(attempt, 0);
            return;
          }
          batch += 1;
          var candidate = String(nonce++);
          sha256Hex(challenge + "." + candidate)
            .then(function (hex) {
              if (leadingZeroBits(hex) >= difficulty) {
                resolve(candidate);
                return;
              }
              next();
            })
            .catch(reject);
        }
        next();
      }
      attempt();
    });
  }

  // fetch() with a deadline. Resolves to the Response and, for a 2xx only, its
  // parsed JSON body: an error body is never handed on as if it were data.
  function requestJson(url, options) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timer = controller ? window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS) : null;
    return fetch(url, controller ? { ...options, signal: controller.signal } : options)
      .then((res) => (res.ok ? res.json().then((body) => ({ res, body })) : { res, body: null }))
      .catch((err) => {
        if (err && err.name === "AbortError") throw new Error("DeepCheck API zaman aşımına uğradı");
        throw err;
      })
      .finally(() => {
        if (timer) window.clearTimeout(timer);
      });
  }

  function register() {
    // React StrictMode mounts, unmounts and remounts in development, so
    // init() runs twice per page load. Minting a second session there would
    // orphan the first one and double the id count for every real visit.
    if (sessionId && sessionToken) return Promise.resolve({ session_id: sessionId, token: sessionToken });
    if (registering) return registering;

    // Two steps. /api/session hands out a signed challenge and nothing else;
    // the token that /api/analyze requires is only issued in exchange for a
    // solved proof of work and runtime measurements inside browser-plausible
    // bounds. That closes posting telemetry with no client work at all. It
    // does NOT prove the poster is a browser or ran this file (see
    // solveProofOfWork), and a bot driving a real browser passes it honestly.
    //
    // Without crypto.subtle (any non-secure context, e.g. plain http on a LAN
    // address) the digest comes from sha256HexFallback instead of registration
    // failing.
    registering = requestJson(`${config.apiUrl}/api/session`, { method: "POST" })
      .then(({ res, body: data }) => {
        if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
        // difficulty_bits is checked too: a missing value compared as
        // `bits >= undefined`, which is never true, so the solver never ended.
        if (
          !data ||
          typeof data.session_id !== "string" ||
          typeof data.challenge !== "string" ||
          !Number.isInteger(data.difficulty_bits) ||
          data.difficulty_bits < 0
        ) {
          throw new Error("DeepCheck oturum yanıtı geçersiz");
        }
        const clockResolutionUs = measureClockResolution(2000);
        return Promise.all([
          data,
          solveProofOfWork(data.challenge, data.difficulty_bits),
          measureTimerLag(9),
          clockResolutionUs,
        ]);
      })
      .then(([data, nonce, timerLagMs, clockResolutionUs]) =>
        requestJson(`${config.apiUrl}/api/session/attest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: data.session_id,
            challenge: data.challenge,
            nonce,
            runtime: {
              clock_resolution_us: clockResolutionUs,
              timer_lag_ms: timerLagMs,
            },
          }),
        })
      )
      .then(({ res, body: data }) => {
        if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
        if (!data || typeof data.session_id !== "string" || typeof data.token !== "string") {
          throw new Error("DeepCheck doğrulama yanıtı geçersiz");
        }
        sessionId = data.session_id;
        sessionToken = data.token;
        return data;
      })
      .finally(() => {
        registering = null;
      });
    return registering;
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

  // Counts an event's provenance. Called for every tracked event, before the
  // event itself is recorded.
  function noteProvenance(e) {
    if (e && e.isTrusted === false) state.untrustedEvents += 1;
    const kind = e && e.pointerType;
    if (kind && Object.prototype.hasOwnProperty.call(state.pointerTypes, kind)) {
      state.pointerTypes[kind] += 1;
    }
  }

  // The trajectory comes from ONE event type, chosen in attachListeners():
  // pointermove where Pointer Events exist, mousemove otherwise. Listening
  // only to mousemove left touch devices with no trajectory at all: a finger
  // moving on the page fires pointermove (until the browser takes the gesture
  // over for scrolling) but never mousemove.
  //
  // For a mouse, the browser fires pointermove and then mousemove for the same
  // input, so mouse-derived features keep the sampling they had. (The one
  // extra pointermove, for a second button pressed without moving, is rare.)
  // getCoalescedEvents() is deliberately NOT used: it would change that
  // sampling rate, and speed and acceleration variance both depend on it.
  //
  // The model has never been trained on touch or pen trajectories. A score
  // for a touch session is therefore unmeasured -- nothing in the evaluation
  // says what it means -- and pointer_touch in client_signals is what lets
  // such sessions be told apart.
  function onPointerMove(e) {
    noteProvenance(e);
    // A second finger (pinch) interleaved with the first would read as a
    // cursor teleporting between two points on every event.
    if (e.isPrimary === false) return;
    recordHesitation();
    state.mouseTrajectory.push({ x: e.clientX, y: e.clientY, t: now() });
  }

  function onMouseMove(e) {
    noteProvenance(e);
    recordHesitation();
    state.mouseTrajectory.push({ x: e.clientX, y: e.clientY, t: now() });
  }

  function onClick(e) {
    noteProvenance(e);
    recordHesitation();
    state.clickTiming.push({ x: e.clientX, y: e.clientY, t: now() });
  }

  function onScroll(e) {
    noteProvenance(e);
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
  function onKeyDown(e) {
    noteProvenance(e);
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

  function reportError(context, err) {
    console.error(`[DeepCheck] ${context}:`, err);
    // Surfaced so the host page can fail CLOSED. Silence here is what let
    // a dead backend read as a clean session.
    try {
      if (typeof config.onError === "function") config.onError(err);
      window.dispatchEvent(
        new CustomEvent("deepcheck:error", { detail: { message: String(err && err.message) } })
      );
    } catch (hostErr) {
      console.error("[DeepCheck] onError işleyicisi hata verdi:", hostErr);
    }
  }

  // Builds the flush for the current rolling window, or returns null when the
  // window holds no behaviour. Advances the idle checkpoint and prunes the
  // buffers as a side effect, exactly once per call.
  function collectPayload() {
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
      client_signals: {
        untrusted_events: Math.min(state.untrustedEvents, COUNTER_CAP),
        webdriver: navigator.webdriver === true,
        pointer_mouse: Math.min(state.pointerTypes.mouse, COUNTER_CAP),
        pointer_pen: Math.min(state.pointerTypes.pen, COUNTER_CAP),
        pointer_touch: Math.min(state.pointerTypes.touch, COUNTER_CAP),
      },
    };

    // Only timestamped events count as behaviour. hesitation_intervals does
    // NOT: after ten idle seconds it is the only list left, holding nothing
    // but the SDK's own flush-time silence checkpoints. Posting it every tick
    // refreshed the session's freshness with no behaviour behind it, and idle
    // windows from different visitors hash identically, so the server's
    // replay check rejected the second one as a duplicate. client_signals does
    // not count either: provenance counters alone have nothing to score.
    const hasBehaviour =
      payload.mouse_trajectory.length ||
      payload.click_timing.length ||
      payload.scroll_events.length ||
      payload.focus_changes.length ||
      payload.key_events.length;
    return hasBehaviour ? payload : null;
  }

  // Exactly the lists the server fingerprints for its replay check.
  function behaviourKey(payload) {
    return JSON.stringify([
      payload.mouse_trajectory,
      payload.click_timing,
      payload.scroll_events,
      payload.hesitation_intervals,
      payload.focus_changes,
      payload.key_events,
    ]);
  }

  // Posts one flush. Rejects on any failure; a 401 is answered once with a
  // fresh registration and a resend of the same window under the new session.
  function postAnalyze(payload) {
    const key = behaviourKey(payload);
    // Unchanged since the last window the server accepted (e.g. flush()
    // called right after a tick with no event in between). The server has
    // these events already and would reject the resend as a replay.
    if (key === lastSentKey) return Promise.resolve();

    // This device's clock at the moment of sending. Every event timestamp
    // comes from the same clock, so the server can measure the device's
    // offset instead of treating a phone whose clock is wrong as a replay.
    payload.client_sent_at = now();

    return requestJson(`${config.apiUrl}/api/analyze`, {
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
      .then(({ res, body: result }) => {
        if (res.status === 401 && !reauthAttempted) return reregisterAndResend(payload);
        if (!res.ok) throw new Error(`DeepCheck API ${res.status}`);
        if (!result || typeof result.risk_score !== "number" || !isFinite(result.risk_score)) {
          throw new Error("DeepCheck API geçersiz yanıt döndürdü");
        }
        lastSentKey = key;
        reauthAttempted = false;
        if (typeof config.onUpdate === "function") config.onUpdate(result);
        window.dispatchEvent(new CustomEvent("deepcheck:update", { detail: result }));
      });
  }

  // The token was refused -- expired, or signed with a secret the server no
  // longer holds. The old session cannot be recovered, so discard it and
  // register a new one. Once: if the new token is refused too, that 401 is
  // reported like any other failure.
  function reregisterAndResend(payload) {
    reauthAttempted = true;
    sessionId = null;
    sessionToken = null;
    const pending = register();
    // ready() waits for the replacement session, not the discarded one.
    registration = pending.catch(() => null);
    return pending.then(() => {
      // The same window, under the new id. Re-collecting instead would add a
      // silence checkpoint that exists only because registration took time.
      payload.session_id = sessionId;
      return postAnalyze(payload);
    });
  }

  // Starts a flush unless one is already in flight. Returns the in-flight
  // promise (never rejects), or null when there was nothing to send.
  function startFlush() {
    if (inFlight) return inFlight;
    // No token yet means the session has not been minted (the request is
    // still in flight, or it failed). Dropping the flush is correct: the
    // buffers are rolling, so the next flush re-sends this window's behavior
    // rather than losing it.
    if (!sessionId || !sessionToken) return null;
    const payload = collectPayload();
    if (!payload) return null;
    const release = () => {
      if (inFlight === request) inFlight = null;
    };
    const request = postAnalyze(payload)
      .catch((err) => reportError("analyze isteği başarısız", err))
      .then(release, release);
    inFlight = request;
    return request;
  }

  function flushBuffer() {
    // Skipped while a request is in flight rather than queued behind it: the
    // window is rolling, so the next tick carries this tick's events anyway.
    if (inFlight) return;
    startFlush();
  }

  function nextTask() {
    return new Promise((resolve) => window.setTimeout(resolve, 0));
  }

  // Sends the current window now and resolves once it has been answered, so a
  // host page can ask /api/decision about behaviour the server has actually
  // stored. Never rejects: failures still go to onError, and the decision
  // endpoint is the one that must treat missing evidence as missing.
  function flush() {
    if (!inFlight && (!sessionId || !sessionToken)) return Promise.resolve();
    return (inFlight || Promise.resolve())
      // One task later, so the event that triggered the call is included: a
      // host's click handler runs while that click is still being dispatched,
      // before it reaches the SDK's listener on window.
      .then(nextTask)
      // A tick may have started a request during that task. It carries the
      // same window, so waiting for it is the fresh flush.
      .then(() => inFlight || startFlush())
      .then(
        () => undefined,
        () => undefined
      );
  }

  function attachListeners() {
    // Never both: for a mouse they report the same motion twice.
    moveEventName = typeof window.PointerEvent !== "undefined" ? "pointermove" : "mousemove";
    window.addEventListener(moveEventName, moveEventName === "pointermove" ? onPointerMove : onMouseMove, {
      passive: true,
    });
    window.addEventListener("click", onClick, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("visibilitychange", onVisibilityChange);
    document.addEventListener("keydown", onKeyDown, { passive: true });
  }

  function detachListeners() {
    window.removeEventListener(moveEventName, moveEventName === "pointermove" ? onPointerMove : onMouseMove);
    window.removeEventListener("click", onClick);
    window.removeEventListener("scroll", onScroll);
    document.removeEventListener("visibilitychange", onVisibilityChange);
    document.removeEventListener("keydown", onKeyDown);
  }

  function init(options) {
    if (started) return;
    config = { ...config, ...(options || {}) };
    // Every request is `${config.apiUrl}/api/...`. A trailing slash would make
    // that "//api/..." -- with "/" alone a protocol-relative URL naming a host
    // called "api" -- so trailing slashes are dropped. An explicit
    // `apiUrl: undefined` or null would make it the relative path
    // "undefined/api/..."; it means the same-origin "" instead.
    config.apiUrl = String(config.apiUrl == null ? "" : config.apiUrl)
      .trim()
      .replace(/\/+$/, "");
    state = createState();
    started = true;

    // Listeners first, then registration: behavior from the very first
    // moment is buffered even though it cannot be sent yet.
    attachListeners();
    timerId = window.setInterval(flushBuffer, config.intervalMs);

    registration = register().catch((err) => {
      // The host page must be able to fail CLOSED. A session that was never
      // registered can never be scored, and a missing score is not a clean
      // one.
      reportError("oturum kaydı başarısız", err);
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

  window.DeepCheck = { init, stop, getSessionId, getToken, ready, flush };
})(window);
