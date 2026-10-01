import { useRef, useState } from "react";
import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { authoredAt, normalise, valueAt, type NodeA } from "../templates/draft/view";
import { IconPicker } from "../templates/IconPicker";
import { ItemYaml } from "../templates/panes/ItemYaml";
import type { PaneCtx } from "../templates/panes/Overview";
import { problemText } from "../templates/problems";
import { LibraryConfig } from "./config";
import { libCrumbs, libDescribe } from "./describe";
import { LibraryOverview } from "./overview";
import { Instructions } from "./steering";
import { UsedBy } from "./UsedBy";
import type { Use } from "./types";

const TABS = [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }, { value: "yaml", label: "YAML" }];
const STEERING_TABS = [{ value: "instructions", label: "Instructions" }, { value: "used", label: "Used by" }, { value: "yaml", label: "YAML" }];
const FIXED_ICON: Record<string, string> = { fixloop: "refresh-cw", judge: "scale", steering: "scroll-text" };
const SINGULAR = { nodes: "node", steps: "step", tasks: "task", steering: "steering profile" } as const;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The side pane for the selected library component or the part of it picked on its canvas (Decisions §10):
 *  crumbs, icon and title, a subtitle, the first problem, Overview | Config | YAML. The same Inspector, tabs
 *  and field rows as the Chains pane, over the library's own file. */
export function LibraryPane({ draft, path, uses, open, size, goTo, onLibrary, onCollapse, onExpand }: {
  draft: ConfigDraft;
  /** `tasks.implementer`, or a part of a node: `nodes.verification.review.code_review`. */
  path: string;
  /** The published uses of the component at its root; null until the library has loaded. */
  uses: Use[] | null;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  goTo: (path: string) => void;
  /** The "Library" crumb: back to the list with nothing selected. */
  onLibrary: () => void;
  onCollapse: () => void;
  onExpand: () => void;
}) {
  const [tab, setTab] = useState("overview");
  const [picker, setPicker] = useState(false);
  const anchor = useRef<HTMLElement | null>(null);
  const r = draft.view!.result;
  const scope = draft.scope;
  const d = libDescribe(r, path);
  if (!d) return null;
  const ctx: PaneCtx = { r, scope, path, draft, goTo };
  const own = authoredAt(r, scope, path) as NodeA | null;
  const root = d.inside === "";
  const taskKind = String(valueAt(r, scope, path, "kind") ?? "");
  const icon = FIXED_ICON[d.kind] ?? (typeof own?.icon === "string" ? own.icon : undefined);
  const probs = r.problems.filter((p) => p.component === d.component || p.path === path || p.path.startsWith(`${path}.`));
  const ext = typeof own?.extends === "string" ? ` · extends ${own.extends}` : "";
  const sub = (() => {
    const prefix = root ? `library ${SINGULAR[d.section]} · ` : "";
    switch (d.kind) {
      case "node": {
        const steps = normalise(own)?.steps ?? [];
        return `${prefix}exec${ext || ` · ${plural(steps.length, "step")} · ${plural(steps.flatMap((s) => s.tasks).length, "task")}`}`;
      }
      case "gate": return `${prefix}gate`;
      case "step": return `${prefix}step · ${plural(((own?.tasks as unknown[] | undefined) ?? []).length, "task")}`;
      case "steering": return `${prefix.replace(" · ", "")}`;
      case "fixloop": return `repairs, then re-measures from ${(normalise(authoredAt(r, scope, path.split(".").slice(0, 2).join(".")) as NodeA | null)?.steps ?? [])[0]?.id ?? "the start"}`;
      case "judge": return "judge · decides continue, accept or stop";
      case "esc": return "escalation task · runs when the node is stuck";
      case "review": return "gate reviewer";
      default: return `${prefix}${taskKind ? `${taskKind} task` : "task"}${ext}`;
    }
  })();
  const tabs = d.kind === "fixloop" ? undefined : d.kind === "steering" ? STEERING_TABS : TABS;
  const shown = tabs?.some((x) => x.value === tab) ? tab : tabs?.[0].value;
  const pickable = ["node", "step", "task", "esc", "review"].includes(d.kind);

  return (
    <>
      <Inspector
        id="library-pane"
        open={open}
        size={size}
        crumbs={[{ label: "Library", onClick: onLibrary }, ...libCrumbs(path).map((c) => ({ label: c.label, onClick: c.path ? () => goTo(c.path) : undefined }))]}
        gate={d.kind === "gate"}
        icon={icon}
        taskKind={["agent", "builtin", "subprocess", "forge"].includes(taskKind) ? (taskKind as "agent") : undefined}
        title={d.id}
        sub={sub}
        prob={probs.length ? { msg: problemText(probs[0]) + (probs.length > 1 ? ` (+${probs.length - 1} more)` : ""), fix: probs[0].chain ? `breaks chain ${probs[0].chain}` : probs[0].repo ? `breaks repo ${probs[0].repo}` : probs[0].field ? `at ${probs[0].field}` : undefined } : undefined}
        tabs={tabs}
        tab={tabs ? shown : undefined}
        onTab={setTab}
        onCollapse={onCollapse}
        onExpand={onExpand}
        onIcon={pickable ? (el) => { anchor.current = el; setPicker(true); } : undefined}
      >
        {d.kind === "fixloop" ? (
          <LibraryOverview d={d} ctx={ctx} uses={null} />
        ) : shown === "instructions" ? <Instructions draft={draft} path={path} />
          : shown === "used" ? <UsedBy uses={uses ?? []} />
          : shown === "config" ? <LibraryConfig ctx={ctx} />
          : shown === "yaml" ? <ItemYaml key={path} draft={draft} scope={scope} path={path} extendsName={typeof own?.extends === "string" ? own.extends : undefined} />
            : <LibraryOverview d={d} ctx={ctx} uses={uses} />}
      </Inspector>
      {picker && (
        <IconPicker anchor={anchor} current={typeof own?.icon === "string" ? own.icon : undefined} onClose={() => setPicker(false)} onPick={(name) => {
          setPicker(false);
          draft.field(path, "icon", name);
        }} />
      )}
    </>
  );
}
