import type { Problem } from "../templates/draft/types";

/** The library component a problem is about: the one it comes from, else the one its own path names (a problem in `library.yaml` itself). */
export function componentOf(p: Problem): string | null {
  if (p.component) return p.component;
  if (p.chain || p.repo) return null;
  const [section, name] = p.path.split(".");
  return section && name ? `${section}.${name}` : null;
}
