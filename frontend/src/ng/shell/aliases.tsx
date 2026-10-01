import { useEffect } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

/** The shipped UI's addresses that moved, and where each lands (spec §11.2),
 *  so a bookmark made before the cutover still opens its page. The query
 *  string is kept. `/settings`, `/settings/intake` and the pages whose path
 *  did not change are routes of their own. */
export const ALIASES: [from: string, to: string][] = [
  ["/search", "/"],
  ["/settings/repos/*", "/templates/repos"],
  ["/settings/chains/*", "/templates/chains"],
  ["/settings/library/*", "/templates/library"],
  ["/settings/harnesses/*", "/templates/harnesses"],
  ["/settings/templates/*", "/templates/chains"],
  ["/settings/plugins/*", "/templates/chains"],
  ["/settings/steering/*", "/templates/library"],
  ["/settings/notify/*", "/settings/notifications"],
];

export function Alias({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={to + search} replace />;
}

/** Where a shipped item-page hash (`#node=X&tab=changes&file=…`) lands, or
 *  null when the address has none: the Changes tab is the review page, a
 *  node is the node view, any other tab or selection is the item page. */
export function shippedHash(path: string, hash: string): string | null {
  const item = /^\/work-items\/([^/]+)\/?$/.exec(path);
  if (!item || hash.length < 2) return null;
  const params = new URLSearchParams(hash.slice(1));
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
