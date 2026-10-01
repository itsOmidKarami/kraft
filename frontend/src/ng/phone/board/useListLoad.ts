import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../../../store";

type Load = { state: "loading" | "ok" } | { state: "error"; error: string };

/** The list, read again on mount: the store's boot load may have failed. Offline
 *  while that read failed or the event socket is reconnecting (the desktop
 *  board's rule, kept apart because the desktop's hook is private to its page). */
export function useListLoad() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const connection = useStore((s) => s.connection);
  const refresh = useCallback(() => {
    setLoad((l) => (l.state === "error" ? l : { state: "loading" }));
    useStore.getState().bootstrap().then(
      () => setLoad({ state: "ok" }),
      (e: Error) => setLoad({ state: "error", error: e.message }),
    );
  }, []);
  useEffect(refresh, [refresh]);
  const was = useRef(connection);
  useEffect(() => {
    if (connection === "open" && was.current === "reconnecting") refresh();
    was.current = connection;
  }, [connection, refresh]);
  return { load, refresh, offline: load.state === "error" || connection === "reconnecting" };
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
