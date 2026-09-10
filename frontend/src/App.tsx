import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ArrowLeft, MagnifyingGlass, Plus } from "@phosphor-icons/react";
import * as api from "./api";
import { ConnBadge } from "./components/ConnBadge";
import { HealthBadge } from "./components/HealthBadge";
import { IntakeModal } from "./components/IntakeModal";
import { BottomNav } from "./components/BottomNav";
import { SearchOverlay } from "./components/SearchOverlay";
import { AnalyticsView } from "./views/Analytics";
import { Board } from "./views/Board";
import { Login } from "./views/Login";
import { SearchView } from "./views/Search";
import { Settings } from "./views/Settings";
import { WorkItemDetail } from "./views/WorkItemDetail";

/**
 * The one header bar (design 2a / 4a): brand, health, Search, and — on the
 * board only — the primary "New work item". Detail screens lead with a back
 * link instead.
 */
function Nav({ onSearch, onNew }: { onSearch: () => void; onNew: () => void }) {
  const onBoard = useLocation().pathname === "/";
  return (
    <header className="nav app-nav">
      {!onBoard && (
        <Link to="/" className="nav-back">
          <ArrowLeft size={14} />
          Board
        </Link>
      )}
      <span className="nav-brand">Kraft</span>
      <ConnBadge />
      <HealthBadge />
      <NavLink to="/analytics" className="nav-link desktop-only">
        Analytics
      </NavLink>
      <NavLink to="/settings" className="nav-link desktop-only">
        Settings
      </NavLink>
      <button className="btn btn-secondary desktop-only" onClick={onSearch}>
        <MagnifyingGlass size={14} />
        Search
        <span className="kbd">⌘K</span>
      </button>
      {onBoard && (
        <button className="btn btn-primary" onClick={onNew}>
          <Plus size={14} />
          New work item
        </button>
      )}
    </header>
  );
}

export function App() {
  const [search, setSearch] = useState(false);
  const [intake, setIntake] = useState(false);
  // Off localhost, any API call can come back 401; `api` raises one event for
  // all of them so the login screen is decided in one place (design 1m).
  const [locked, setLocked] = useState(false);
  const [bind, setBind] = useState<string | undefined>();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch(true);
      } else if (e.key === "Escape") {
        setSearch(false);
      }
    };
    const onUnauthenticated = () => setLocked(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("kraft:unauthenticated", onUnauthenticated);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("kraft:unauthenticated", onUnauthenticated);
    };
  }, []);

  useEffect(() => {
    if (!locked) return;
    // /health needs no session, and it is where the bind address comes from
    api
      .getHealth()
      .then((h) => setBind(h.bind))
      .catch(() => {});
  }, [locked]);

  const signedIn = useCallback(() => {
    setLocked(false);
    window.location.reload(); // simplest correct refill of every view's data
  }, []);

  if (locked) return <Login bind={bind} onSignedIn={signedIn} />;

  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Nav onSearch={() => setSearch(true)} onNew={() => setIntake(true)} />
      <main>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
          <Route path="/analytics" element={<AnalyticsView />} />
          <Route path="/settings/*" element={<Settings />} />
          <Route path="/search" element={<SearchView />} />
        </Routes>
      </main>
      <BottomNav />
      {search && <SearchOverlay onClose={() => setSearch(false)} />}
      {intake && <IntakeModal onClose={() => setIntake(false)} />}
    </BrowserRouter>
  );
}
