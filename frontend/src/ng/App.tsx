import { lazy, Suspense, useEffect, useState, type ReactElement } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AnalyticsPage } from "./analytics/AnalyticsPage";
import { ArchivedPage } from "./board/ArchivedPage";
import { BoardPage } from "./board/BoardPage";
import { DraftItemPage } from "./board/draft/DraftItemPage";
import { LibraryPage } from "./library/LibraryPage";
import { HarnessesPage } from "./harnesses/HarnessesPage";
import { PhoneApp } from "./phone/PhoneApp";
import { PhoneSignIn } from "./phone/signin/PhoneSignIn";
import { usePhone } from "./phone/usePhone";
import { ItemPage } from "./item/ItemPage";
import { AboutPage } from "./settings/AboutPage";
import { AccessPage } from "./settings/AccessPage";
import { AppearancePage } from "./settings/AppearancePage";
import { NotifyPage } from "./settings/NotifyPage";
import { ReviewPage } from "./review/ReviewPage";
import { resumeSession } from "./session";
import { Alias, ALIASES, PHONE_ONLY, ShippedHash } from "./shell/aliases";
import { Placeholder } from "./shell/Placeholder";
import { ROUTES } from "./shell/routes";
import { Shell } from "./shell/Shell";
import { SignIn } from "./shell/SignIn";
import { ChainsIndex } from "./templates/ChainsIndex";
import { ChainsPage } from "./templates/ChainsPage";
import { ReposPage } from "./templates/ReposPage";
import { IntakePage } from "./settings/IntakePage";
import { PolicyPage } from "./settings/PolicyPage";
import { StoragePage } from "./settings/StoragePage";
import { Toaster } from "./ui/Toast";
import { Tooltip } from "./ui/Tooltip";

/** `/_gallery` and `/_tokens`, the component and token sheets, are for building
 *  the UI: the dev server has them, and so does a build made with
 *  `VITE_DEV_PAGES=1`. A release
 *  build has neither, nor their code: the constant is false there, so the
 *  bundler drops the imports. */
const DEV_PAGES = import.meta.env.DEV || import.meta.env.VITE_DEV_PAGES === "1";
const Gallery = DEV_PAGES ? lazy(() => import("./graph/Gallery").then((m) => ({ default: m.Gallery }))) : null;
const TokenSheet = DEV_PAGES ? lazy(() => import("./theme/TokenSheet").then((m) => ({ default: m.TokenSheet }))) : null;

/** The routes whose page exists; every other row of ROUTES renders a placeholder. */
const BUILT: Record<string, ReactElement> = { "/": <BoardPage />, "/archived": <ArchivedPage />, "/analytics": <AnalyticsPage />, "/settings/appearance": <AppearancePage />, "/settings/access": <AccessPage />, "/settings/about": <AboutPage />, "/settings/storage": <StoragePage />, "/settings/notifications": <NotifyPage />, "/templates/chains": <ChainsIndex />, "/templates/library": <LibraryPage />, "/settings/repos": <ReposPage />, "/settings/policy": <PolicyPage />, "/settings/auto-intake": <IntakePage />, "/settings/harnesses": <HarnessesPage /> };

export function App({ initiallyLocked = false }: { initiallyLocked?: boolean }) {
  const [locked, setLocked] = useState(initiallyLocked);
  const phone = usePhone();
  useEffect(() => {
    const lock = () => setLocked(true);
    window.addEventListener("kraft:unauthenticated", lock);
    return () => window.removeEventListener("kraft:unauthenticated", lock);
  }, []);
  if (locked) {
    const onSignedIn = async () => { await resumeSession(); setLocked(false); };
    return phone ? <PhoneSignIn onSignedIn={onSignedIn} /> : <SignIn onSignedIn={onSignedIn} />;
  }
  if (phone) return <PhoneApp />;
  return (
    <BrowserRouter>
      <ShippedHash />
      <Routes>
        {Gallery && <Route path="/_gallery" element={<Suspense fallback={null}><Gallery /></Suspense>} />}
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
          <Route path="/settings/repos/:repo" element={<ReposPage />} />
          <Route path="/settings" element={<Navigate to="/settings/policy/limits" replace />} />
          <Route path="/settings/policy/:section" element={<PolicyPage />} />
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
          <Route path="/templates/chains/:chain/nodes/:node" element={<ChainsPage />} />
          {TokenSheet && <Route path="/_tokens" element={<Suspense fallback={null}><TokenSheet /></Suspense>} />}
          {[...ALIASES, ...PHONE_ONLY].map(([from, to]) => (
            <Route key={from} path={from} element={<Alias to={to} />} />
          ))}
          <Route path="*" element={<Placeholder label="Not found" />} />
        </Route>
      </Routes>
      <Toaster />
      <Tooltip />
    </BrowserRouter>
  );
}
