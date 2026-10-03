import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { PanelLeftClose, PanelLeftOpen } from "../icons";
import { Kbd } from "../ui/Kbd";
import { useStore } from "../../store";
import { NAV_ICON } from "../icons";
import { isTextField, mod } from "../keys";
import { routesIn, type NgRoute } from "./routes";
import { currentSidebar, writeSidebar, type SidebarMode } from "./sidebarPref";
import { useDraftCounts } from "./useDraftCounts";
import { useGroupCount } from "../board/counts";
import { request } from "../http";
import type { UpdateState } from "../settings/AboutPage";
import { restartPending, useHealth } from "./health";

const connectionWord = (c: "connecting" | "open" | "reconnecting") =>
  c === "open" ? "live" : c === "connecting" ? "connecting…" : "reconnecting…";

export function Sidebar({ onSearch }: { onSearch?: () => void }) {
  const [mode, setMode] = useState<SidebarMode>(currentSidebar);
  const connection = useStore((s) => s.connection);
  // The board's own Needs you count: failed and paused mid-chain included.
  const needsYou = useGroupCount("needs");
  const health = useHealth();
  const drafts = useDraftCounts();
  const location = useLocation();
  // A newer release on the feed, read once; About has the rest.
  const [behind, setBehind] = useState(false);
  useEffect(() => {
    request<UpdateState>("/update").then((r) => setBehind(r.status === 200 && r.body.behind === true), () => {});
  }, []);
  const restart = restartPending(health);
  const update = behind || restart;

  // Unpinning only changes the mode: an unpinned sidebar is shown while the
  // pointer is over it (or keyboard focus is in it) and hides when it leaves,
  // all in shell.css. Nothing here closes it for the pointer. From the
  // keyboard, Escape and a row chosen with Enter hand focus to the page, which
  // hides it: it stayed over the new page until every row was tabbed past (#502 review).
  const toPage = () => document.getElementById("ng-main")?.focus();
  const onSideKey = (e: React.KeyboardEvent) => {
    if (mode !== "rail" || e.key !== "Escape" || e.nativeEvent.isComposing) return;
    e.preventDefault();
    toPage();
  };
  const toggle = () => {
    const next = mode === "pinned" ? "rail" : "pinned";
    writeSidebar(next);
    setMode(next);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "\\" || !(e.metaKey || e.ctrlKey) || isTextField(e.target)) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const row = (r: NgRoute) => {
    const board = r.path === "/";
    const area = drafts[r.path];
    const label = board && needsYou > 0
      ? `Board, ${needsYou} need you`
      : area ? [r.label, "unpublished draft", area.problems ? `${area.problems} problem${area.problems === 1 ? "" : "s"}` : ""].filter(Boolean).join(", ") : r.label;
    return (
      <NavLink
        key={r.path}
        to={r.path}
        // A templates area has pages under its row (/templates/chains/default), and so has Policy (/settings/policy/loops).
        end={r.group !== "templates" && r.path !== "/settings/policy"}
        aria-label={label}
        // `detail` 0: Enter, not a click; the pointer's row keeps focus (S4).
        onClick={(e) => { if (mode === "rail" && e.detail === 0) requestAnimationFrame(toPage); }}
        className={({ isActive }) => `ng-side-row${isActive || (board && location.pathname === "/archived") ? " active" : ""}`}
      >
        <span className="ng-side-ico">
          <r.icon size={16} aria-hidden />
        </span>
        <span className="ng-side-label" aria-hidden>{r.label}</span>
        {board && needsYou > 0 && <span className="ng-side-dot" aria-hidden />}
        {area && (
          <span className="ng-side-badges ng-side-label" aria-hidden>
            <span className="ng-side-dot" />
            {area.problems > 0 && <span className="ng-side-count">{area.problems}</span>}
          </span>
        )}
      </NavLink>
    );
  };

  const SearchIcon = NAV_ICON.search;

  return (
    <div className="ng-side">
      <div className="ng-side-edge" aria-hidden />
      <aside className="ng-sidebar" aria-label="Sidebar" onKeyDown={onSideKey}>
        <div className="ng-side-head">
          <span className={`ng-side-live-dot ${connection === "open" ? "ok" : "warn"}`} role="img" aria-label={connectionWord(connection)} />
          <span className="ng-side-brand ng-side-label">Kraft</span>
          <span className={`ng-side-live ng-side-label ${connection === "open" ? "ok" : "warn"}`}>{connectionWord(connection)}</span>
          <button
            type="button"
            className="ng-side-pin"
            aria-pressed={mode === "pinned"}
            title={mode === "pinned" ? "Collapse sidebar" : "Pin sidebar"}
            // One name, its state in aria-pressed: a label that swapped with it read "Collapse sidebar, pressed" (R8b-11).
            aria-label="Pin sidebar"
            onClick={toggle}
          >
            {mode === "pinned" ? <PanelLeftClose size={16} aria-hidden /> : <PanelLeftOpen size={16} aria-hidden />}
          </button>
        </div>
        <nav className="ng-side-nav" aria-label="Pages">
          <button type="button" className="ng-side-row" aria-label="Search" aria-keyshortcuts="Meta+K Control+K" onClick={() => onSearch?.()}>
            <SearchIcon size={16} aria-hidden />
            <span className="ng-side-label" aria-hidden>Search</span>
            <span className="ng-side-kbd ng-side-label" aria-hidden><Kbd>{mod("K")}</Kbd></span>
          </button>
          {routesIn("top").map(row)}
          <div className="ng-side-group ng-side-group-templates"><span className="ng-side-label">Templates</span></div>
          {routesIn("templates").map(row)}
          <div className="ng-side-group"><span className="ng-side-label">Settings</span></div>
          {routesIn("settings").map(row)}
        </nav>
        <div className="ng-side-foot">
          {health?.version && (
            <NavLink to="/settings/about" end className="ng-side-meta" aria-label={`Kraft v${health.version}${restart ? ", restart to finish the update" : behind ? ", update available" : ""}`}>
              v{health.version}
              {update && <span className="ng-side-update"><span className="ng-side-dot" />update</span>}
            </NavLink>
          )}
        </div>
      </aside>
    </div>
  );
}
