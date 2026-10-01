import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Pin } from "lucide-react";
import { Kbd } from "../ui/Kbd";
import { useStore } from "../../store";
import * as api from "../../api";
import type { Health } from "../../types";
import { NAV_ICON } from "../icons";
import { legacyPath } from "../legacyPath";
import { routesIn, type NgRoute } from "./routes";
import { currentSidebar, writeSidebar, type SidebarMode } from "./sidebarPref";

const connectionWord = (c: "connecting" | "open" | "reconnecting") =>
  c === "open" ? "live" : c === "connecting" ? "connecting…" : "reconnecting…";

/** Polled like the shipped sidebar's own `useHealth`. */
function useHealth(): Health | null {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    const load = () => api.getHealth().then(setHealth).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  return health;
}

const isTextField = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

export function Sidebar({ onSearch }: { onSearch?: () => void }) {
  const [mode, setMode] = useState<SidebarMode>(currentSidebar);
  // Escape in a revealed rail closes it even while the pointer is still over it.
  const [dismissed, setDismissed] = useState(false);
  const connection = useStore((s) => s.connection);
  // The server's badge (R16): the board's own Needs you count, failed included.
  const needsYou = useStore((s) => Object.values(s.workItems).filter((i) => i.display_status === "needs_you" || i.display_status === "failed").length);
  const health = useHealth();
  const location = useLocation();
  const ref = useRef<HTMLElement>(null);

  const pick = (next: SidebarMode) => {
    writeSidebar(next);
    setMode(next);
  };
  const toggle = () => pick(mode === "pinned" ? "rail" : "pinned");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "\\" || !(e.metaKey || e.ctrlKey) || isTextField(e.target)) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Escape" || mode === "pinned") return;
    setDismissed(true);
    document.getElementById("ng-main")?.focus();
  };

  const row = (r: NgRoute) => {
    const board = r.path === "/";
    const label = board && needsYou > 0 ? `Board, ${needsYou} need you` : r.label;
    return (
      <NavLink
        key={r.path}
        to={r.path}
        end
        aria-label={label}
        className={({ isActive }) => `ng-side-row${isActive || (board && location.pathname === "/archived") ? " active" : ""}`}
      >
        <r.icon size={16} aria-hidden />
        <span className="ng-side-label" aria-hidden>{r.label}</span>
        {board && needsYou > 0 && <span className="ng-side-dot" aria-hidden />}
      </NavLink>
    );
  };

  const bind = health ? `${health.bind ?? ""}${health.port != null ? `:${health.port}` : ""}` : "";
  const SearchIcon = NAV_ICON.search;

  return (
    <div className="ng-side" data-dismissed={dismissed || undefined} onPointerLeave={() => setDismissed(false)} onFocus={() => setDismissed(false)}>
      <aside ref={ref} className="ng-sidebar" aria-label="Sidebar" onKeyDown={onKeyDown}>
        <div className="ng-side-head">
          <span className={`ng-side-live-dot ${connection === "open" ? "ok" : "warn"}`} role="img" aria-label={connectionWord(connection)} />
          <span className="ng-side-brand ng-side-label">Kraft</span>
          <span className={`ng-side-live ng-side-label ${connection === "open" ? "ok" : "warn"}`}>{connectionWord(connection)}</span>
        </div>
        <nav className="ng-side-nav" aria-label="Pages">
          <button type="button" className="ng-side-row" aria-label="Search" aria-keyshortcuts="Meta+K Control+K" onClick={onSearch}>
            <SearchIcon size={16} aria-hidden />
            <span className="ng-side-label" aria-hidden>Search</span>
            <span className="ng-side-kbd ng-side-label" aria-hidden><Kbd>⌘K</Kbd></span>
          </button>
          {routesIn("top").map(row)}
          <div className="ng-side-group ng-side-group-templates"><span className="ng-side-label">Templates</span></div>
          {routesIn("templates").map(row)}
          <div className="ng-side-group"><span className="ng-side-label">Settings</span></div>
          {routesIn("settings").map(row)}
        </nav>
        <div className="ng-side-foot">
          <NavLink to="/settings/about" end className="ng-side-meta ng-side-label">
            {health ? `${bind}${health.version ? ` · v${health.version}` : ""}` : ""}
          </NavLink>
          <a className="ng-side-meta ng-side-label" href={legacyPath({ pathname: `/ng${location.pathname}`, search: location.search })}>Current UI ↗</a>
          <button
            type="button"
            className="ng-side-pin"
            aria-pressed={mode === "pinned"}
            title={mode === "pinned" ? "Collapse sidebar" : "Pin sidebar"}
            aria-label={mode === "pinned" ? "Collapse sidebar" : "Pin sidebar"}
            onClick={toggle}
          >
            <Pin size={16} aria-hidden />
          </button>
        </div>
      </aside>
    </div>
  );
}
