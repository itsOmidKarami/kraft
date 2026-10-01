import { repoName } from "../../format";
import { ROUTES } from "./routes";

export interface Crumb {
  text: string;
  /** A page inside /ng. */
  to?: string;
  /** A page on the shipped UI, reached by a full load. */
  href?: string;
  title?: string;
  kind: "repo" | "mid" | "current";
}

type ItemLookup = (id: string) => { repo: string; title: string } | undefined;

const SHIPPED_BOARD = "/";
const GROUP_HEAD = { templates: "Templates", settings: "Settings" } as const;

/** The crumbs for a pathname under /ng. "Templates" and "Settings" have no page
 *  of their own, so they are text. */
export function crumbsFor(pathname: string, item: ItemLookup): Crumb[] {
  const current = (text: string): Crumb => ({ text, kind: "current", title: text });
  const mid = (text: string, extra: Partial<Crumb> = {}): Crumb => ({ text, kind: "mid", ...extra });

  const wi = /^\/work-items\/([^/]+)/.exec(pathname);
  if (wi) {
    const id = decodeURIComponent(wi[1]);
    const it = item(id);
    if (!it) return [mid("Board", { href: SHIPPED_BOARD }), current(id)];
    const repo = repoName(it.repo);
    return [{ text: repo, kind: "repo", title: it.repo }, mid("Board", { href: SHIPPED_BOARD }), current(it.title)];
  }
  if (pathname === "/archived") return [mid("Board", { href: SHIPPED_BOARD }), current("Archived")];

  const route = ROUTES.find((r) => r.path === pathname);
  if (route?.group === "templates" || route?.group === "settings" || pathname === "/settings/about")
    return [mid(GROUP_HEAD[pathname.startsWith("/templates") ? "templates" : "settings"]), current(route!.label)];
  if (route) return [current(route.label)];
  return [current(pathname === "/_tokens" ? "Tokens" : "Not found")];
}
