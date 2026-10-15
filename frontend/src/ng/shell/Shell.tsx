import { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { ApplyDialogs } from "../apply/ApplyChip";
import { DocViewer } from "../item/DocViewer";
import { dialogOpen, isBareKey } from "../keys";
import { useOffline, useReloadOnReconnect } from "../offline";
import { Header } from "./Header";
import { OfflineBanner } from "./OfflineBanner";
import { HeaderActionsHost, HeaderTailHost } from "./HeaderActions";
import { SearchOverlay } from "./SearchOverlay";
import { ShortcutHelp } from "./ShortcutHelp";
import { Sidebar } from "./Sidebar";
import { useDocumentTitle, useFocusHeading } from "./usePageChrome";
import "./shell.css";

/** The frame every page sits in. */
export function Shell() {
  const [actions, setActions] = useState<HTMLElement | null>(null);
  const [tail, setTail] = useState<HTMLElement | null>(null);
  const [searching, setSearching] = useState(false);
  const [viewing, setViewing] = useState<{ id: string; q: string } | null>(null);
  const [help, setHelp] = useState(false);
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { offline } = useOffline();
  useReloadOnReconnect();
  useDocumentTitle();
  useFocusHeading("ng-main", pathname);

  // The keys every page has, outside a field and with no dialog open. "N" files an item from the board's composer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!isBareKey(e) || dialogOpen()) return;
      if (e.key === "?") setHelp(true);
      else if (e.key === "N" && !offline) {
        const q = new URLSearchParams(pathname === "/" ? search : "");
        q.delete("sel");
        q.set("new", "1");
        navigate(`/?${q}`);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    const open = () => setHelp(true);
    window.addEventListener("kraft:shortcuts", open);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("kraft:shortcuts", open);
    };
  }, [pathname, search, offline, navigate]);

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
            <OfflineBanner />
            <main id="ng-main" tabIndex={-1} className="ng-main">
              <Outlet />
            </main>
          </div>
        </div>
        {searching && <SearchOverlay onClose={closeSearch} onDocument={(id, q) => setViewing({ id, q })} />}
        {/* The search that opened it is gone by its close: focus goes to the page, not <body> (#502 review). */}
        {viewing && <DocViewer source={{ kind: "document", id: viewing.id }} query={viewing.q} onClose={() => setViewing(null)} returnTo={() => document.getElementById("ng-main")} />}
        {help && <ShortcutHelp onClose={() => setHelp(false)} />}
        <ApplyDialogs />
      </HeaderTailHost.Provider>
    </HeaderActionsHost.Provider>
  );
}
