import { useEffect, useState, type ReactElement } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AnalyticsPage } from "./analytics/AnalyticsPage";
import { ArchivedPage } from "./board/ArchivedPage";
import { BoardPage } from "./board/BoardPage";
import { DraftItemPage } from "./board/draft/DraftItemPage";
import { Gallery } from "./graph/Gallery";
import { LibraryPage } from "./library/LibraryPage";
import { ItemPage } from "./item/ItemPage";
import { AboutPage } from "./settings/AboutPage";
import { AccessPage } from "./settings/AccessPage";
import { AppearancePage } from "./settings/AppearancePage";
import { NotifyPage } from "./settings/NotifyPage";
import { ReviewPage } from "./review/ReviewPage";
import { resumeSession } from "./session";
import { Placeholder } from "./shell/Placeholder";
import { ROUTES } from "./shell/routes";
import { Shell } from "./shell/Shell";
import { SignIn } from "./shell/SignIn";
import { ChainsIndex } from "./templates/ChainsIndex";
import { ChainsPage } from "./templates/ChainsPage";
import { ReposPage } from "./templates/ReposPage";
import { PolicyPage } from "./settings/PolicyPage";
import { TokenSheet } from "./theme/TokenSheet";
import { Toaster } from "./ui/Toast";

/** The routes whose page exists; every other row of ROUTES renders a placeholder. */
const BUILT: Record<string, ReactElement> = { "/": <BoardPage />, "/archived": <ArchivedPage />, "/analytics": <AnalyticsPage />, "/settings/appearance": <AppearancePage />, "/settings/access": <AccessPage />, "/settings/about": <AboutPage />, "/settings/notifications": <NotifyPage />, "/templates/chains": <ChainsIndex />, "/templates/library": <LibraryPage />, "/templates/repos": <ReposPage />, "/settings/policy": <PolicyPage /> };

export function App({ initiallyLocked = false }: { initiallyLocked?: boolean }) {
  const [locked, setLocked] = useState(initiallyLocked);
  useEffect(() => {
    const lock = () => setLocked(true);
    window.addEventListener("kraft:unauthenticated", lock);
    return () => window.removeEventListener("kraft:unauthenticated", lock);
  }, []);
  if (locked) return <SignIn onSignedIn={async () => { await resumeSession(); setLocked(false); }} />;
  return (
    <BrowserRouter basename="/ng">
      <Routes>
        <Route path="/_gallery" element={<Gallery />} />
        <Route element={<Shell />}>
          {ROUTES.map((r) => (
            <Route key={r.path} path={r.path} element={r.built && BUILT[r.path] ? BUILT[r.path] : <Placeholder label={r.label} />} />
          ))}
          <Route path="/work-items/new" element={<DraftItemPage />} />
          <Route path="/work-items/:id" element={<ItemPage />} />
          <Route path="/work-items/:id/nodes/:node" element={<ItemPage />} />
          <Route path="/work-items/:id/review" element={<ReviewPage />} />
          <Route path="/templates/library/:ref" element={<LibraryPage />} />
          <Route path="/templates" element={<Navigate to="/templates/chains" replace />} />
          <Route path="/templates/repos/:repo" element={<ReposPage />} />
          <Route path="/settings" element={<Navigate to="/settings/policy/limits" replace />} />
          <Route path="/settings/policy/:section" element={<PolicyPage />} />
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
          <Route path="/templates/chains/:chain/nodes/:node" element={<ChainsPage />} />
          <Route path="/_tokens" element={<TokenSheet />} />
          <Route path="*" element={<Placeholder label="Not found" note="There is no page at this address in the new UI." />} />
        </Route>
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
