import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Pin } from "lucide-react";
import { Kbd } from "../ui/Kbd";
import { useStore } from "../../store";
import { NAV_ICON } from "../icons";
import { isTextField, mod } from "../keys";
import { routesIn, type NgRoute } from "./routes";
import { currentSidebar, writeSidebar, type SidebarMode } from "./sidebarPref";
import { useDraftCounts } from "./useDraftCounts";
import { useGroupCount } from "../board/counts";
import { restartPending, restartWords, useHealth } from "./health";

const connectionWord = (c: "connecting" | "open" | "reconnecting") =>
  c === "open" ? "live" : c === "connecting" ? "connecting…" : "reconnecting…";

export { restartPending };

export function Sidebar({ onSearch }: { onSearch?: () => void }) {
  const [mode, setMode] = useState<SidebarMode>(currentSidebar);
  // Closed by hand (Escape, unpinning, a row, leaving the window) even while the
  // pointer is still over it; the pointer moving in or out, or focus coming back, lifts it.
  const [dismissed, setDismissed] = useState(false);
  const connection = useStore((s) => s.connection);
  // The board's own Needs you count: failed and paused mid-chain included.
  const needsYou = useGroupCount("needs");
  const health = useHealth();
  const drafts = useDraftCounts();
  const location = useLocation();
  const ref = useRef<HTMLElement>(null);

  const pick = (next: SidebarMode) => {
    writeSidebar(next);
    setMode(next);
  };
  // Closes a revealed rail while the pointer is still over it, and takes focus
  // out of it: focus left on a clicked row or the pin would hold it open.
  const retract = (moveFocus = true) => {
    setDismissed(true);
    if (moveFocus) release();
  };
  const release = () => {
    if (ref.current?.contains(document.activeElement)) document.getElementById("ng-main")?.focus({ preventScroll: true });
  };
  // Unpinning collapses it at once. From the keyboard, focus stays on the pin,
  // so the person keeps their place.
  const toggle = (fromPointer: boolean) => {
    const next = mode === "pinned" ? "rail" : "pinned";
    pick(next);
    if (next === "rail") retract(fromPointer);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "\\" || !(e.metaKey || e.ctrlKey) || isTextField(e.target)) return;
      e.preventDefault();
      toggle(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Leaving the window closes it too, so it never waits for a click back in the page.
  useEffect(() => {
    if (mode === "pinned") return;
    const onBlur = () => retract();
    window.addEventListener("blur", onBlur);
    return () => window.removeEventListener("blur", onBlur);
  });

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Escape" || mode === "pinned") return;
    retract();
  };
  // Going to a page from the rail closes it. A click leaves it to the pointer,
  // open while it stays over the rail and closed once it leaves, and only takes
  // the focus out that would hold it open; Enter from the keyboard closes it.
  const went = (e: React.MouseEvent) => {
    if (mode !== "rail") return;
    if (e.detail > 0) release();
    else retract();
  };

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
        onClick={went}
        className={({ isActive }) => `ng-side-row${isActive || (board && location.pathname === "/archived") ? " active" : ""}`}
      >
        <span className="ng-side-ico">
          <r.icon size={16} aria-hidden />
          {area && <span className={`ng-side-mark${area.problems ? " is-bad" : ""}`} aria-hidden />}
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

  const bind = health ? `${health.bind ?? ""}${health.port != null ? `:${health.port}` : ""}` : "";
  const SearchIcon = NAV_ICON.search;

  return (
    <div className="ng-side" data-dismissed={dismissed || undefined} onPointerEnter={() => setDismissed(false)} onPointerLeave={() => setDismissed(false)} onFocus={() => setDismissed(false)}>
      <aside ref={ref} className="ng-sidebar" aria-label="Sidebar" onKeyDown={onKeyDown}>
        <div className="ng-side-head">
          <span className={`ng-side-live-dot ${connection === "open" ? "ok" : "warn"}`} role="img" aria-label={connectionWord(connection)} />
          <span className="ng-side-brand ng-side-label">Kraft</span>
          <span className={`ng-side-live ng-side-label ${connection === "open" ? "ok" : "warn"}`}>{connectionWord(connection)}</span>
        </div>
        <nav className="ng-side-nav" aria-label="Pages">
          <button type="button" className="ng-side-row" aria-label="Search" aria-keyshortcuts="Meta+K Control+K" onClick={() => { if (mode === "rail") retract(); onSearch?.(); }}>
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
          <NavLink to="/settings/about" end className="ng-side-meta ng-side-label" onClick={went}>
            {health ? `${bind}${health.version ? ` · v${health.version}` : ""}` : ""}
          </NavLink>
          {restartPending(health) && (
            <NavLink to="/settings/about" end className="ng-side-meta ng-side-label is-warn" onClick={went}>
              {restartWords(health)}
            </NavLink>
          )}
          <button
            type="button"
            className="ng-side-pin"
            aria-pressed={mode === "pinned"}
            title={mode === "pinned" ? "Collapse sidebar" : "Pin sidebar"}
            // One name, its state in aria-pressed: a label that swapped with it read "Collapse sidebar, pressed" (R8b-11).
            aria-label="Pin sidebar"
            // A click's detail counts its presses; Enter or Space on the button reads 0.
            onClick={(e) => toggle(e.detail > 0)}
          >
            <Pin size={16} aria-hidden />
          </button>
        </div>
      </aside>
    </div>
  );
}
