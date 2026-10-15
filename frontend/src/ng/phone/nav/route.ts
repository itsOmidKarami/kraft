/** Where a phone location sits in the stack (W17 brief A.3, A.4). The URL is
 *  the stack: every screen's parent is a pure function of its own address. */

import { pageName } from "../../shell/pageName";

export type Tab = "board" | "search" | "analytics" | "more";

const AREA = /^\/(?:templates\/(?:chains|library)|settings\/(?:repos|harnesses|policy|auto-intake|notifications|access|appearance|storage|about))/;

const split = (href: string) => {
  const [pathname, query = ""] = href.split("?");
  return { pathname, q: new URLSearchParams(query) };
};
const join = (pathname: string, q: URLSearchParams) => (q.size ? `${pathname}?${q}` : pathname);

/** The address Back goes to, or null on a root (a tab's own page). */
export function parentOf(href: string): string | null {
  const { pathname, q } = split(href);
  // A document, an attachment (a spec or plan read before start), a composer or the YAML view sits over its screen (R10b-10).
  for (const over of ["compose", "doc", "attached", "yaml"]) {
    if (q.has(over)) {
      q.delete(over);
      return join(pathname, q);
    }
  }
  const seg = pathname.split("/").filter(Boolean);
  if (!seg.length || (seg.length === 1 && ["search", "analytics", "more"].includes(seg[0]))) return null;
  if (pathname === "/archived") return "/more";
  if (seg[0] === "work-items") {
    if (seg[1] === "new" || seg.length === 2) return "/";
    if (seg[2] === "review") return `/work-items/${seg[1]}`;
    if (seg[2] === "nodes") {
      // A task is the node route with `sel` naming a task (node.step.task); anything else is the node.
      // A scope of a changed-test-scope task is a screen over that task.
      if (q.has("sel") && q.get("scope")) {
        q.delete("scope");
        q.delete("tab");
        return join(pathname, q);
      }
      if (q.has("sel")) {
        q.delete("sel");
        q.delete("attempt");
        q.delete("scope");
        q.delete("tab");
        return join(pathname, q);
      }
      return `/work-items/${seg[1]}`;
    }
    return `/work-items/${seg[1]}`;
  }
  if (AREA.test(pathname)) {
    const area = AREA.exec(pathname)![0];
    const rest = pathname.slice(area.length).split("/").filter(Boolean);
    // Policy's section is a tab of one screen, not a page.
    if (area === "/settings/policy" || !rest.length) return "/more";
    if (rest.length >= 2 && rest[rest.length - 2] === "nodes") return `${area}${rest.slice(0, -2).map((s) => `/${s}`).join("")}`;
    if (rest.length >= 2 && (rest[0] === "profiles" || rest[0] === "schedules")) return area;
    return `${area}${rest.slice(0, -1).map((s) => `/${s}`).join("")}`;
  }
  return "/";
}

/** The label of the Back control: the name of the screen it goes to, never "Back" or a generic word. */
export function backLabel(href: string, item?: Parameters<typeof pageName>[1]): string {
  const parent = parentOf(href);
  return parent ? pageName(parent, item) : "Board";
}

/** The tab a location belongs to; null when the tab bar is hidden (item, node, task, review, new item). */
export function tabOf(href: string): Tab | null {
  const { pathname } = split(href);
  if (pathname === "/" || pathname === "") return "board";
  if (pathname === "/search") return "search";
  if (pathname === "/analytics") return "analytics";
  if (pathname === "/more" || pathname === "/archived" || AREA.test(pathname)) return "more";
  return null;
}

/** Whether the tab bar is out of the way: an item, its nodes, its review and the new-item page fill the screen. */
export const tabsHidden = (href: string) => split(href).pathname.startsWith("/work-items");

/** `pathname`, `sel` and a task's `scope` only: whether two addresses are the same screen. */
export const screenKey = (href: string) => {
  const { pathname, q } = split(href);
  return `${pathname}${q.get("sel") ? `#${q.get("sel")}${q.get("scope") ? `#${q.get("scope")}` : ""}` : ""}`;
};
