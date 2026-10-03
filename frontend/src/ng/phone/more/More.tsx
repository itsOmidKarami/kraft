import { Archive, Bell, Bot, ChartColumn, Download, GitBranch, Info, LibraryBig, Lock, Palette, SlidersHorizontal, Workflow, type LucideIcon } from "lucide-react";
import { useEffect } from "react";
import { useDraftCounts } from "../../shell/useDraftCounts";
import { restartNote, SELF_RESTART, useApply } from "../../apply/store";
import { Button } from "../../ui/Button";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { RootHeader } from "../nav/ScreenHeader";
import { Group, type RowSpec } from "../areas/kit";
import "../areas/areas.css";

const TEMPLATES: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/templates/chains", label: "Chains", icon: Workflow },
  { to: "/templates/library", label: "Library", icon: LibraryBig },
];
const SETTINGS: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/settings/repos", label: "Repos", icon: GitBranch },
  { to: "/settings/harnesses", label: "Harnesses", icon: Bot },
  { to: "/settings/policy", label: "Policy", icon: SlidersHorizontal },
  { to: "/settings/auto-intake", label: "Auto-intake", icon: Download },
  { to: "/settings/notifications", label: "Notifications", icon: Bell },
  { to: "/settings/access", label: "Access", icon: Lock },
  { to: "/settings/appearance", label: "Appearance", icon: Palette },
  { to: "/settings/about", label: "About", icon: Info },
];

/** `/more` (W17 brief K.1): every area, with its draft dot or problem count, and the Apply group when a reload or restart is pending. */
export function More() {
  const counts = useDraftCounts();
  const apply = useApply();
  const sheet = useSheet();
  useEffect(() => void useApply.getState().refresh(), []);
  const row = (r: { to: string; label: string; icon: LucideIcon }): RowSpec => {
    const c = counts[r.to];
    return { to: r.to, label: r.label, icon: r.icon, chips: c ? [{ label: c.problems ? `${c.problems} problem${c.problems === 1 ? "" : "s"}` : "draft", tone: c.problems ? "bad" : "warn" }] : undefined };
  };
  const pending = apply.reload.length + apply.restart.length;
  return (
    <>
      <RootHeader title="More" />
      <div className="ph-content">
        {(pending > 0 || apply.phase !== "idle") && (
          <Group title="Apply" note={apply.phase === "restarting" ? "Restarting Kraft…" : apply.phase === "stuck" ? "Kraft did not come back. Run kraft admin health, or read server.log in the run directory." : undefined}>
            <div className="ph-list">
              {[...apply.reload.map((i) => ({ ...i, kind: "reload" as const })), ...apply.restart.map((i) => ({ ...i, kind: "restart" as const }))].map((i) => (
                <div key={i.id} className="ph-row ph-row-static">
                  <span className="ph-row-text">
                    <span className="ph-row-label">{i.text}</span>
                    {i.problem && <span className="ph-row-hint ph-tone-bad">{i.problem}</span>}
                  </span>
                </div>
              ))}
            </div>
            {apply.error && <p className="ph-error" role="alert">{apply.error}</p>}
            <div className="ph-actions-row">
              {apply.reload.length > 0 && <Button className="ph-btn" disabled={apply.phase !== "idle"} onClick={() => void apply.runReload()}>Reload</Button>}
              {apply.restart.length > 0 && apply.managed && <Button className="ph-btn" disabled={apply.phase !== "idle"} onClick={async () => { await apply.askRestart(); sheet.open("restart"); }}>Restart Kraft</Button>}
              {apply.restart.length > 0 && !apply.managed && <p className="ph-note">{SELF_RESTART}</p>}
            </div>
          </Group>
        )}
        <Group title="Templates" rows={TEMPLATES.map(row)} />
        <Group title="Settings" rows={SETTINGS.map(row)} />
        <Group rows={[{ to: "/analytics", label: "Analytics", icon: ChartColumn }, { to: "/archived", label: "Archived", icon: Archive }]} />
      </div>
      {sheet.is("restart") && (
        <ConfirmSheet
          title="Restart Kraft?"
          text={`${apply.restart.map((i) => i.text).join(" · ") || "Pending changes"} apply after the restart. ${restartNote(apply.active)}`}
          confirm={{ label: "Restart now", danger: true, run: () => { sheet.close(); void apply.runRestart(); } }}
          onClose={() => { apply.cancelRestart(); sheet.close(); }}
        />
      )}
    </>
  );
}
