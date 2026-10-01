import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Theme } from "../../types";

export type BoardPrefs = Theme["board"];

/** `theme.yaml`'s defaults (`config.BoardPrefs`), used until `GET /theme` answers or when it fails. */
export const DEFAULT_PREFS: BoardPrefs = { group_by: "status", show_done: 5, open_in: "peek" };

let cached: Promise<BoardPrefs> | null = null;

/** Appearance › Board (`theme.board`): Group, the Done cap and Open in. Read
 *  once per page load; W16 writes it. */
export function useBoardPrefs(): BoardPrefs | null {
  const [prefs, setPrefs] = useState<BoardPrefs | null>(null);
  useEffect(() => {
    cached ??= api.getTheme().then((t) => ({ ...DEFAULT_PREFS, ...t.board })).catch(() => DEFAULT_PREFS);
    let live = true;
    cached.then((p) => live && setPrefs(p));
    return () => void (live = false);
  }, []);
  return prefs;
}

/** Tests only: forget the read so the next mount asks again. */
export const resetBoardPrefs = () => void (cached = null);
