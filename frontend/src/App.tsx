import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import * as api from "./api";
import { AppNav } from "./components/AppNav";
import { Header } from "./components/Header";
import { IntakeModal } from "./components/IntakeModal";
import { BottomNav } from "./components/BottomNav";
import { SearchOverlay } from "./components/SearchOverlay";
import { ToastHost } from "./components/Toast";
import { AnalyticsView } from "./views/Analytics";
import { Board } from "./views/Board";
import { ArchivedView } from "./views/board/Archived";
import { Login } from "./views/Login";
import { SearchView } from "./views/Search";
import { Settings } from "./views/settings";
import { WorkItemDetail } from "./views/work_item";

export function App({ initiallyLocked = false }: { initiallyLocked?: boolean }) {
  const [search, setSearch] = useState(false);
  const [intake, setIntake] = useState(false);
  // Off localhost, any API call can come back 401; `api` raises one event for
  // all of them so the login screen is decided in one place (design 1m).
  // `main.tsx` already knows before mount when its one probe came back 401.
  const [locked, setLocked] = useState(initiallyLocked);
  const [bind, setBind] = useState<string | undefined>();
  const [sessionExpiryDays, setSessionExpiryDays] = useState<number | undefined>();

  // A layout effect, not a passive one: passive effects flush in a later
  // scheduler task, so there was a window where the header was on screen and
  // Ctrl-K did nothing (Kraft-utvg3, Kraft-ica3). Layout effects run in the
  // same commit that puts the DOM up.
  useLayoutEffect(() => {
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
      .then((h) => {
        setBind(h.bind);
        setSessionExpiryDays(h.session_expiry_days);
      })
      .catch(() => {});
  }, [locked]);

  const signedIn = useCallback(() => {
    setLocked(false);
    window.location.reload(); // simplest correct refill of every view's data
  }, []);

  if (locked)
    return <Login bind={bind} sessionExpiryDays={sessionExpiryDays} onSignedIn={signedIn} />;

  return (
    <BrowserRouter>
      <div className="app-shell">
        <AppNav />
        <div className="app-content">
          <Header onSearch={() => setSearch(true)} onNew={() => setIntake(true)} />
          <main>
            <Routes>
              <Route path="/" element={<Board onNewWorkItem={() => setIntake(true)} />} />
              <Route path="/archived" element={<ArchivedView />} />
              <Route path="/work-items/:id" element={<WorkItemDetail />} />
              <Route path="/analytics" element={<AnalyticsView />} />
              <Route path="/settings/*" element={<Settings />} />
              <Route path="/search" element={<SearchView />} />
            </Routes>
          </main>
        </div>
      </div>
      <BottomNav />
      {search && <SearchOverlay onClose={() => setSearch(false)} />}
      {intake && <IntakeModal onClose={() => setIntake(false)} />}
      <ToastHost />
    </BrowserRouter>
  );
}
