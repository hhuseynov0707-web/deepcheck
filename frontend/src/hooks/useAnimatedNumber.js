import { useEffect, useRef, useState } from "react";

function easeOutCubic(t) {
  return 1 - Math.pow(1 - t, 3);
}

// The count-up exists to say "this figure just changed". Someone who has asked
// their system for less motion still needs the new figure -- they get it at
// once instead of over 600ms. matchMedia is missing in jsdom and can throw in
// hardened browsers, so its absence means "no preference expressed".
function prefersReducedMotion() {
  try {
    return (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  } catch {
    return false;
  }
}

export default function useAnimatedNumber(target, duration = 500) {
  const [value, setValue] = useState(target);
  const frameRef = useRef(null);
  // The value currently on screen. A new animation has to start from here
  // rather than from the previous target: the dashboard polls every 3s and
  // animates for 600ms, so targets routinely change mid-flight, and starting
  // from the old target snaps the number forward before it animates back.
  const currentRef = useRef(target);

  useEffect(() => {
    const from = currentRef.current;
    const delta = target - from;
    if (delta === 0) return undefined;

    // duration 0 is a caller asking for no animation at all (a figure driven by
    // a pointer, not by a poll). Without this the progress below is 0/0 = NaN
    // on the first frame and the figure renders as NaN.
    if (duration <= 0 || prefersReducedMotion()) {
      currentRef.current = target;
      setValue(target);
      return undefined;
    }

    const start = performance.now();

    function tick(now) {
      // Clamped at both ends. A frame whose clock reads earlier than the
      // frame that started the animation (fake timers in tests, a suspended
      // tab in a browser) gave a negative progress, and easeOutCubic turns
      // that into a large negative number: the card briefly showed -68 for a
      // value of 1. The figure may never leave the range it is travelling.
      const progress = Math.min(Math.max((now - start) / duration, 0), 1);
      const next = from + delta * easeOutCubic(progress);
      currentRef.current = next;
      setValue(next);

      if (progress < 1) {
        frameRef.current = requestAnimationFrame(tick);
      }
    }

    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [target, duration]);

  return value;
}
