import { useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { CaretRight, DotsThree, MagnifyingGlass, Plus } from "@phosphor-icons/react";
import { SETTINGS_NAV } from "../settingsNav";
import { useStore } from "../store";
import { repoName } from "../format";

/** `to` set only where Final's own markup draws an `<a href>` — the item
 *  page's "Board" segment (screen 11: `<a href="#04">Board</a>`), so a
 *  reader can jump back without the browser Back button. Every other
 *  breadcrumb segment on every other screen (04, 24) is a plain `<span>`,
 *  including the current page's own name on the board and "Settings" on a
 *  settings page — not every non-final segment is a link. */
type Crumb = { text: string; sub?: string; to?: string };

function useCrumb(): { crumb: Crumb[]; primary: "new" | "more" | null } {
  const { pathname } = useLocation();
  const items = useStore((s) => Object.values(s.workItems));
  const itemMatch = pathname.match(/^\/work-items\/([^/]+)/);
  const item = useStore((s) => (itemMatch ? s.workItems[itemMatch[1]] : undefined));
  const settingsMatch = pathname.match(/^\/settings\/([^/]+)/);

  return useMemo(() => {
    if (pathname === "/") {
      const repos = new Set(items.map((i) => i.repo)).size;
      return {
        crumb: [{ text: "Board", sub: `${items.length} work items across ${repos} repos` }],
        primary: "new",
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
  }, [pathname, items, itemMatch, item, settingsMatch]);
}

export function Header({ onSearch, onNew }: { onSearch: () => void; onNew: () => void }) {
  const { crumb, primary } = useCrumb();

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
          <button className="btn btn-primary" onClick={onNew}>
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
