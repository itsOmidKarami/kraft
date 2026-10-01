import type { LucideIcon } from "lucide-react";

// Every Lucide icon, by its file name (the kebab name `icon:` takes and the
// server's lucide_icons.txt lists). Eager inside this module, which is itself
// loaded lazily (iconSet.ts), so the whole set is one chunk off the main path.
const files = import.meta.glob<LucideIcon>(["../../node_modules/lucide-react/dist/esm/icons/*.mjs", "!../../node_modules/lucide-react/dist/esm/icons/index.mjs"], { eager: true, import: "default" });

export const ICONS: Record<string, LucideIcon> = Object.fromEntries(
  Object.entries(files).map(([path, icon]) => [path.slice(path.lastIndexOf("/") + 1, -".mjs".length), icon]),
);
