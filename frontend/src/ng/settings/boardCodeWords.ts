import type { BoardOpenIn, Theme } from "../../types";
import type { DiffPrefs } from "../review/prefs";

/** What Board & code offers, in one set of words for the desktop page and the phone screen. */
type Scheme = NonNullable<Theme["code_scheme"]>;
export type SchemeMode = "light" | "dark";

export const SCHEMES: { [M in SchemeMode]: { value: Scheme[M]; label: string }[] } = {
  light: [{ value: "auto", label: "Auto" }, { value: "none", label: "None" }, { value: "solarized-light", label: "Solarized Light" }],
  dark: [{ value: "auto", label: "Auto" }, { value: "none", label: "None" }, { value: "solarized-dark", label: "Solarized Dark" }, { value: "monokai", label: "Monokai" }, { value: "dracula", label: "Dracula" }],
};

export const SCHEME_NOTE = "Colours code in review diffs, chosen separately for light and dark. Auto follows Colour amount; None is a single colour.";

export const LAYOUTS: { value: DiffPrefs["layout"]; label: string }[] = [{ value: "split", label: "Side-by-side" }, { value: "unified", label: "Inline" }];

export const DIFF_COLOURS: { value: DiffPrefs["colours"]; label: string; note: string }[] = [
  { value: "theme", label: "Theme", note: "status colours" },
  { value: "safe", label: "Colour-blind safe", note: "blue, orange" },
  { value: "plain", label: "None", note: "marks only" },
];

export const TOGGLES: { key: "show_whitespace" | "word_highlight" | "wrap_lines" | "one_file_at_a_time"; label: string; sub: string }[] = [
  { key: "show_whitespace", label: "Show whitespace changes", sub: "Off hides lines that differ only in whitespace" },
  { key: "word_highlight", label: "Highlight changed words", sub: "A stronger tint on the words that changed inside a line" },
  { key: "wrap_lines", label: "Wrap long lines", sub: "Off scrolls sideways" },
  { key: "one_file_at_a_time", label: "One file at a time", sub: "The tree stays beside a single file" },
];

export const OPEN_IN: { value: BoardOpenIn; label: string }[] = [{ value: "peek", label: "Peek" }, { value: "full", label: "Full page" }];
