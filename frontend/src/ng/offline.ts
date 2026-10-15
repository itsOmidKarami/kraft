import { useEffect, useRef } from "react";
import type { Connection } from "../store";
import { useStore } from "../store";

/** The one offline state both trees read: the list read failed, or the event
 *  socket is reconnecting. Nothing page-specific, so every page can say it. */
export function useOffline() {
  const loadError = useStore((s) => s.loadError);
  const connection = useStore((s) => s.connection);
  return { offline: loadError !== null || connection === "reconnecting", loadError };
}

/** The sidebar's cue: "live" only while the socket is open and the last list read worked. */
export const connectionWord = (c: Connection, loadError: string | null) =>
  loadError !== null ? "offline" : c === "open" ? "live" : c === "connecting" ? "connecting…" : "reconnecting…";

/** The read's error without its request line ("(GET /work-items?…)"): the banner says what broke, not which call. */
const bannerError = (e: string) => e.replace(/ \((?:GET|POST|PUT|PATCH|DELETE) [^)]*\)/, "").replace(/\.$/, "");

/** The banner's words, desktop and phone alike. */
export const offlineWords = (loadError: string | null) =>
  `Offline: ${loadError ? bannerError(loadError) : "the live connection dropped"}. What is shown may be out of date; changes are off until it reconnects.`;

/** Reads the list again. */
export const reload = () => useStore.getState().bootstrap();

/** Reads again when the socket comes back from a drop, or opens after a failed read. */
export function useReloadOnReconnect() {
  const connection = useStore((s) => s.connection);
  const failed = useStore((s) => s.loadError !== null);
  const was = useRef(connection);
  useEffect(() => {
    if (connection === "open" && (was.current === "reconnecting" || failed)) reload().catch(() => {});
    was.current = connection;
    // `failed` is read at the moment the socket opens, not a trigger: a read that fails while open must not loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection]);
}

/** Runs `fn` once when the app goes from offline back to online (the latest `fn` of that render). */
export function useOnRecovered(fn: () => void) {
  const { offline } = useOffline();
  const was = useRef(offline);
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    if (was.current && !offline) latest.current();
    was.current = offline;
  }, [offline]);
}
