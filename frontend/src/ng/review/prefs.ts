import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Theme } from "../../types";
import { detailOf, jsonBody, request } from "../http";

export type DiffPrefs = NonNullable<Theme["diff"]>;

/** The server's defaults (`config.DiffPrefs`), until `/theme` answers. */
export const DEFAULT_PREFS: DiffPrefs = { layout: "unified", colours: "theme", show_whitespace: true, word_highlight: true, wrap_lines: false, one_file_at_a_time: true };

/** The review diff's preferences from `theme.yaml` (B30), and a setter that
 *  saves on change (Decisions §13). `PUT /theme` merges top-level keys only,
 *  so every save sends the whole `diff` object. `plainCode`: the scheme for
 *  the current mode is `none`. */
export function useDiffPrefs() {
  const [prefs, setPrefs] = useState<DiffPrefs>(DEFAULT_PREFS);
  const [scheme, setScheme] = useState<Theme["code_scheme"]>();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.getTheme().then(
      (t) => {
        if (!live) return;
        if (t.diff) setPrefs(t.diff);
        setScheme(t.code_scheme);
      },
      () => {},
    );
    return () => void (live = false);
  }, []);
  const set = async (patch: Partial<DiffPrefs>) => {
    const before = prefs;
    const next = { ...prefs, ...patch };
    setPrefs(next);
    const { status, body } = await request("/theme", jsonBody("PUT", { diff: next }));
    if (status === 200) return setError(null);
    setPrefs(before);
    setError(detailOf(body));
  };
  const mode = typeof document !== "undefined" && document.documentElement.dataset.mode === "light" ? "light" : "dark";
  return { prefs, set, error, plainCode: scheme?.[mode] === "none" };
}
