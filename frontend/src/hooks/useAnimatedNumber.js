import { useEffect, useRef, useState } from "react";

function easeOutCubic(t) {
  return 1 - Math.pow(1 - t, 3);
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

    const start = performance.now();

    function tick(now) {
      const progress = Math.min((now - start) / duration, 1);
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
