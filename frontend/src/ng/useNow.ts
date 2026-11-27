import { useEffect, useState } from "react";

/** A half-minute clock, so an age or a countdown ("next check in 3m") moves without new data. */
export function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now;
}
