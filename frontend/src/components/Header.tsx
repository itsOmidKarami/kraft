import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { CaretRight, DotsThree, MagnifyingGlass, Plus } from "@phosphor-icons/react";
import * as api from "../api";
import { SETTINGS_NAV } from "../settingsNav";
import { useStore } from "../store";
import { repoName } from "../format";

/** How many repos are connected — a fresh install has 0 items *and* 0 repos,
 *  which reads differently from "0 items, repos connected" (design 08). Not
 *  derivable from work items alone, so this is its own small fetch, the same
 *  shape `AppNav.tsx`'s `useHealth()` already uses. Refetches on every route
 *  change so returning to the board after connecting a repo from the empty
 *  state's ReposPage picks up the new count instead of staying stuck at 0
 *  until a reload. */
function useRepoCount(): number | null {
  const [n, setN] = useState<number | null>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    api
      .getRepos()
      .then((r) => setN(r.repos.length))
      .catch(() => {});
  }, [pathname]);
  return n;
}

/** `to` set only where Final's own markup draws an `<a href>` — the item
 *  page's "Board" segment (screen 11: `<a href="#04">Board</a>`), so a
 *  reader can jump back without the browser Back button. Every other
 *  breadcrumb segment on every other screen (04, 24) is a plain `<span>`,
 *  including the current page's own name on the board and "Settings" on a
 *  settings page — not every non-final segment is a link. */
type Crumb = { text: string; sub?: string; to?: string };

function useCrumb(
  repoCount: number | null,
  archivedCount: number | null,
): { crumb: Crumb[]; primary: "new" | "more" | null } {
  const { pathname } = useLocation();
  const items = useStore((s) => Object.values(s.workItems));
  const itemMatch = pathname.match(/^\/work-items\/([^/]+)/);
  const item = useStore((s) => (itemMatch ? s.workItems[itemMatch[1]] : undefined));
  const settingsMatch = pathname.match(/^\/settings\/([^/]+)/);

  return useMemo(() => {
    if (pathname === "/") {
      const repos = new Set(items.map((i) => i.repo)).size;
      const sub =
        repoCount === 0 ? "0 work items · no repos" : `${items.length} work items across ${repos} repos`;
      return {
        crumb: [{ text: "Board", sub }],
        primary: "new",
      };
    }
    if (pathname === "/archived") {
      return {
        crumb: [
          { text: "Board", to: "/" },
          { text: `Archived ${archivedCount ?? 0} items` },
        ],
        primary: null,
      };
    }
    if (itemMatch && item) {
      return {
        crumb: [
          { text: repoName(item.repo) },
          { text: "Board", to: "/" },
          { text: itemMatch[1] },
        ],
        primary: "more",
      };
    }
    if (pathname === "/analytics") return { crumb: [{ text: "Analytics" }], primary: null };
    if (settingsMatch) {
      const page = SETTINGS_NAV.find((n) => n.to === settingsMatch[1]);
      return {
        crumb: [{ text: "Settings" }, { text: page?.label ?? settingsMatch[1] }],
        primary: null,
      };
    }
    return { crumb: [{ text: "Board" }], primary: null };
  }, [pathname, items, itemMatch, item, settingsMatch, repoCount, archivedCount]);
}

/** Same small-fetch shape as `useRepoCount` -- the "Archived N items"
 *  breadcrumb (design 07) needs a count the default work-item list, which
 *  excludes archived items, cannot supply. Refetches whenever the store's
 *  archivedVersion bumps (any work_item_archived/restored WS event, not
 *  just clicks made from this tab), so the count on /archived doesn't go
 *  stale while the poller or another tab archives or restores items. */
function useArchivedCount(): number | null {
  const [n, setN] = useState<number | null>(null);
  const archivedVersion = useStore((s) => s.archivedVersion);
  useEffect(() => {
    api
      .listArchivedWorkItems()
      .then((r) => setN(r.items.length))
      .catch(() => {});
  }, [archivedVersion]);
  return n;
}

export function Header({ onSearch, onNew }: { onSearch: () => void; onNew: () => void }) {
  const repoCount = useRepoCount();
  const archivedCount = useArchivedCount();
  const { crumb, primary } = useCrumb(repoCount, archivedCount);

  return (
    <header className="app-header">
      <span className="app-header-crumb">
        {crumb.map((c, i) => (
          <span key={i} className="app-header-crumb-part">
            {i > 0 && <CaretRight size={11} />}
            {c.to ? (
              <Link to={c.to} className="app-header-crumb-link">
                {c.text}
              </Link>
            ) : (
              <span className={i === crumb.length - 1 ? "app-header-crumb-current" : undefined}>
                {c.text}
              </span>
            )}
            {c.sub && <span className="app-header-crumb-sub">{c.sub}</span>}
          </span>
        ))}
      </span>
      <div className="app-header-actions">
        <button className="btn btn-secondary" onClick={onSearch}>
          <MagnifyingGlass size={14} />
          Search
          <span className="kbd">⌘K</span>
        </button>
        {primary === "new" && (
          <button className="btn btn-primary" onClick={onNew} disabled={repoCount === 0}>
            <Plus size={14} />
            New work item
          </button>
        )}
        {primary === "more" && (
          // Wired by the item-detail redesign (handoff README "Suggested
          // order" step 3) — this shell only has to draw it, per screen 11.
          <button className="btn btn-ghost" aria-label="More">
            <DotsThree size={14} />
          </button>
        )}
      </div>
    </header>
  );
}
