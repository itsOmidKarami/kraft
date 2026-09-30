import type { Accent, ColourAmount, Surface, ThemeMode } from "../../types";

/** The choices Appearance offers, in the prototype's order and words. */
export const SURFACES: { id: Surface; name: string; note: string }[] = [
  { id: "graphite", name: "Graphite", note: "neutral grey" },
  { id: "slate", name: "Slate", note: "cool grey" },
  { id: "ink", name: "Ink", note: "Nocturne, muted" },
  { id: "sand", name: "Sand", note: "warm grey" },
  { id: "moss", name: "Moss", note: "green grey" },
];
export const ACCENTS: Accent[] = ["none", "blue", "violet", "green", "amber", "rose"];
export const AMOUNTS: { value: ColourAmount; label: string }[] = [
  { value: "mono", label: "Mono" },
  { value: "subtle", label: "Subtle" },
  { value: "full", label: "Full" },
];

export const title = (s: string) => s[0].toUpperCase() + s.slice(1);

/** The mode theme.css keys on: `system` is whatever <html> resolved it to. */
export const paintedMode = () => (document.documentElement.dataset.mode === "light" ? "light" : "dark");

/** The mode a choice paints in, `system` asked of the operating system. */
export const resolveMode = (m: ThemeMode) =>
  m !== "system" ? m : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
