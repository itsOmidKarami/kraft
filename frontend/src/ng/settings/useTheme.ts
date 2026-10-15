import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Theme } from "../../types";
import { applyTheme, lookOf } from "../theme/applyTheme";
import { accentOf } from "../theme/looks";
import { DEFAULT_PREFS } from "../review/prefs";

/** What theme.yaml holds, from the page's own state: the effective value of every control. */
export function themeYaml(theme: Theme): string {
  const scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff = theme.diff ?? DEFAULT_PREFS;
  const amount = theme.colour_amount ?? "subtle";
  return [
    `mode: ${theme.mode}`,
    `surface: ${theme.surface ?? "graphite"}`,
    `accent: ${amount === "mono" ? "none" : accentOf(theme.accent)}`,
    `colour_amount: ${amount}`,
    "code_scheme:",
    `  light: ${scheme.light}`,
    `  dark: ${scheme.dark}`,
    "diff:",
    `  layout: ${diff.layout}`,
    `  colours: ${diff.colours}`,
    `  show_whitespace: ${diff.show_whitespace}`,
    `  word_highlight: ${diff.word_highlight}`,
    `  wrap_lines: ${diff.wrap_lines}`,
    `  one_file_at_a_time: ${diff.one_file_at_a_time}`,
    `density: ${theme.density}`,
    "board:",
    `  open_in: ${theme.board.open_in}`,
    ...(theme.editor ? [`editor: ${theme.editor}`] : []),
  ].join("\n");
}

/** The theme a Settings page edits, saved on change: each control sends only its own key and the
 *  server merges it into theme.yaml. Desktop and phone both use it. The page paints whatever it holds: a change at once, a failed save undone. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getTheme().then(setTheme, (e) => setError(String(e instanceof Error ? e.message : e)));
  }, []);
  useEffect(() => {
    if (theme) applyTheme(lookOf(theme));
  }, [theme]);

  /** Answers null once saved, or why it was refused (a sheet on the phone closes on null). */
  const save = async (patch: Partial<Theme>): Promise<string | null> => {
    if (!theme) return null;
    // A derived look is written out whole on its first change, so the other
    // two keys stop following the default at the same moment (brief E.3).
    const amount = theme.colour_amount ?? "subtle";
    const body = theme.derived ? { surface: theme.surface ?? "graphite", accent: amount === "mono" ? "none" : accentOf(theme.accent), colour_amount: amount, ...patch } : patch;
    const before = theme;
    setTheme({ ...theme, ...body, derived: false });
    setError(null);
    try {
      setTheme(await api.putTheme(body));
      return null;
    } catch (e) {
      setTheme(before);
      const why = e instanceof Error ? e.message : String(e);
      setError(`Not saved: ${why}`);
      return why;
    }
  };
  return { theme, error, save };
}
