import { useEffect } from "react";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

/** The shipped UI's addresses that moved, and where each lands (spec §11.2),
 *  so a bookmark made before the cutover still opens its page. The query
 *  string is kept. `/settings` and the pages whose path did not change are
 *  routes of their own. Repos and Harnesses are Settings, as they were in
 *  1.4; the `/templates/...` addresses the 2.0 candidates gave them land
 *  there too. */
export const ALIASES: [from: string, to: string][] = [
  ["/search", "/"],
  ["/settings/intake", "/settings/auto-intake"],
  ["/templates/repos/:repo", "/settings/repos/:repo"],
  ["/templates/repos/*", "/settings/repos"],
  ["/settings/chains/*", "/templates/chains"],
  ["/settings/library/*", "/templates/library"],
  ["/templates/harnesses/profiles/:name", "/settings/harnesses?profile=:name"],
  ["/templates/harnesses/:id", "/settings/harnesses?harness=:id"],
  ["/templates/harnesses/*", "/settings/harnesses"],
  ["/settings/templates/*", "/templates/chains"],
  ["/settings/plugins/*", "/templates/chains"],
  ["/settings/steering/*", "/templates/library"],
  ["/settings/notify/*", "/settings/notifications"],
];

/** The phone's screens the desktop has no page for, and the desktop page each
 *  lands on. Both shapes share one address space, so widening the window, or
 *  opening a link sent from a phone, must never end on Not found. A `:param`
 *  in the target is filled from the address. */
export const PHONE_ONLY: [from: string, to: string][] = [
  ["/more", "/"],
  ["/settings/harnesses/profiles/:name", "/settings/harnesses?profile=:name"],
  ["/settings/harnesses/:id", "/settings/harnesses?harness=:id"],
  ["/settings/auto-intake/schedules/:index", "/settings/auto-intake"],
  ["/settings/notifications/:channel", "/settings/notifications"],
];

/** Where an alias lands: its target with each `:param` filled from the
 *  address, then the address's own query, joined onto the target's. */
export function aliasTarget(to: string, params: Record<string, string | undefined>, search: string): string {
  const filled = to.replace(/:(\w+)/g, (_, k: string) => encodeURIComponent(params[k] ?? ""));
  if (search.length < 2) return filled;
  return filled.includes("?") ? `${filled}&${search.slice(1)}` : filled + search;
}

export function Alias({ to }: { to: string }) {
  const { search } = useLocation();
  const params = useParams();
  return <Navigate to={aliasTarget(to, params, search)} replace />;
}

/** The keys the shipped item page kept in its hash (`useItemUrlState`). */
const SHIPPED_KEYS = ["node", "tab", "session", "file", "doc", "tnode", "max"];

/** Where a shipped item-page hash (`#node=X&tab=changes&file=…`) lands, or
 *  null when the hash is not one: the Changes tab is the review page, a node
 *  is the node view, any other tab or selection is the item page. A hash with
 *  none of the shipped keys (an anchor) is left alone. */
export function shippedHash(path: string, hash: string): string | null {
  const item = /^\/work-items\/([^/]+)\/?$/.exec(path);
  if (!item || hash.length < 2) return null;
  const params = new URLSearchParams(hash.slice(1));
  if (!SHIPPED_KEYS.some((k) => params.has(k))) return null;
  const base = `/work-items/${item[1]}`;
  if (params.get("tab") === "changes") return `${base}/review`;
  const node = params.get("node");
  return node ? `${base}/nodes/${encodeURIComponent(node)}` : base;
}

/** Applies `shippedHash` in place of the address, with no history entry. */
export function ShippedHash() {
  const { pathname, search, hash } = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    const to = shippedHash(pathname, hash);
    if (to) navigate(to + search, { replace: true });
  }, [pathname, search, hash, navigate]);
  return null;
}
