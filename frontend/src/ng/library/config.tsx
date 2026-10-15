import { Head, Note } from "../templates/panes/controls";
import { ConfigRow, type Row } from "../templates/panes/Config";
import type { PaneCtx } from "../templates/panes/Overview";
import { authoredAt } from "../templates/draft/view";
import type { LibKind } from "./describe";

/** What a component writes in structure, not as a setting: the Overview and the canvas show these. */
const NOT_SETTINGS = new Set(["id", "kind", "icon", "extends", "steps", "tasks", "on_failure", "fix_loop", "escalation", "auto_review", "judge", "on_conflict", "instructions"]);
/** A value a row can edit as one: a plain mapping (`policy`, `wait`) is read key by key, as `policy.max_attempts`. */
const isMap = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** A component's own settings, in file order, each as its dotted field. */
export function ownRows(own: Record<string, unknown> | null): Row[] {
  const rows: Row[] = [];
  const walk = (o: Record<string, unknown>, prefix: string) => {
    for (const [k, v] of Object.entries(o)) {
      if (!prefix && NOT_SETTINGS.has(k)) continue;
      const field = `${prefix}${k}`;
      if (isMap(v) && k !== "on_base_changed") walk(v, `${field}.`);
      else rows.push({ field, value: v });
    }
  };
  if (own) walk(own, "");
  return rows;
}

/** What the Overview tab already edits, per kind: Config leaves these out, so a field has one home. */
const ON_OVERVIEW: Partial<Record<LibKind, ReadonlySet<string>>> = {
  task: new Set(["prompt", "harness", "profile", "model", "effort", "fallback", "ref", "scope", "execution", "command", "target", "wait.polling.initial_interval", "policy.total_time_cap_minutes"]),
  gate: new Set(["message", "timeout"]),
};

/** The Config tab of a library component: the keys it writes that the Overview tab does not edit. The library has no
 *  inherited values to chip (its draft answers no `sources`), so each row is the component's own: ✎ edits, ↺ removes the key. */
export function LibraryConfig({ ctx, kind }: { ctx: PaneCtx; kind: LibKind }) {
  const own = authoredAt(ctx.r, ctx.scope, ctx.path);
  const all = ownRows(own);
  const rows = all.filter((x) => !ON_OVERVIEW[kind]?.has(x.field));
  if (!all.length) return <Note>This component writes no settings of its own.</Note>;
  if (!rows.length) return <Note>Everything this component writes is edited on the Overview tab.</Note>;
  return (
    <>
      <Head>Settings it writes</Head>
      <div className="cfg">{rows.map((x) => <ConfigRow key={x.field} row={x} ctx={ctx} />)}</div>
    </>
  );
}
