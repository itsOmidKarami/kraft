import { useState } from "react";
import { Outlet } from "react-router-dom";
import { HeaderActionsHost } from "./HeaderActions";
import { Sidebar } from "./Sidebar";
import "./shell.css";

/** The frame every /ng page sits in. */
export function Shell() {
  const [actions, setActions] = useState<HTMLElement | null>(null);
  return (
    <HeaderActionsHost.Provider value={actions}>
      <a href="#ng-main" className="ng-skip" onClick={(e) => { e.preventDefault(); document.getElementById("ng-main")?.focus(); }}>
        Skip to content
      </a>
      <div className="ng-shell">
        <Sidebar />
        <div className="ng-frame">
          <header className="ng-header">
            <div className="ng-header-actions" ref={setActions} />
          </header>
          <main id="ng-main" tabIndex={-1} className="ng-main">
            <Outlet />
          </main>
        </div>
      </div>
    </HeaderActionsHost.Provider>
  );
}
