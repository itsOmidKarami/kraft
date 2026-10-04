import { elapsedBetween } from "../../../format";
import type { CancelPreview } from "../../../types";
import { taskName } from "../paths";

/** What ending the item stops now, as the Cancel and Mark complete cards both say it. */
export const stopsNow = (run: CancelPreview["running"]): string => {
  if (!run) return "Nothing is running.";
  const what = [run.task ? taskName(run.task) : run.node, run.attempt ? `attempt ${run.attempt}` : ""].filter(Boolean).join(", ");
  return `${what}${run.started_at ? ` (${elapsedBetween(run.started_at)})` : ""}. That attempt's work is lost.`;
};

/** The rows of what an end does (stops, keeps, afterwards), under a card's lead. */
export function EndFacts({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="item-facts">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}
