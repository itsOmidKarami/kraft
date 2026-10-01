import { useState } from "react";
import { Inspector } from "../../graph/Inspector";
import type { useResizable } from "../../graph/useResizable";
import { Button } from "../../ui/Button";
import type { ConfigDraft } from "../draft/useConfigDraft";
import { authoredAt, authoredNodes, normalise, problemsAt, resolvedAt, valueAt, type NodeA } from "../draft/view";
import { problemText } from "../problems";
import { Config } from "./Config";
import { crumbPath, describe, type PaneKind } from "./describe";
import { Overview, type PaneCtx } from "./Overview";
import "./panes.css";

const FIXED_ICON: Partial<Record<PaneKind, string>> = { chain: "workflow", fixloop: "refresh-cw", judge: "scale" };
const TABS = [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }];
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
/** Keys a component that extends a library one can own without being an override. */
const NOT_OVERRIDES = new Set(["id", "extends", "icon", "kind", "on_failure"]);

/** The side pane for whatever is selected (Decisions §9 Side pane): crumb,
 *  icon and title, subtitle, the problem row, Overview | Config, footer. */
export function ChainPane({ draft, chain, path, open, size, onCollapse, onExpand, onFocus, goTo }: {
  draft: ConfigDraft;
  chain: string;
  path: string;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
  onFocus?: () => void;
  goTo: (path: string) => void;
}) {
  const [tab, setTab] = useState("overview");
  const r = draft.view!.result;
  const d = describe(r, chain, path);
  const ctx: PaneCtx = { r, chain, path, draft, goTo };
  const own = authoredAt(r, chain, path);
  const res = resolvedAt(r, path) ?? own;
  const probs = path ? problemsAt(r, path) : r.problems.filter((p) => !p.path);
  const loneMain = (prefix: string) => {
    const c = normalise(resolvedAt(r, prefix) ?? authoredAt(r, chain, prefix));
    return !!c && c.steps.length === 1 && c.steps[0].id === "main";
  };
  const crumbs = d.kind === "chain"
    ? [{ label: "Chains" }]
    : [{ label: chain, onClick: () => goTo("") }, ...crumbPath(path, loneMain).map((c) => ({ label: c.label, onClick: () => goTo(c.path) }))];
  const overrides = own?.extends ? Object.keys(own).filter((k) => !NOT_OVERRIDES.has(k)).length : 0;
  const ext = typeof own?.extends === "string" ? ` · extends ${own.extends}${overrides ? ` · ${plural(overrides, "override")}` : ""}` : "";
  const taskKind = String(valueAt(r, chain, path, "kind") ?? "");
  const sub = (() => {
    switch (d.kind) {
      case "chain": return `chain · ${plural(authoredNodes(r, chain).length, "node")}`;
      case "gate": return `gate${res?.chain_finalized ? " · final review" : ""}`;
      case "node": {
        const steps = draft.resolvedNode(path)?.steps ?? [];
        return ext ? `exec node${ext}` : `exec node · ${plural(steps.length, "step")} · ${plural(steps.flatMap((s) => s.tasks).length, "task")}`;
      }
      case "step": {
        const n = (res?.tasks as unknown[] | undefined)?.length ?? 0;
        return `step · ${n === 0 ? "no tasks yet" : n === 1 ? "1 task" : `${n} tasks run in parallel`}`;
      }
      case "fixloop": {
        const first = draft.resolvedNode(d.node)?.steps?.[0]?.id;
        return `repairs, then re-measures from ${first ?? "the start"}`;
      }
      default:
        return `${taskKind ? `${taskKind} task` : "task"}${ext}${valueAt(r, chain, path, "scope") === "each_repository" ? " · × each repository" : ""}`;
    }
  })();
  const tabs = d.kind === "fixloop" ? undefined : TABS;
  const node = d.node ? (authoredNodes(r, chain).find((n) => n.id === d.node) as NodeA | undefined) : undefined;
  const icon = FIXED_ICON[d.kind] ?? (typeof res?.icon === "string" ? res.icon : undefined);
  const footer = overrides > 0 ? <Button onClick={() => draft.ops([{ op: "reset_field", path }])}>Reset all overrides</Button> : undefined;
  return (
    <Inspector
      id="chains-pane"
      open={open}
      size={size}
      crumbs={crumbs}
      gate={d.kind === "gate"}
      icon={icon}
      taskKind={["agent", "builtin", "subprocess", "forge"].includes(taskKind) ? (taskKind as "agent") : undefined}
      title={d.kind === "chain" ? chain : d.kind === "judge" ? "judge" : d.id}
      sub={sub}
      prob={probs.length ? { msg: problemText(probs[0]) + (probs.length > 1 ? ` (+${probs.length - 1} more)` : ""), fix: probs[0].field ? `at ${probs[0].field}${probs[0].line ? `, line ${probs[0].line}` : ""}` : probs[0].line ? `line ${probs[0].line}` : undefined } : undefined}
      tabs={tabs}
      tab={tabs ? tab : undefined}
      onTab={setTab}
      onCollapse={onCollapse}
      onExpand={onExpand}
      onFocus={node && onFocus ? onFocus : undefined}
      footer={footer}
    >
      {d.kind === "fixloop" ? (
        <>
          <Config kind={d.kind} ctx={ctx} />
          <Overview kind={d.kind} ctx={ctx} />
        </>
      ) : tab === "config" ? <Config kind={d.kind} ctx={ctx} /> : <Overview kind={d.kind} ctx={ctx} />}
    </Inspector>
  );
}
