import { Link, Navigate, Route, Routes } from "react-router-dom";
import { SETTINGS_GROUP_LABEL, SETTINGS_NAV } from "../../settingsNav";
import { AccessPage } from "./AccessPage";
import { AppearancePage } from "./AppearancePage";
import { IntakePage } from "./IntakePage";
import { NotifyPage } from "./NotifyPage";
import { PluginsPage } from "./PluginsPage";
import { PolicyPage } from "./PolicyPage";
import { ReposPage } from "./ReposPage";
import "./settings.css";
import { usePhone } from "./shared";
import { SteeringPage } from "./SteeringPage";
import { TemplatesPage } from "./TemplatesPage";

/* ── shell ────────────────────────────────────────────────────────────────── */

/** m10 left: the phone-only Settings index — a real drill-down state, not
 *  the desktop redirect straight to Repos (Kraft-j92g). */
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
              {/* per-page summary line (design m10: "3 connected", "15 hooks", …)
                  is each page's own job — its list already knows its count;
                  wiring a live summary into this shared list would mean
                  fetching every page's resource just to render this screen. */}
            </Link>
          ))}
        </div>
      ))}
    </div>
  );
}

export function Settings() {
  const phone = usePhone();
  return (
    <div className="settings-body">
      <Routes>
        <Route index element={phone ? <SettingsIndex /> : <Navigate to="repos" replace />} />
        <Route path="templates" element={<Navigate to="/settings/chains" replace />} />
        {SETTINGS_NAV.map((n) => (
          <Route
            key={n.to}
            path={n.to}
            element={
              {
                repos: <ReposPage />,
                chains: <TemplatesPage />,
                plugins: <PluginsPage />,
                policy: <PolicyPage />,
                steering: <SteeringPage />,
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
