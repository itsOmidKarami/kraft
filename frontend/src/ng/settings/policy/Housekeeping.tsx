import { ATTRIBUTION_KEY, KEYS } from "./keys";
import { PolicyCell, setValue } from "./PolicyCell";
import { SectionHead } from "./SectionHead";
import { problemAt, type Ctx } from "./ctx";
import { BOARD_AND_FORGE } from "../sharedWords";
import { Chip } from "../../ui/Chip";

/** Housekeeping (Decisions §12): how this install runs: instance-wide, no repo, chain or item can change these. */
export function Housekeeping({ ctx }: { ctx: Ctx }) {
  const { p, draft, changes } = ctx;
  const row = (id: keyof typeof KEYS, label: string, value: number | string | null, help: string, bound = false, placeholder?: string) => (
    <div className="pol-one" key={id}>
      <span className="pol-k">{label}</span>
      <PolicyCell draft={draft} k={KEYS[id]} label={label} value={value} bound={bound} placeholder={placeholder} change={changes.get(KEYS[id].key)} problem={problemAt(ctx, KEYS[id].key)} />
      <span className="pol-help">{help}</span>
    </div>
  );
  const max = p.housekeeping.max_concurrent.value;
  const cleanup = p.housekeeping.storage_auto_cleanup?.value ?? null;
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  const signed = p.housekeeping.mr_attribution?.value !== false;
  return (
    <>
      <SectionHead id="housekeeping">How this install runs. Instance-wide: no repo, chain or item can change these.</SectionHead>
      <section className="pol-card" aria-label="Runs">
        <h3 className="pol-h2">Runs</h3>
        {row("concurrent", "max active items", max, `${ctx.active != null ? `${ctx.active} of ${max ?? "—"} slots in use now. ` : ""}Counts every active item, however it was started. Starting past it is refused; Auto-intake only fills free slots.`)}
        {row("relaunch", "rate-limit relaunches", num(p.retries.rate_limit_retries?.value), "Relaunches after a rate limit before the item stops for a person. An item already being relaunched keeps the number it started with.")}
      </section>
      <section className="pol-card" aria-label={BOARD_AND_FORGE}>
        <h3 className="pol-h2">{BOARD_AND_FORGE}</h3>
        {row("archive", "archive after", p.housekeeping.archive_after_days.value, "Completed and abandoned items older than this are archived. Blank never archives. The board's Done group states this number.")}
        {row("forge", "forge call timeout", num(p.retries.forge_cli_timeout_s?.value), "How long one gh, glab or git call may run before it counts as a forge error.")}
        <div className="pol-one">
          <span className="pol-k">merge request footer</span>
          <span className="pol-chips" role="group" aria-label="Kraft footer on merge requests">
            {[true, false].map((on) => (
              <Chip key={String(on)} pressed={signed === on} changed={changes.has(ATTRIBUTION_KEY.key)} onClick={() => void setValue(draft, ATTRIBUTION_KEY, on)}>{on ? "on" : "off"}</Chip>
            ))}
          </span>
          <span className="pol-help">{signed ? "Every merge request description Kraft writes ends with an “Opened by Kraft” footer naming its work item." : "Off: Kraft adds no footer to merge request descriptions."}</span>
        </div>
      </section>
      <section className="pol-card" aria-label="Storage">
        <h3 className="pol-h2">Storage</h3>
        {row("storageLimit", "limit", p.housekeeping.storage_limit?.value ?? null, "The most disk worktrees may use, as a size: 1000M, 10G, 1T. Over it, a start that needs a new worktree waits until you make room; Kraft deletes nothing on its own unless automatic clean-up is on. Blank is no limit.", false, "no limit")}
        {row("storageQuota", "quota", p.housekeeping.storage_quota?.value ?? null, "Over this, Kraft warns. Blank is 80% of the limit.", false, p.housekeeping.storage_quota_default ?? "not set")}
        {row("storageCleanup", "automatic clean-up", cleanup, cleanup ? `On: over the limit, Kraft archives completed and abandoned items that ended ${cleanup} ago or more, oldest first, down to the quota. Their worktrees and any uncommitted changes go; a branch with unpushed commits is kept. Blank turns it off.` : "Off: over the limit, starts wait until you make room. Enter an age in hours or days (24h, 2d) to have Kraft archive finished items at least that old.", false, "off")}
      </section>
    </>
  );
}
