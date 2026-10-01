import { repoName, shortId } from "../../format";
import type { CrumbItem } from "./pageItem";
import { ROUTES } from "./routes";

export interface Crumb {
  text: string;
  /** A page inside /ng. */
  to?: string;
  /** A page on the shipped UI, reached by a full load. */
  href?: string;
  title?: string;
  /** `ext`: a link out of Kraft after the crumbs (the item's merge request), opened in a new tab. */
  kind: "repo" | "mid" | "current" | "ext";
}

type ItemLookup = (id: string) => Pick<CrumbItem, "repo" | "title"> & Partial<CrumbItem> | undefined;

const SHIPPED_BOARD = "/";
const GROUP_HEAD = { templates: "Templates", settings: "Settings" } as const;

/** The crumbs for a pathname under /ng. "Templates" and "Settings" have no page
 *  of their own, so they are text. */
export function crumbsFor(pathname: string, item: ItemLookup): Crumb[] {
  const current = (text: string): Crumb => ({ text, kind: "current", title: text });
  const mid = (text: string, extra: Partial<Crumb> = {}): Crumb => ({ text, kind: "mid", ...extra });

  // Board › repo › bead id (else the short id), then the node in a node view
  // (Decisions §1, §5, §14). The title is the page's own h1, never a crumb.
  const wi = /^\/work-items\/([^/]+)(?:\/nodes\/([^/]+))?/.exec(pathname);
  if (wi) {
    const id = decodeURIComponent(wi[1]);
    const node = wi[2] && decodeURIComponent(wi[2]);
    const it = item(id);
    const name = it?.bead_id || shortId(id);
    const out: Crumb[] = [mid("Board", { href: SHIPPED_BOARD })];
    if (it) out.push({ text: repoName(it.repo), kind: "repo", title: it.repo });
    out.push(node ? mid(name, { to: `/work-items/${encodeURIComponent(id)}`, title: id }) : { text: name, kind: "current", title: id });
    if (node) out.push(current(node));
    if (it?.mr_ref) out.push({ text: `!${it.mr_ref.number}${it.display_status === "done" || it.display_status === "archived" ? " merged" : ""} ↗`, kind: "ext", href: it.mr_ref.url, title: it.mr_ref.url });
    return out;
  }
  if (pathname === "/archived") return [mid("Board", { href: SHIPPED_BOARD }), current("Archived")];

  const route = ROUTES.find((r) => r.path === pathname);
  if (route?.group === "templates" || route?.group === "settings" || pathname === "/settings/about")
    return [mid(GROUP_HEAD[pathname.startsWith("/templates") ? "templates" : "settings"]), current(route!.label)];
  if (route) return [current(route.label)];
  return [current(pathname === "/_tokens" ? "Tokens" : "Not found")];
}
