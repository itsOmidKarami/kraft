import { useContext, useEffect, useRef, useState } from "react";
import { Inspector } from "../graph/Inspector";
import { detailOf } from "../http";
import { isTextField, mod } from "../keys";
import { Button } from "../ui/Button";
import { showToast } from "../ui/Toast";
import type { useResizable } from "../graph/useResizable";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { authoredAt, normalise, valueAt, type NodeA } from "../templates/draft/view";
import { ChangeBaseCard, type BaseCheck } from "../templates/cards/ChangeBaseCard";
import { RemoveCard } from "../templates/cards/RemoveCard";
import { RenameTitle } from "../templates/panes/RenameTitle";
import { fragment } from "../templates/draft/draftApi";
import { IconPicker } from "../templates/IconPicker";
import { ExtendMenu } from "../templates/menus/ExtendMenu";
import { IdCard } from "../templates/menus/IdCard";
import { ItemYaml } from "../templates/panes/ItemYaml";
import type { PaneCtx } from "../templates/panes/Overview";
import { problemText } from "../templates/problems";
import { LibraryConfig } from "./config";
import { libCrumbs, libDescribe } from "./describe";
import { LibraryOverview } from "./overview";
import { Instructions } from "./steering";
import { UsedBy } from "./UsedBy";
import type { Use } from "./types";
import { ReadOnly } from "../templates/plugin";
import { yamlOf } from "../phone/areas/yaml";

const TABS = [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }, { value: "yaml", label: "YAML" }];
const STEERING_TABS = [{ value: "instructions", label: "Instructions" }, { value: "used", label: "Used by" }, { value: "yaml", label: "YAML" }];
const FIXED_ICON: Record<string, string> = { fixloop: "refresh-cw", judge: "scale", steering: "scroll-text" };
const SINGULAR = { nodes: "node", steps: "step", tasks: "task", steering: "steering profile" } as const;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The side pane for the selected library component or the part of it picked on its canvas (Decisions §10):
 *  crumbs, icon and title, a subtitle, the first problem, Overview | Config | YAML. The same Inspector, tabs
 *  and field rows as the Chains pane, over the library's own file. */
