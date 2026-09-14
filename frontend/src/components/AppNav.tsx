import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  CaretDown,
  CaretRight,
  ChartBar,
  Gear,
  Kanban,
  SidebarSimple,
} from "@phosphor-icons/react";
import * as api from "../api";
import { SETTINGS_GROUP_LABEL, SETTINGS_NAV } from "../settingsNav";
import { useItemStates, useStore } from "../store";
import { repoName } from "../format";
import { HealthBadge } from "./HealthBadge";
import type { Health } from "../types";

const COLLAPSE_KEY = "kraft.sidebar_collapsed";
const SETTINGS_OPEN_KEY = "kraft.sidebar_settings_open";

/** Under 1280 the sidebar is a rail by default (UI v3 · 45). An explicit
 *  choice still wins: only an absent localStorage key falls through to the
 *  width. */
function readCollapsed(): boolean {
  const stored = localStorage.getItem(COLLAPSE_KEY);
  if (stored !== null) return stored === "true";
  return Boolean(window.matchMedia?.("(max-width: 1279px)")?.matches);
}

/** The item's repo initial on an item page ("a" for repo-a, design 11); "K"
 *  everywhere else (design 03). Only the collapsed rail's top slot swaps —
 *  the expanded sidebar always reads "Kraft" (designs 01/02/04). */
function useRailAvatar(): string {
  const { pathname } = useLocation();
  const match = pathname.match(/^\/work-items\/([^/]+)/);
  const item = useStore((s) => (match ? s.workItems[match[1]] : undefined));
  if (match && item) return repoName(item.repo).charAt(0);
  return "K";
}

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

/** "live" / "connecting…" / "reconnecting…" — folds `ConnBadge`'s three
 *  states into the sidebar's pill (design 01/02/04: always visible, not
 *  conditionally hidden when open). */
function connectionWord(c: "connecting" | "open" | "reconnecting"): string {
  if (c === "open") return "live";
  return c === "connecting" ? "connecting…" : "reconnecting…";
}

export function AppNav() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  // W4.8: the open Settings group survives a reload, like the rail choice.
  const [settingsOpen, setSettingsOpen] = useState(() => localStorage.getItem(SETTINGS_OPEN_KEY) === "true");
  useEffect(() => {
    localStorage.setItem(SETTINGS_OPEN_KEY, String(settingsOpen));
  }, [settingsOpen]);
  const { pathname } = useLocation();
  const connection = useStore((s) => s.connection);
  const items = useStore((s) => Object.values(s.workItems));
  const health = useHealth();
  const avatar = useRailAvatar();
  const onSettings = pathname.startsWith("/settings");
  // "/" is a prefix of every path, so NavLink's own (non-`end`) matching
  // can't tell "on the board" from "on an item page" apart from everything
  // else the way it can for `/analytics`/`/settings` (nothing else starts
  // with those). Board owns work-item pages too (screen 11: the rail's
  // Kanban icon is the active one while viewing an item) — compute it by
  // hand instead of relying on NavLink's `isActive`.
  const boardActive = pathname === "/" || pathname.startsWith("/work-items/");

  useEffect(() => {
    if (onSettings) setSettingsOpen(true);
  }, [onSettings]);

  // An escalating item is running, not waiting on you (W11 · J.3).
  const stateOf = useItemStates();
  const needsYouCount = items.filter((i) => stateOf(i).needsYou).length;
  const runningCount = items.filter((i) =>
    ["running", "rate_limited", "waiting", "escalating"].includes(stateOf(i).state),
  ).length;

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(COLLAPSE_KEY, String(next));
  };

  const bindLine =
    health == null
      ? ""
      : `${health.bind ?? ""}${health.port != null ? `:${health.port}` : ""}${
          health.version ? ` · v${health.version}` : ""
        }`;

  if (collapsed) {
    return (
      <aside className="app-rail">
        <span className="rail-avatar">{avatar}</span>
        <NavLink to="/" className={`rail-btn${boardActive ? " active" : ""}`} title="Board">
          <Kanban />
          {needsYouCount > 0 && <span className="rail-dot" />}
        </NavLink>
        <NavLink to="/analytics" className="rail-btn" title="Analytics">
          <ChartBar />
        </NavLink>
        <NavLink to="/settings" className="rail-btn" title="Settings">
          <Gear />
        </NavLink>
        <span className="rail-divider" />
        {SETTINGS_NAV.filter((n) => n.group === "how").map((n) => (
          <NavLink key={n.to} to={`/settings/${n.to}`} className="rail-btn rail-btn-sm" title={n.label}>
            <n.icon />
          </NavLink>
        ))}
        <button className="rail-expand" title="Expand" aria-label="Expand" onClick={toggle}>
          <SidebarSimple />
        </button>
      </aside>
    );
  }

  return (
    <aside className="app-sidebar">
      <div className="app-sidebar-top">
        <span className="app-sidebar-brand">Kraft</span>
        <span className="app-sidebar-live">{connectionWord(connection)}</span>
        <button className="app-sidebar-collapse" title="Collapse" aria-label="Collapse" onClick={toggle}>
          <SidebarSimple />
        </button>
      </div>
      <nav className="app-sidebar-nav">
        <NavLink to="/" className={`app-sidebar-row${boardActive ? " active" : ""}`}>
          <Kanban size={16} />
          <span className="app-sidebar-row-label">Board</span>
          {needsYouCount > 0 && <span className="app-sidebar-badge">{needsYouCount}</span>}
        </NavLink>
        <NavLink to="/analytics" className="app-sidebar-row">
          <ChartBar size={16} />
          <span className="app-sidebar-row-label">Analytics</span>
        </NavLink>
        <button
          type="button"
          className="app-sidebar-row app-sidebar-row-toggle"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen((v) => !v)}
        >
          <Gear size={16} />
          <span className="app-sidebar-row-label">Settings</span>
          {settingsOpen ? <CaretDown size={11} /> : <CaretRight size={11} />}
        </button>
        {settingsOpen && (
          <div className="app-sidebar-sub">
            {(["how", "instance"] as const).map((group) => (
              <div key={group}>
                <div className="app-sidebar-eyebrow">{SETTINGS_GROUP_LABEL[group]}</div>
                {SETTINGS_NAV.filter((n) => n.group === group).map((n) => (
                  <NavLink key={n.to} to={`/settings/${n.to}`} className="app-sidebar-row app-sidebar-row-sm">
                    <n.icon size={15} />
                    <span className="app-sidebar-row-label">{n.label}</span>
                  </NavLink>
                ))}
              </div>
            ))}
          </div>
        )}
      </nav>
      {/* Its own slot above the footer (W0.12): inside the footer a long
          degraded message grew into a bubble drawn over it. */}
      <div className="app-sidebar-health">
        <HealthBadge />
      </div>
      <div className="app-sidebar-foot">
        <span>
          {runningCount} running · {needsYouCount} need you
        </span>
        {bindLine && <span>{bindLine}</span>}
      </div>
    </aside>
  );
}
