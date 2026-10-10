import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { Archived } from "./board/Archived";
import { Board } from "./board/Board";
import { Analytics } from "./analytics/Analytics";
import { Item } from "./item/Item";
import { ChainNodeView, ChainsList, ChainView } from "./areas/Chains";
import { HarnessesList, HarnessView, ProfileView } from "./areas/Harnesses";
import { LibraryComponentView, LibraryList } from "./areas/Library";
import { AboutScreen } from "./areas/About";
import { AccessScreen } from "./areas/Access";
import { AppearanceScreen } from "./areas/Appearance";
import { IntakeScreen, ScheduleScreen } from "./areas/Intake";
import { NotificationChannel, NotificationsList } from "./areas/Notifications";
import { PolicyScreen } from "./areas/Policy";
import { ReposList, RepoView } from "./areas/Repos";
import { More } from "./more/More";
import { Search } from "./search/Search";
import { GateReviewRoute } from "./review/GateReview";
import { NewItem } from "./new/NewItem";
import { NodeRoute } from "./node/NodeRoute";
import { Alias, ALIASES, ShippedHash } from "../shell/aliases";
import { NotFound } from "./nav/NotFound";
import { TabBar } from "./nav/TabBar";
import { Toaster } from "./nav/Toaster";
import { Tooltip } from "../ui/Tooltip";
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

/** The phone app (W17): its own router, its own toaster. It
 *  shares the desktop's URLs, so a place survives a resize (A.1). */
export function PhoneApp() {
  return (
    <BrowserRouter>
      <ShippedHash />
      <Routes>
        <Route element={<Frame />}>
          <Route path="/" element={<Board />} />
          <Route path="/search" element={<Search />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/more" element={<More />} />
          <Route path="/archived" element={<Archived />} />
          <Route path="/work-items/new" element={<NewItem />} />
          <Route path="/work-items/:id" element={<Item />} />
          <Route path="/work-items/:id/nodes/:node" element={<NodeRoute />} />
          <Route path="/work-items/:id/review" element={<GateReviewRoute />} />
          <Route path="/templates" element={<Navigate to="/templates/chains" replace />} />
          <Route path="/templates/chains" element={<ChainsList />} />
          <Route path="/templates/chains/:chain" element={<ChainView />} />
          <Route path="/templates/chains/:chain/nodes/:node" element={<ChainNodeView />} />
          <Route path="/templates/library" element={<LibraryList />} />
          <Route path="/templates/library/:ref" element={<LibraryComponentView />} />
          <Route path="/settings/harnesses" element={<HarnessesList />} />
          <Route path="/settings/harnesses/profiles/:name" element={<ProfileView />} />
          <Route path="/settings/harnesses/:id" element={<HarnessView />} />
          <Route path="/settings/repos" element={<ReposList />} />
          <Route path="/settings/repos/:repo" element={<RepoView />} />
          <Route path="/templates/*" element={<NotFound />} />
          <Route path="/settings" element={<Navigate to="/settings/policy/limits" replace />} />
          <Route path="/settings/policy" element={<Navigate to="/settings/policy/limits" replace />} />
          <Route path="/settings/policy/:section" element={<PolicyScreen />} />
          <Route path="/settings/auto-intake" element={<IntakeScreen />} />
          <Route path="/settings/auto-intake/schedules/:index" element={<ScheduleScreen />} />
          <Route path="/settings/notifications" element={<NotificationsList />} />
          <Route path="/settings/notifications/:channel" element={<NotificationChannel />} />
          <Route path="/settings/access" element={<AccessScreen />} />
          <Route path="/settings/appearance" element={<AppearanceScreen />} />
          <Route path="/settings/about" element={<AboutScreen />} />
          {/* A desktop-only page (Storage; its phone screen is Kraft-ipokh): the address opens More, never Not found. */}
          <Route path="/settings/storage" element={<Navigate to="/more" replace />} />
          <Route path="/settings/*" element={<NotFound />} />
          {/* The shipped addresses that moved (spec §11.2), after the screens: /search is a phone screen and wins over its alias to the board. */}
          {ALIASES.map(([from, to]) => <Route key={from} path={from} element={<Alias to={to} />} />)}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
      <Toaster />
      <Tooltip />
    </BrowserRouter>
  );
}
