import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Gallery } from "./graph/Gallery";
import { legacyPath } from "./legacyPath";
import { AppearancePage } from "./settings/AppearancePage";
import { TokenSheet } from "./theme/TokenSheet";
import { Toaster } from "./ui/Toast";

const currentUi = () => legacyPath(window.location);

function Stub() {
  return (
    <main style={{ padding: "16px 24px" }}>
      <h1>Kraft next</h1>
      <p>This is the UX V2 build, in progress.</p>
      <a href={currentUi()}>Current UI ↗</a>
    </main>
  );
}

export function App({ initiallyLocked = false }: { initiallyLocked?: boolean }) {
  const [locked, setLocked] = useState(initiallyLocked);
  useEffect(() => {
    const lock = () => setLocked(true);
    window.addEventListener("kraft:unauthenticated", lock);
    return () => window.removeEventListener("kraft:unauthenticated", lock);
  }, []);
  // W2 replaces this with the Sign-in screen.
  if (locked) return <main><a href={currentUi()}>Sign in on the current UI</a></main>;
  return (
    <BrowserRouter basename="/ng">
      <Routes>
        <Route path="/settings/appearance" element={<AppearancePage />} />
        <Route path="/_gallery" element={<Gallery />} />
        <Route path="/_tokens" element={<TokenSheet />} />
        <Route path="*" element={<Stub />} />
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
