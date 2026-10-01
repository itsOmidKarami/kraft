import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { Board } from "./board/Board";
import { Item } from "./item/Item";
import { GateReviewRoute } from "./review/GateReview";
import { NewItem } from "./new/NewItem";
import { NodeRoute } from "./node/NodeRoute";
import { Soon } from "./nav/Soon";
import { TabBar } from "./nav/TabBar";
import { Toaster } from "./nav/Toaster";
import { useTrail } from "./nav/trail";
import "./nav/nav.css";

function Frame() {
  useTrail();
  return (
    <div className="ph-app">
      <main id="ph-main" className="ph-screen">
        <Outlet />
      </main>
      <TabBar />
    </div>
  );
}

/** The phone app (W17): its own router on the /ng basename, its own toaster. It
 *  shares the desktop's URLs, so a place survives a resize (A.1). */
export function PhoneApp() {
  return (
    <BrowserRouter basename="/ng">
      <Routes>
        <Route element={<Frame />}>
          <Route path="/" element={<Board />} />
          <Route path="/search" element={<Soon title="Search" />} />
          <Route path="/analytics" element={<Soon title="Analytics" />} />
          <Route path="/more" element={<Soon title="More" />} />
          <Route path="/work-items/new" element={<NewItem />} />
          <Route path="/work-items/:id" element={<Item />} />
          <Route path="/work-items/:id/nodes/:node" element={<NodeRoute />} />
          <Route path="/work-items/:id/review" element={<GateReviewRoute />} />
          <Route path="/templates/*" element={<Soon title="Templates" />} />
          <Route path="/settings" element={<Navigate to="/settings/policy/limits" replace />} />
          <Route path="/settings/*" element={<Soon title="Settings" />} />
          <Route path="*" element={<Soon title="Not found" />} />
        </Route>
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
