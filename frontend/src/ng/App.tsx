import { useEffect, useState, type ReactElement } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AnalyticsPage } from "./analytics/AnalyticsPage";
import { ArchivedPage } from "./board/ArchivedPage";
import { BoardPage } from "./board/BoardPage";
import { DraftItemPage } from "./board/draft/DraftItemPage";
import { Gallery } from "./graph/Gallery";
import { ItemPage } from "./item/ItemPage";
import { AccessPage } from "./settings/AccessPage";
import { AppearancePage } from "./settings/AppearancePage";
import { ReviewPage } from "./review/ReviewPage";
import { resumeSession } from "./session";
import { Placeholder } from "./shell/Placeholder";
import { ROUTES } from "./shell/routes";
import { Shell } from "./shell/Shell";
import { SignIn } from "./shell/SignIn";
import { ChainsIndex } from "./templates/ChainsIndex";
import { ChainsPage } from "./templates/ChainsPage";
import { TokenSheet } from "./theme/TokenSheet";
import { Toaster } from "./ui/Toast";

/** The routes whose page exists; every other row of ROUTES renders a placeholder. */
const BUILT: Record<string, ReactElement> = { "/": <BoardPage />, "/archived": <ArchivedPage />, "/analytics": <AnalyticsPage />, "/settings/appearance": <AppearancePage />, "/settings/access": <AccessPage />, "/templates/chains": <ChainsIndex /> };

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
            <Route key={r.path} path={r.path} element={r.built ? BUILT[r.path] : <Placeholder label={r.label} />} />
          ))}
          <Route path="/work-items/new" element={<DraftItemPage />} />
          <Route path="/work-items/:id" element={<ItemPage />} />
          <Route path="/work-items/:id/nodes/:node" element={<ItemPage />} />
          <Route path="/work-items/:id/review" element={<ReviewPage />} />
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
