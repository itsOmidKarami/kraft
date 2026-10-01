import { useCallback, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { ApplyDialogs } from "../apply/ApplyChip";
import { DocViewer } from "../item/DocViewer";
import { Header } from "./Header";
import { HeaderActionsHost, HeaderTailHost } from "./HeaderActions";
import { SearchOverlay } from "./SearchOverlay";
import { Sidebar } from "./Sidebar";
import "./shell.css";

/** The frame every page sits in. */
export function Shell() {
  const [actions, setActions] = useState<HTMLElement | null>(null);
  const [tail, setTail] = useState<HTMLElement | null>(null);
  const [searching, setSearching] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);

  // From every page, including with focus in a field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "k" || !(e.metaKey || e.ctrlKey) || e.altKey) return;
      e.preventDefault();
      setSearching(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const closeSearch = useCallback(() => setSearching(false), []);
  return (
    <HeaderActionsHost.Provider value={actions}>
      <HeaderTailHost.Provider value={tail}>
        <a href="#ng-main" className="ng-skip" onClick={(e) => { e.preventDefault(); document.getElementById("ng-main")?.focus(); }}>
          Skip to content
        </a>
        <div className="ng-shell">
          <Sidebar onSearch={() => setSearching(true)} />
          <div className="ng-frame">
            <Header actionsRef={setActions} tailRef={setTail} />
            <main id="ng-main" tabIndex={-1} className="ng-main">
              <Outlet />
            </main>
          </div>
        </div>
        {searching && <SearchOverlay onClose={closeSearch} onDocument={setViewing} />}
        {viewing && <DocViewer source={{ kind: "document", id: viewing }} onClose={() => setViewing(null)} />}
        <ApplyDialogs />
      </HeaderTailHost.Provider>
    </HeaderActionsHost.Provider>
  );
}
