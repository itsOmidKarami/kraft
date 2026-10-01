import { ChartColumn, Ellipsis, Kanban, Search } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { useApply } from "../../apply/store";
import { useNeedsCount } from "./needsCount";
import { tabOf, type Tab } from "./route";

const TABS: { tab: Tab; label: string; to: string; Icon: typeof Kanban }[] = [
  { tab: "board", label: "Board", to: "/", Icon: Kanban },
  { tab: "search", label: "Search", to: "/search", Icon: Search },
  { tab: "analytics", label: "Analytics", to: "/analytics", Icon: ChartColumn },
  { tab: "more", label: "More", to: "/more", Icon: Ellipsis },
];

/** Board, Search, Analytics, More (W17 brief A.4). A press goes to the tab's root
 *  with `replace`; pressing the tab you are on at its root scrolls to the top. */
export function TabBar() {
  const { pathname, search } = useLocation();
  const active = tabOf(pathname + search);
  const needs = useNeedsCount();
  const pending = useApply((s) => s.restart.length + s.reload.length);
  if (!active) return null;
  return (
    <nav className="ph-tabs" aria-label="Primary">
      {TABS.map(({ tab, label, to, Icon }) => {
        const count = tab === "board" ? needs : 0;
        const dot = tab === "more" && pending > 0;
        const name = count ? `${label}, ${count} need${count === 1 ? "s" : ""} you` : dot ? `${label}, ${pending} pending` : label;
        return (
          <Link
            key={tab}
            to={to}
            replace
            className={`ph-tab${active === tab ? " ph-is-active" : ""}`}
            aria-current={active === tab ? "page" : undefined}
            aria-label={name}
            onClick={() => {
              if (active === tab && pathname === to) document.querySelector(".ph-content")?.scrollTo({ top: 0 });
            }}
          >
            <span className="ph-tab-icon">
              <Icon size={22} aria-hidden="true" />
              {count > 0 && <span className="ph-badge" aria-hidden="true">{count > 99 ? "99+" : count}</span>}
              {dot && <span className="ph-badge ph-badge-dot" aria-hidden="true" />}
            </span>
            <span className="ph-tab-label">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
