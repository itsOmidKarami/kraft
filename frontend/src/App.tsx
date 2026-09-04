import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ConnBadge } from "./components/ConnBadge";
import { HealthBadge } from "./components/HealthBadge";
import { SearchOverlay } from "./components/SearchOverlay";
import { Board } from "./views/Board";
import { WorkItemDetail } from "./views/WorkItemDetail";

export function App() {
  const [search, setSearch] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch(true);
      } else if (e.key === "Escape") {
        setSearch(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <header className="app-header">
        <span className="brand">Kraft</span>
        <ConnBadge />
        <HealthBadge />
        <button className="search-open" onClick={() => setSearch(true)}>
          Search
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </main>
      {search && <SearchOverlay onClose={() => setSearch(false)} />}
    </BrowserRouter>
  );
}
