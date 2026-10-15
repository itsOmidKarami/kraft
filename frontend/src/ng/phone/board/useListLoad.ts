import { useCallback, useEffect, useState } from "react";
import { reload, useOffline } from "../../offline";

type Load = { state: "loading" | "ok" } | { state: "error"; error: string };

/** The list, read again on mount: the store's boot load may have failed. Offline is the app's (`useOffline`). */
export function useListLoad() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const refresh = useCallback(() => {
    setLoad((l) => (l.state === "error" ? l : { state: "loading" }));
    reload().then(
      () => setLoad({ state: "ok" }),
      (e: Error) => setLoad({ state: "error", error: e.message }),
    );
  }, []);
  useEffect(refresh, [refresh]);
  return { load, refresh, offline: useOffline().offline };
}

/** A half-minute clock for the rows' ages. */
export function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now;
}
