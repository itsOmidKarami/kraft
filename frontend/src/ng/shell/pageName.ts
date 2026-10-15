import { repoName, shortId } from "../../format";
import { nodeWords } from "../board/rowText";
import type { CrumbItem } from "./pageItem";
import { ROUTES } from "./routes";

type ItemLookup = (id: string) => Pick<CrumbItem, "title"> & Partial<CrumbItem> | undefined;

export const APP_NAME = "Kraft";

/** Pages that exist on the phone only (or whose desktop twin has another path). */
const EXTRA: Record<string, string> = { "/search": "Search", "/more": "More", "/work-items/new": "New work item" };

const dec = decodeURIComponent;

/** What a person calls the page at an address: the one source for the tab title,
 *  the phone's Back label and the not-found page's way up, desktop and phone alike.
 *  The query is ignored, so an overlay (a document, the YAML) names its screen. */
export function pageName(href: string, item: ItemLookup = () => undefined): string {
  const [pathname, query = ""] = href.split("#")[0].split("?");
  if (EXTRA[pathname]) return EXTRA[pathname];
  const wi = /^\/work-items\/([^/]+)(?:\/nodes\/([^/]+)|\/(review))?$/.exec(pathname);
  if (wi) {
    // A task is the node route with `sel` naming it (node.step.task).
    const task = new URLSearchParams(query).get("sel")?.split(".").at(-1);
    if (wi[2]) return nodeWords(task || dec(wi[2]));
    if (wi[3]) return "Review";
    const id = dec(wi[1]);
    const it = item(id);
    return it?.title || it?.bead_id || shortId(id);
  }
  const seg = /^\/(?:templates\/(chains|library)|settings\/(repos|harnesses|auto-intake|notifications))\/(?:(?:profiles|schedules)\/)?([^/]+)(?:\/nodes\/([^/]+))?$/.exec(pathname);
  if (seg) {
    const [, , , name, node] = seg;
    if (node) return nodeWords(dec(node));
    if (pathname.includes("/schedules/")) return `Schedule ${Number(name) + 1}`;
    return seg[2] === "repos" ? repoName(dec(name)) : dec(name);
  }
  const base = pathname.replace(/^(\/settings\/policy)\/.+$/, "$1");
  return ROUTES.find((r) => r.path === base)?.label ?? "Not found";
}

/** The tab title: "<page> · Kraft", an item's pages led by its title, the
 *  board's Needs you count in front when it is not zero. */
export function pageTitle(href: string, item: ItemLookup, needs = 0): string {
  const pathname = href.split(/[?#]/)[0];
  const wi = /^\/work-items\/([^/]+)\/(?:nodes|review)/.exec(pathname);
  const parts = [pageName(href, item)];
  if (wi) parts.push(pageName(`/work-items/${wi[1]}`, item));
  const lead = needs > 0 ? `(${needs}) ` : "";
  return `${lead}${parts.join(" · ")} · ${APP_NAME}`;
}

/** The nearest page above an address that exists, for a not-found page to offer:
 *  the longest known prefix, else the section's landing page (the address
 *  `/templates` and `/settings` themselves redirect to). */
export function nearestPage(pathname: string): { to: string; label: string } {
  const segs = pathname.split("/").filter(Boolean);
  for (let n = segs.length - 1; n > 0; n--) {
    const to = `/${segs.slice(0, n).join("/")}`;
    const r = ROUTES.find((x) => x.path === to);
    if (r) return { to, label: r.label };
  }
  if (segs[0] === "templates") return { to: "/templates/chains", label: "Chains" };
  if (segs[0] === "settings") return { to: "/settings/policy", label: "Policy" };
  return { to: "/", label: "Board" };
}