export function LibraryPane({ draft, path, uses, names, open, size, goTo, onLibrary, onRenamed, onRemoved, onDuplicated, onCollapse, onExpand }: {
  draft: ConfigDraft;
  /** `tasks.implementer`, or a part of a node: `nodes.verification.review.code_review`. */
  path: string;
  /** The published uses of the component at its root; null until the library has loaded. */
  uses: Use[] | null;
  /** The names in this component's section, for a new id to avoid. */
  names: string[];
  open: boolean;
  size: ReturnType<typeof useResizable>;
  goTo: (path: string) => void;
  /** The "Library" crumb: back to the list with nothing selected. */
  onLibrary: () => void;
  /** After a rename: the path renamed and its new path, for the selection to follow. */
  onRenamed: (from: string, to: string) => void;
  /** After a remove: what was removed. */
  onRemoved: (path: string) => void;
  /** After Duplicate: the new component's id (`tasks.fixer_copy`). */
  onDuplicated: (id: string) => void;
  onCollapse: () => void;
  onExpand: () => void;
}) {
  const [tab, setTab] = useState("overview");
  const [card, setCard] = useState<{ t: "icon" } | { t: "remove" } | { t: "dup" } | { t: "copy" } | { t: "extend" } | { t: "base"; base: string; check: BaseCheck } | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const anchor = useRef<HTMLElement | null>(null);
  const at = (el: HTMLElement) => void (anchor.current = el);
  const closeCard = () => {
    setCard(null);
    setRefused(null);
  };
  // The path whose title is being renamed in place: another selection ends it.
  const [renaming, setRenaming] = useState<string | null>(null);
  useEffect(() => setRenaming(null), [path]);
  const startRename = () => {
    setRefused(null);
    setRenaming(path);
  };
  const endRename = () => {
    setRenaming(null);
    setRefused(null);
  };
  // F2 renames what the pane shows (Decisions §9 Rename), outside a text field.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const title = document.querySelector<HTMLElement>(".pane-title-btn");
      if (e.key !== "F2" || isTextField(e.target) || !title) return;
      e.preventDefault();
      title.click();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  const readOnly = useContext(ReadOnly);
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
  const pickable = !readOnly && ["node", "step", "task", "esc", "review"].includes(d.kind);
  const renameable = !readOnly && !["fixloop", "judge"].includes(d.kind);
  const parent = path.split(".").slice(0, -1).join(".");
  // The ids a rename must avoid: the section's names for a component, else the siblings in its step or node.
  const siblings = root ? names : ((): string[] => {
    const c = normalise(authoredAt(r, scope, parent) as NodeA | null);
    if (d.kind === "step") return c?.steps.map((x) => x.id) ?? [];
    if (d.kind === "task") return (((authoredAt(r, scope, parent) as NodeA | null)?.tasks as { id: string }[] | undefined) ?? []).map((x) => x.id);
    return [];
  })();
  const uselist = root ? (uses ?? []).map((u) => ({ node: u.chain, path: `${u.chain} · ${u.path}` })) : [];
  const noun = root ? SINGULAR[d.section] : d.kind === "esc" ? "escalation" : d.kind === "review" ? "reviewer" : d.kind === "fixloop" ? "fix loop" : d.kind;
  const removePath = d.kind === "esc" ? path.split(".").slice(0, -1).join(".") : path;
  const rename = async (id: string) => {
    const a = await draft.ops([{ op: "rename", path, id }], { quiet: true });
    if (a.status !== 200) return setRefused(detailOf(a.body));
    const updated = (a.body.ops?.[0]?.result?.updated as unknown[] | undefined)?.length ?? 0;
    endRename();
    showToast(`Renamed ${d.id} → ${id}${updated ? ` · ${plural(updated, "reference")} updated` : ""}`);
    onRenamed(path, [...path.split(".").slice(0, -1), id].join("."));
  };
  const remove = async () => {
    const a = await draft.ops([{ op: "remove", path: removePath }]);
    closeCard();
    if (a.status !== 200) return;
    const broken = (a.body.ops?.[0]?.result?.broken as unknown[] | undefined)?.length ?? 0;
    showToast(`Removed ${path}${broken ? ` · ${plural(broken, "use")} now broken` : ""} · ${mod("Z")} undoes it`);
    onRemoved(path);
  };
  // A copy is a new empty component of the same kind with the original's YAML set into it: one request, one undo.
  const duplicate = async (name: string) => {
    const frag = await fragment(scope.area, scope.key, path);
    if (frag.status !== 200) return setRefused(detailOf(frag.body));
    const kind = d.section === "nodes" ? String(own?.kind ?? "exec") : d.section === "tasks" && taskKind ? taskKind : undefined;
    const to = `${d.section}.${name}`;
    const a = await draft.ops([{ op: "add_component", section: d.section, name, ...(kind ? { kind } : {}) }, { op: "set_fragment", path: to, yaml: frag.body.text }], { quiet: true });
    if (a.status !== 200) return setRefused(detailOf(a.body));
    closeCard();
    showToast(`Duplicated as ${name}`);
    onDuplicated(to);
  };
  // A plugin's component is changed by copying it: the copy is the library's own, under a name of its own.
  const copy = async (name: string) => {
    const a = await draft.ops([{ op: "copy_component", ref: path, name }], { quiet: true });
    if (a.status !== 200) return setRefused(detailOf(a.body));
    closeCard();
    showToast(`Copied to your library as ${name}`);
    onDuplicated(String(a.body.ops?.[0]?.result?.path ?? `${d.section}.${name}`));
  };
  const pickBase = async (base: string) => {
    const a = await draft.ops([{ op: "change_base", node: path, base }], { preview: true });
    if (a.status !== 200) return showToast(detailOf(a.body));
    const res = (a.body.ops?.[0]?.result ?? {}) as Partial<BaseCheck>;
    setCard({ t: "base", base, check: { kept: res.kept ?? [], dropped: res.dropped ?? [] } });
  };
  const applyBase = async (base: string) => {
    const a = await draft.ops([{ op: "change_base", node: path, base }]);
    closeCard();
    if (a.status === 200) showToast(`Base is now ${base}`);
  };
  const footer = readOnly ? (root ? <Button variant="primary" onClick={(e) => { at(e.currentTarget); setCard({ t: "copy" }); }}>Copy to my library</Button> : undefined) : (
    <>
      {root && <Button onClick={(e) => { at(e.currentTarget); setCard({ t: "dup" }); }}>Duplicate</Button>}
      {d.kind === "node" && typeof own?.extends === "string" && <Button onClick={(e) => { at(e.currentTarget); setCard({ t: "extend" }); }}>Change base…</Button>}
      <span className="bp-gap" />
      <Button variant="danger" onClick={(e) => { at(e.currentTarget); setCard({ t: "remove" }); }}>Remove {noun}</Button>
    </>
  );

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
        onTitle={renameable ? startRename : undefined}
        titleEdit={renameable && renaming === path ? <RenameTitle key={path} what={noun} id={d.id} taken={siblings} refs={uselist} refused={refused} onGo={(id) => void rename(id)} onCancel={endRename} /> : undefined}
        onIcon={pickable ? (el) => { at(el); setCard({ t: "icon" }); } : undefined}
        footer={footer}
      >
        {d.kind === "fixloop" ? (
          <LibraryOverview d={d} ctx={ctx} uses={null} />
        ) : shown === "instructions" ? <Instructions draft={draft} path={path} />
          : shown === "used" ? <UsedBy uses={uses ?? []} />
          : shown === "config" ? <LibraryConfig ctx={ctx} />
          : shown === "yaml" ? <ItemYaml key={path} draft={draft} scope={scope} path={path} fixed={readOnly ? yamlOf(own ?? {}) : undefined} extendsName={typeof own?.extends === "string" ? own.extends : undefined} />
            : <LibraryOverview d={d} ctx={ctx} uses={uses} />}
      </Inspector>
      {card?.t === "icon" && (
        <IconPicker anchor={anchor} current={typeof own?.icon === "string" ? own.icon : undefined} onClose={closeCard} onPick={(name) => {
          closeCard();
          draft.field(path, "icon", name);
        }} />
      )}
      {card?.t === "remove" && <RemoveCard anchor={anchor} label={`Remove ${noun}`} refs={uselist} onRemove={() => void remove()} onClose={closeCard} />}
      {card?.t === "dup" && <IdCard anchor={anchor} title={`Duplicate ${d.id} as`} initial={`${d.id}_copy`} taken={names} go="Duplicate" refused={refused} onGo={(name) => void duplicate(name)} onClose={closeCard} />}
      {card?.t === "copy" && <IdCard anchor={anchor} title={`Copy ${d.id} to my library as`} initial={d.id.split(":").pop()!} taken={names} go="Copy" refused={refused} onGo={(name) => void copy(name)} onClose={closeCard} />}
      {card?.t === "extend" && <ExtendMenu anchor={anchor} title="Change base" note="Next, you'll see which of its overrides fit the new base." exclude={d.component.split(".")[1]} onPick={(b) => void pickBase(b)} onClose={closeCard} />}
      {card?.t === "base" && <ChangeBaseCard anchor={anchor} node={path} base={card.base} check={card.check} onApply={() => void applyBase(card.base)} onClose={closeCard} />}
    </>
  );
}
