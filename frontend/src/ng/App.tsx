import { useEffect, useState, type ReactElement } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Gallery } from "./graph/Gallery";
import { ItemPage } from "./item/ItemPage";
import { AppearancePage } from "./settings/AppearancePage";
import { resumeSession } from "./session";
import { BoardPage } from "./shell/BoardPage";
import { Placeholder } from "./shell/Placeholder";
import { ROUTES } from "./shell/routes";
import { Shell } from "./shell/Shell";
import { SignIn } from "./shell/SignIn";
import { TokenSheet } from "./theme/TokenSheet";
import { Toaster } from "./ui/Toast";

/** The routes whose page exists; every other row of ROUTES renders a placeholder. */
const BUILT: Record<string, ReactElement> = { "/settings/appearance": <AppearancePage /> };

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
            <Route key={r.path} path={r.path} element={r.built ? BUILT[r.path] : r.path === "/" ? <BoardPage label={r.label} /> : <Placeholder label={r.label} />} />
          ))}
          <Route path="/work-items/:id" element={<ItemPage />} />
          <Route path="/work-items/:id/nodes/:node" element={<ItemPage />} />
          {/* The review page is W8's; until then the placeholder links to the shipped Changes tab. */}
          <Route path="/work-items/:id/review" element={<Placeholder label="Work item" />} />
          <Route path="/_tokens" element={<TokenSheet />} />
          <Route path="*" element={<Placeholder label="Not found" note="There is no page at this address in the new UI." />} />
        </Route>
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
