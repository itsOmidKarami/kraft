import type { Accent, ColourAmount, Surface, Theme, ThemeMode } from "../../types";

/** The part of `theme.yaml` the new UI paints from. */
export interface Look {
  surface: Surface;
  accent: Accent;
  colour_amount: ColourAmount;
  mode: ThemeMode;
  density: Theme["density"];
  /** The named code scheme per mode; `auto` and `none` paint no `--tok-*`. */
  code_scheme?: Theme["code_scheme"];
}

/** What the page paints when nothing is cached and the server has not answered. */
export const DEFAULT_LOOK: Look = { surface: "graphite", accent: "none", colour_amount: "subtle", mode: "dark", density: "compact" };

// Not the shipped UI's `kraft.theme`: the two UIs keep their own copy.
const KEY = "kraft.theme.v2";

/** The look the last `applyTheme` wrote, so boot can paint it before `GET /theme`. */
export function cachedLook(): Look | null {
  try {
    const t = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return t && typeof t.surface === "string" && typeof t.mode === "string" ? { ...DEFAULT_LOOK, ...t } : null;
  } catch {
    return null;
  }
}

/** The look in a `GET /theme` answer (which always fills the V2 keys). */
export function lookOf(t: Theme): Look {
  return {
    surface: t.surface ?? DEFAULT_LOOK.surface,
    accent: t.accent ?? DEFAULT_LOOK.accent,
    colour_amount: t.colour_amount ?? DEFAULT_LOOK.colour_amount,
    mode: t.mode,
    density: t.density,
    code_scheme: t.code_scheme,
  };
}

let stopFollowing: (() => void) | null = null;

/** Sets the data attributes theme.css keys on and caches the look. `system`
 *  follows `prefers-color-scheme` live until a later call picks a mode. */
export function applyTheme(look: Look): void {
  const root = document.documentElement.dataset;
  root.surface = look.surface;
  root.accent = look.accent;
  root.amount = look.colour_amount;
  root.density = look.density;
  try {
    localStorage.setItem(KEY, JSON.stringify(look));
  } catch {
    /* storage blocked (a private window): the next load waits for the server */
  }
  stopFollowing?.();
  stopFollowing = null;
  // The scheme belongs to the resolved mode, so it is set where the mode is.
  const setMode = (mode: "light" | "dark") => {
    root.mode = mode;
    const id: string | undefined = look.code_scheme?.[mode];
    if (id && id !== "auto" && id !== "none") root.code = id;
    else delete root.code;
  };
  if (look.mode !== "system") {
    setMode(look.mode);
    return;
  }
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const follow = () => setMode(query.matches ? "dark" : "light");
  follow();
  query.addEventListener("change", follow);
  stopFollowing = () => query.removeEventListener("change", follow);
}
