import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ConnBadge } from "./components/ConnBadge";
import { HealthBadge } from "./components/HealthBadge";
import { Board } from "./views/Board";
import { WorkItemDetail } from "./views/WorkItemDetail";

export function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <header className="app-header">
        <span className="brand">Kraft</span>
        <ConnBadge />
        <HealthBadge />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
