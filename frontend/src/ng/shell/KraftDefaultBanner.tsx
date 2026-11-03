import { useEffect, useState } from "react";
import * as api from "../../api";
import { notifyServer } from "../../browserNotify";
import type { Health } from "../../types";

const HIDDEN_KEY = "kraft.kraftDefault.hidden";
const NOTIFIED_KEY = "kraft.kraftDefault.notified";
const read = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const write = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A blocked store only means the banner comes back on a reload.
  }
};

/** The kraft-default drift lines while there is drift this browser has not hidden, else null.
 *  Also marks a shown state as seen, and offers a new one to the browser notification. */
export function useKraftDefault(health: Health | null | undefined): { lines: string[]; hide: () => void } | null {
  const signature = health?.kraft_default?.signature ?? null;
  const count = health?.kraft_default?.count ?? 0;
  const [hidden, setHidden] = useState<string | null>(() => read(HIDDEN_KEY));
  const [found, setFound] = useState<{ lines: string[]; signature: string | null } | null>(null);
  const shown = !!signature && count > 0 && hidden !== signature;
  useEffect(() => {
    if (signature) notifyServer(signature, count);
  }, [signature, count]);
  const have = found?.signature === signature;
  // `health` is a new object each poll, so a failed read is asked again a minute later.
  useEffect(() => {
    if (!shown || have) return;
    let live = true;
    api.getKraftDefault().then((r) => live && setFound(r)).catch(() => {});
    return () => {
      live = false;
    };
  }, [shown, signature, have, health]);
  useEffect(() => {
    if (shown && found && found.signature === signature && signature) write(NOTIFIED_KEY, signature);
  }, [shown, found, signature]);
  if (!shown || !found || found.signature !== signature) return null;
  return {
    lines: found.lines,
    hide: () => {
      write(HIDDEN_KEY, signature!);
      setHidden(signature);
    },
  };
}

/** The board's banner while Kraft's own chains drifted: the `kraft admin doctor` row, brought to the operator. */
export function KraftDefaultBanner({ health }: { health: Health | null | undefined }) {
  const drift = useKraftDefault(health);
  if (!drift) return null;
  const more = drift.lines.length - 3;
  return (
    <div className="ng-offline ng-offline-warn ng-offline-tall" role="status">
      <span className="ng-offline-mark" aria-hidden>!</span>
      <div className="ng-offline-text">
        <strong>Kraft's own chains need attention</strong>
        <ul className="ng-offline-lines">
          {drift.lines.slice(0, 3).map((l, i) => <li key={i}>{l}</li>)}
          {more > 0 && <li>and {more} more</li>}
        </ul>
        Run <code>kraft admin doctor</code> for the fix.
      </div>
      <button type="button" className="btn ng-offline-act" onClick={drift.hide}>Hide until it changes</button>
    </div>
  );
}
