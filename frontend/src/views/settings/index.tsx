import { Navigate, Route, Routes } from "react-router-dom";
import { SETTINGS_NAV } from "../../settingsNav";
import { AccessPage } from "./AccessPage";
import { AppearancePage } from "./AppearancePage";
import { IntakePage } from "./IntakePage";
import { NotifyPage } from "./NotifyPage";
import { PluginsPage } from "./PluginsPage";
import { PolicyPage } from "./PolicyPage";
import { ReposPage } from "./ReposPage";
import { SteeringPage } from "./SteeringPage";
import { TemplatesPage } from "./TemplatesPage";

/* ── shell ────────────────────────────────────────────────────────────────── */

export function Settings() {
  return (
    <div className="settings-body">
      <Routes>
        <Route index element={<Navigate to="repos" replace />} />
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
