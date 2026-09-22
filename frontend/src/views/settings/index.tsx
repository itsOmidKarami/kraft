import { Link, Navigate, Route, Routes } from "react-router-dom";
import { SETTINGS_GROUP_LABEL, SETTINGS_NAV } from "../../settingsNav";
import { AccessPage } from "./AccessPage";
import { AppearancePage } from "./AppearancePage";
import { HarnessesPage } from "./HarnessesPage";
import { IntakePage } from "./IntakePage";
import { LibraryPage } from "./LibraryPage";
import { NotifyPage } from "./NotifyPage";
import { PolicyPage } from "./PolicyPage";
import { ReposPage } from "./ReposPage";
import "./settings.css";
import { TemplatesPage } from "./TemplatesPage";

/* ── shell ────────────────────────────────────────────────────────────────── */

/** The Settings index (m10; W7.9 on desktop too): the ten sections with a
 *  line each, so the "Settings" crumb names a page, not a redirect to Repos. */
function SettingsIndex() {
  return (
    <div className="settings-index">
      {(["how", "instance"] as const).map((group) => (
        <div key={group}>
          <div className="section-label">{SETTINGS_GROUP_LABEL[group]}</div>
          {SETTINGS_NAV.filter((n) => n.group === group).map((n) => (
            <Link key={n.to} to={n.to} className="settings-index-row">
              <n.icon size={18} />
              <span className="row-title">{n.label}</span>
              {/* What the page is for, static. A live count (design m10: "3
                  connected") would fetch every page's resource for this list. */}
              <span className="row-sub">{n.description}</span>
            </Link>
          ))}
        </div>
      ))}
    </div>
  );
}

export function Settings() {
  return (
    <div className="settings-body">
      <Routes>
        <Route index element={<SettingsIndex />} />
        <Route path="templates" element={<Navigate to="/settings/chains" replace />} />
        {/* The hook-binding editor went with the registry it edited (Template Schema V1). */}
        <Route path="plugins" element={<Navigate to="/settings/chains" replace />} />
        {/* Steering is library profiles now (Kraft-91i6p): edited on the Library screen. */}
        <Route path="steering" element={<Navigate to="/settings/library" replace />} />
        {SETTINGS_NAV.map((n) => (
          <Route
            key={n.to}
            path={n.to}
            element={
              {
                repos: <ReposPage />,
                chains: <TemplatesPage />,
                library: <LibraryPage />,
                harnesses: <HarnessesPage />,
                policy: <PolicyPage />,
                intake: <IntakePage />,
                notify: <NotifyPage />,
                access: <AccessPage />,
                appearance: <AppearancePage />,
              }[n.to]
            }
          />
        ))}
      </Routes>
    </div>
  );
}
