import type { PaletteId, ThemeMode } from "./types";

/** Swatch preview colors for the Settings picker — each palette's own dark
 *  bg/accent, shown regardless of the app's current mode. A representative
 *  pair for a chip, not a copy of every token palettes.css defines. */
export const PALETTES: { id: PaletteId; name: string; bg: string; accent: string }[] = [
  { id: "nocturne", name: "Nocturne", bg: "#161826", accent: "#9184d9" },
  { id: "rose", name: "Rose", bg: "#1e1522", accent: "#b876bc" },
  { id: "forest", name: "Forest", bg: "#131c10", accent: "#53a768" },
  { id: "amber", name: "Amber", bg: "#24150f", accent: "#cc7c40" },
  { id: "slate", name: "Slate", bg: "#081d21", accent: "#04a1cb" },
];

let systemQuery: MediaQueryList | null = null;
let systemListener: ((e: MediaQueryListEvent) => void) | null = null;

function teardownSystemListener(): void {
  if (systemQuery && systemListener) {
    systemQuery.removeEventListener("change", systemListener);
  }
  systemQuery = null;
  systemListener = null;
}

/** Sets `data-palette`/`data-mode` on `<html>` — palettes.css (plus
 *  nocturne.css's unscoped `:root` for Nocturne dark, the default) does
 *  the actual repaint; nothing here touches an individual CSS variable.
 *  `"system"` resolves once against `prefers-color-scheme` and keeps a
 *  listener alive for as long as `"system"` stays selected, so the page
 *  follows an OS-level theme change live instead of only on next load. */
export function applyTheme(palette: PaletteId, mode: ThemeMode): void {
  const root = document.documentElement;
  root.dataset.palette = palette;

  teardownSystemListener();

  if (mode === "system") {
    systemQuery = window.matchMedia("(prefers-color-scheme: dark)");
    root.dataset.mode = systemQuery.matches ? "dark" : "light";
    systemListener = (e) => {
      root.dataset.mode = e.matches ? "dark" : "light";
    };
    systemQuery.addEventListener("change", systemListener);
  } else {
    root.dataset.mode = mode;
  }
}

export function applyDensity(density: "compact" | "comfortable"): void {
  document.documentElement.dataset.density = density;
}
