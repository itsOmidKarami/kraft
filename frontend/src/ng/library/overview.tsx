import { Head, Kv, Note, PauseText } from "../templates/panes/controls";
import { Overview, type PaneCtx } from "../templates/panes/Overview";
import { authoredAt, normalise, valueAt, type NodeA, type Step, type Task } from "../templates/draft/view";
import type { LibDescription } from "./describe";
import { UsedBy } from "./UsedBy";
import type { Use } from "./types";

const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** A component's Overview (Decisions §10): per kind, what it writes; at a component's root, who uses it. The
 *  task kinds are W10's `Overview`, which reads `valueAt` and so follows `extends` (the library has no `sources`). */
export function LibraryOverview({ d, ctx, uses }: { d: LibDescription; ctx: PaneCtx; uses: Use[] | null }) {
  const { r, scope, path, draft, goTo } = ctx;
  const own = authoredAt(r, scope, path) as NodeA | null;
  const root = d.inside === "";
  // Chains the draft would break at this component: each is named, with its path, in Review & publish.
  const breaks = new Set(r.problems.filter((p) => root && p.chain && p.component === d.component).map((p) => p.chain)).size;
  const body = (() => {
    switch (d.kind) {
      case "node": {
        const c = normalise(own);
        const steps: Step[] = c?.steps ?? [];
        const tasks = steps.flatMap((s) => s.tasks);
        const lone = steps.length === 1 && steps[0].id === "main";
        const fix = own?.fix_loop as NodeA | null | undefined;
        const esc = own?.escalation as Task | null | undefined;
        return (
          <>
            {typeof own?.extends === "string" && <Kv k="extends" v={own.extends} mono onClick={() => goTo(`nodes.${own.extends}`)} />}
            <Head>{steps.length ? "Steps, in order" : "Steps"}</Head>
            {!steps.length && <Note>No steps yet. Open the node to add the first one.</Note>}
            {steps.map((s) => (
              <Kv key={s.id} k={lone ? s.tasks.map((t) => t.id).join(", ") || "main" : s.id} v={s.tasks.length > 1 ? `${s.tasks.length} tasks · parallel` : plural(s.tasks.length, "task")} onClick={() => goTo(`${path}.${s.id}`)} />
            ))}
            <Head>Handling</Head>
            <Kv k="produces" v={[...new Set(tasks.map((t) => t.produces).filter((x): x is string => typeof x === "string"))].join(", ") || "nothing"} mono />
            <Kv k="fix loop" v={fix ? `${plural(normalise(fix)?.steps.length ?? 0, "repair step")}${(fix as { judge?: unknown }).judge ? " · judge" : ""}` : "none"} muted={!fix} onClick={fix ? () => goTo(`${path}.fix_loop`) : undefined} />
            <Kv k="escalation" v={esc ? str(esc.id) : "none"} mono={!!esc} muted={!esc} onClick={esc ? () => goTo(`${path}.escalation.${str(esc.id)}`) : undefined} />
            <Kv k="on failure" v={own?.on_failure ? "node handler" : "none"} muted={!own?.on_failure} />
          </>
        );
      }
      case "gate":
        return (
          <>
            <PauseText label="message" long rows={2} value={str(valueAt(r, scope, path, "message"))} placeholder="What the reviewer should decide" onText={(t) => draft.field(path, "message", t, true)} onBlur={draft.flush} />
            <PauseText label="timeout" mono value={str(valueAt(r, scope, path, "timeout"))} placeholder="none · e.g. 2d" onText={(t) => draft.field(path, "timeout", t.trim() || null, true)} onBlur={draft.flush} />
            <Kv k="auto review" v={own?.auto_review ? `${str((own.auto_review as Task).id) || "reviewer"}` : "none"} muted={!own?.auto_review} onClick={own?.auto_review ? () => goTo(`${path}.auto_review`) : undefined} />
            <Note>Which document it asks about, and where Reject goes, are set in the chain that uses it.</Note>
          </>
        );
      case "step": {
        const tasks = (own?.tasks as Task[] | undefined) ?? [];
        return (
          <>
            <Head>Tasks</Head>
            {!tasks.length && <Note>No tasks yet.</Note>}
            {tasks.map((t) => <Kv key={t.id} k={t.id} v={`${str(valueAt(r, scope, `${path}.${t.id}`, "kind")) || "task"}${t.extends ? ` · extends ${str(t.extends)}` : ""}`} onClick={() => goTo(`${path}.${t.id}`)} />)}
          </>
        );
      }
      case "fixloop": {
        const loop = own as (NodeA & { judge?: Task }) | null;
        const repair = normalise(loop)?.steps ?? [];
        return (
          <>
            <Head>Run order</Head>
            <Kv k="judge" v={loop?.judge ? `${str(loop.judge.id)} · decides continue, accept or stop` : "none · repairs until attempts run out"} muted={!loop?.judge} onClick={loop?.judge ? () => goTo(`${path}.judge`) : undefined} />
            <Kv k="repair" v={repair.map((s) => (s.id === "main" && repair.length === 1 ? s.tasks.map((t) => t.id).join(", ") || "empty" : s.id)).join(" → ") || "empty"} mono />
          </>
        );
      }
      case "steering": {
        const text = str(own?.instructions);
        return <PauseText label="instructions" long rows={10} value={text} required bad={!text.trim()} sub={!text.trim() ? "Required." : undefined} onText={(t) => draft.field(path, "instructions", t, true)} onBlur={draft.flush} />;
      }
      default:
        return <Overview kind={d.kind as never} ctx={ctx} />;
    }
  })();
  return (
    <>
      {typeof own?.extends === "string" && d.kind === "task" && <Kv k="extends" v={own.extends} mono onClick={() => goTo(`tasks.${own.extends}`)} />}
      {breaks > 0 && <Note bad>{plural(breaks, "chain")} would break. Review &amp; publish names {breaks === 1 ? "it" : "them"}.</Note>}
      {body}
      {root && <UsedBy uses={uses} />}
    </>
  );
}
