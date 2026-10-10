import { useContext, useEffect, useRef, useState } from "react";
import { Inspector } from "../../graph/Inspector";
import type { useResizable } from "../../graph/useResizable";
import { Button } from "../../ui/Button";
import type { Scope } from "../draft/types";
import type { ConfigDraft } from "../draft/useConfigDraft";
import { authoredAt, authoredNodes, liveChainId, nodeGlyph, normalise, problemsAt, resolvedAt, valueAt, type NodeA } from "../draft/view";
import { problemText } from "../problems";
import { Config } from "./Config";
import { ItemYaml } from "./ItemYaml";
import { crumbPath, describe, type PaneKind } from "./describe";
import { Overview, type PaneCtx } from "./Overview";
import { RenameTitle } from "./RenameTitle";
import { ChangeBaseCard, type BaseCheck } from "../cards/ChangeBaseCard";
import { RemoveCard } from "../cards/RemoveCard";
import { refsTo } from "../cards/refs";
import { ExtendMenu } from "../menus/ExtendMenu";
import { IconPicker } from "../IconPicker";
import { showToast } from "../../ui/Toast";
import { LibraryHint } from "../../library/LibraryHint";
import { MoveToLibraryCard } from "../../library/MoveToLibraryCard";
import { movable } from "../../library/moveToLibrary";
import { detailOf } from "../../http";
import "./panes.css";
import { mod } from "../../keys";
import { ReadOnly } from "../plugin";

const FIXED_ICON: Partial<Record<PaneKind, string>> = { chain: "workflow", fixloop: "refresh-cw", judge: "scale" };
const TABS = [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }];
/** Every pane but the chain's has its own YAML (Decisions §9 Item YAML). */
const TABS_YAML = [...TABS, { value: "yaml", label: "YAML" }];
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
/** Keys a component that extends a library one can own without being an override. */
const NOT_OVERRIDES = new Set(["id", "extends", "icon", "kind", "on_failure"]);

/** The side pane for whatever is selected (Decisions §9 Side pane): crumb,
 *  icon and title, subtitle, the problem row, Overview | Config, footer. */
export function ChainPane({ draft, scope, path, open, size, onCollapse, onExpand, onFocus, goTo, onDuplicate, onDeleted, onRenamed, onRemoved, onMarking, renaming: renamingAt, onRenaming }: {
  draft: ConfigDraft;
  scope: Scope;
  path: string;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
  onFocus?: () => void;
  goTo: (path: string) => void;
  /** The chain pane's Duplicate: the switcher's id step. */
  onDuplicate?: () => void;
  /** After Delete chain: the deletion is a draft change, reviewed and published. */
  onDeleted?: () => void;
  /** After a rename: the selection follows the new path. */
  onRenamed?: (from: string, to: string) => void;
  /** After a remove: back to the floor (W3's `removed`). */
  onRemoved?: () => void;
  /** The paths the open remove card lists, for the canvas to mark red. */
  onMarking?: (nodes: string[]) => void;
  /** The path whose title is edited in place, when the page holds it (a click on the
   *  selected node's name starts it; a double-click or another selection ends it). */
  renaming?: string | null;
  onRenaming?: (path: string | null) => void;
}) {
  const [card, setCard] = useState<{ t: "move" } | { t: "remove" } | { t: "extend" } | { t: "icon" } | { t: "base"; base: string; check: BaseCheck } | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  // The path whose title is being renamed in place: the page's, else this pane's own.
  const [ownRenaming, setOwnRenaming] = useState<string | null>(null);
  const renaming = onRenaming ? renamingAt ?? null : ownRenaming;
  const setRenaming = onRenaming ?? setOwnRenaming;
  const anchor = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!onRenaming) setOwnRenaming(null);
    setRefused(null);
  }, [path, onRenaming]);
  const [asking, setAsking] = useState(false);
  const [tab, setTab] = useState("overview");
  const readOnly = useContext(ReadOnly);
  const r = draft.view!.result;
  const d = describe(r, scope, path);
  // A chain renamed in the draft is titled and crumbed by its new id (R10b-02).
  const chain = scope.area === "chains" ? liveChainId(r.model, scope.key) : scope.key;
  const ctx: PaneCtx = { r, scope, path, draft, goTo };
  const own = authoredAt(r, scope, path);
  const res = resolvedAt(r, path) ?? own;
  const probs = path ? problemsAt(r, path) : r.problems.filter((p) => !p.path);
  const loneMain = (prefix: string) => {
    const c = normalise(resolvedAt(r, prefix) ?? authoredAt(r, scope, prefix));
    return !!c && c.steps.length === 1 && c.steps[0].id === "main";
  };
  const crumbs = d.kind === "chain"
    ? [{ label: "Chains" }]
    : [{ label: chain, onClick: () => goTo("") }, ...crumbPath(path, loneMain).map((c) => ({ label: c.label, onClick: () => goTo(c.path) }))];
  const overrides = own?.extends ? Object.keys(own).filter((k) => !NOT_OVERRIDES.has(k)).length : 0;
  const ext = typeof own?.extends === "string" ? ` · extends ${own.extends}${overrides ? ` · ${plural(overrides, "override")}` : ""}` : "";
  const taskKind = String(valueAt(r, scope, path, "kind") ?? "");
  const sub = (() => {
    switch (d.kind) {
      case "chain": return `chain · ${plural(authoredNodes(r, scope).length, "node")}`;
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
        return `${taskKind ? `${taskKind} task` : "task"}${ext}${valueAt(r, scope, path, "scope") === "each_repository" ? " · × each repository" : ""}`;
    }
  })();
  const tabs = d.kind === "fixloop" ? undefined : d.kind === "chain" ? TABS : TABS_YAML;
  const shownTab = tabs?.some((x) => x.value === tab) ? tab : "overview";
  const node = d.node ? (authoredNodes(r, scope).find((n) => n.id === d.node) as NodeA | undefined) : undefined;
  const glyph = d.kind === "node" ? nodeGlyph(r, path) : undefined;
  const icon = FIXED_ICON[d.kind] ?? (typeof res?.icon === "string" ? res.icon : glyph?.icon);
  const repos = r.impact.repos ?? [];
  const at = (el: HTMLElement) => void (anchor.current = el);
  const closeCard = () => {
    setCard(null);
    setRefused(null);
    onMarking?.([]);
  };
  const endRename = () => {
    setRenaming(null);
    setRefused(null);
  };
  // What a remove or a rename touches (Decisions §9 Rename, Remove).
  const isNodeLike = d.kind === "node" || d.kind === "gate";
  const refs = isNodeLike ? refsTo(r, scope, path) : [];
  const removePath = d.kind === "esc" ? `${d.node}.escalation` : path;
  const removeLabel = { node: "Remove node", gate: "Remove gate", step: "Remove step", task: "Remove task", fixloop: "Remove fix loop", judge: "Remove judge", esc: "Remove escalation", review: "Remove reviewer", chain: "" }[d.kind];
  const parent = path.split(".").slice(0, -1).join(".");
  const siblings = (() => {
    if (isNodeLike) return authoredNodes(r, scope).map((n) => n.id);
    const c = normalise(resolvedAt(r, parent) ?? authoredAt(r, scope, parent));
    if (d.kind === "step") return c?.steps.map((s) => s.id) ?? [];
    if (d.kind === "task") return (((resolvedAt(r, parent) ?? authoredAt(r, scope, parent))?.tasks as { id: string }[] | undefined) ?? []).map((x) => x.id);
    return [];
  })();
  const renameable = !readOnly && !["fixloop", "judge"].includes(d.kind);
  // Removing a task a node inherits makes this chain own that step's list (Decisions §9 Inherited items).
  const nodeOwn = d.node ? authoredAt(r, scope, d.node) : null;
  const inherits = (d.kind === "task" || d.kind === "step") && !!nodeOwn?.extends && !nodeOwn.steps;
  const rename = async (id: string) => {
    const a = await draft.ops([{ op: "rename", path, id }], { quiet: true });
    if (a.status !== 200) return setRefused(detailOf(a.body));
    const updated = (a.body.ops?.[0]?.result?.updated as unknown[] | undefined)?.length ?? 0;
    endRename();
    showToast(`Renamed ${d.id} → ${id}${updated ? ` · ${plural(updated, "reference")} updated` : ""}${d.kind === "chain" ? " · publish moves the file" : ""}`);
    onRenamed?.(path, path ? [...path.split(".").slice(0, -1), id].join(".") : "");
  };
  const remove = async () => {
    const a = await draft.ops([{ op: "remove", path: removePath }]);
    closeCard();
    if (a.status !== 200) return;
    const broken = (a.body.ops?.[0]?.result?.broken as unknown[] | undefined)?.length ?? 0;
    showToast(`Removed ${path}${broken ? ` · ${plural(broken, "reference")} now broken` : ""} · ${mod("Z")} undoes it`);
    onRemoved?.();
  };
  const pickBase = async (base: string) => {
    const a = await draft.ops([{ op: "change_base", node: path, base }], { preview: true });
    if (a.status !== 200) return showToast(detailOf(a.body));
    const res2 = (a.body.ops?.[0]?.result ?? {}) as Partial<BaseCheck>;
    setCard({ t: "base", base, check: { kept: res2.kept ?? [], dropped: res2.dropped ?? [] } });
  };
  const applyBase = async (base: string) => {
    const a = await draft.ops([{ op: "change_base", node: path, base }]);
    closeCard();
    if (a.status === 200) showToast(`Base is now ${base}`);
  };

  // R47: an exec node or a task that extends nothing can become a library component.
  const moveWhat = movable(path, own, d.kind);
  const footer = readOnly ? undefined : d.kind === "chain" ? (
    asking ? (
      <>
        <span className="tpl-rv-ask">Delete {chain}? It goes when you publish.</span>
        <span className="bp-gap" />
        <Button onClick={() => setAsking(false)}>Keep</Button>
        <Button variant="danger" onClick={async () => {
          setAsking(false);
          const a = await draft.ops([{ op: "delete_chain" }]);
          if (a.status === 200) onDeleted?.();
        }}>Delete</Button>
      </>
    ) : (
      <>
        <Button onClick={onDuplicate}>Duplicate</Button>
        <span className="bp-gap" />
        {/* Refused while a repo defaults to it (Decisions §9 Chain settings). */}
        <Button variant="danger" disabled={repos.length > 0} title={repos.length ? `Can't delete: ${repos.join(", ")} default to it` : undefined} onClick={() => setAsking(true)}>Delete chain</Button>
      </>
    )
  ) : (
    <>
      {d.kind === "node" && typeof own?.extends === "string" && <Button onClick={(e) => { at(e.currentTarget); setCard({ t: "extend" }); }}>Change base…</Button>}
      {overrides > 0 && <Button onClick={() => draft.ops([{ op: "reset_field", path }])}>Reset all overrides</Button>}
      {moveWhat && <Button onClick={(e) => { at(e.currentTarget); setCard({ t: "move" }); }}>Move to library…</Button>}
      <span className="bp-gap" />
      {removeLabel && <Button variant="danger" onClick={(e) => { at(e.currentTarget); setCard({ t: "remove" }); onMarking?.(refs.map((x) => x.node)); }}>{removeLabel}</Button>}
    </>
  );

  return (
    <>
    <Inspector
      id="chains-pane"
      open={open}
      size={size}
      crumbs={crumbs}
      gate={d.kind === "gate"}
      icon={icon}
      taskKind={glyph?.taskKind ?? (["agent", "builtin", "subprocess", "forge"].includes(taskKind) ? (taskKind as "agent") : undefined)}
      title={d.kind === "chain" ? chain : d.kind === "judge" ? "judge" : d.id}
      sub={sub}
      prob={probs.length ? { msg: problemText(probs[0]) + (probs.length > 1 ? ` (+${probs.length - 1} more)` : ""), fix: probs[0].field ? `at ${probs[0].field}${probs[0].line ? `, line ${probs[0].line}` : ""}` : probs[0].line ? `line ${probs[0].line}` : undefined } : undefined}
      tabs={tabs}
      tab={tabs ? shownTab : undefined}
      onTab={setTab}
      onCollapse={onCollapse}
      onExpand={onExpand}
      onFocus={node && onFocus ? onFocus : undefined}
      onTitle={renameable ? () => { setRefused(null); setRenaming(path); } : undefined}
      titleEdit={renameable && renaming === path ? (
        <RenameTitle key={path} what={d.kind === "chain" ? "chain" : d.kind === "gate" ? "gate" : d.kind === "node" ? "node" : d.kind === "step" ? "step" : "task"} id={d.kind === "chain" ? chain : d.id} taken={siblings} refs={refs} refused={refused} chain={d.kind === "chain"} onGo={(id) => void rename(id)} onCancel={endRename} />
      ) : undefined}
      // Nodes, steps and tasks pick an icon; the chain, gates, the fix loop and the judge have fixed ones (Decisions §9 Icons).
      onIcon={!readOnly && ["node", "step", "task", "esc", "review"].includes(d.kind) ? (el) => { at(el); setCard({ t: "icon" }); } : undefined}
      footer={footer}
    >
      {d.kind === "fixloop" ? (
        <>
          <Config kind={d.kind} ctx={ctx} />
          <Overview kind={d.kind} ctx={ctx} />
        </>
      ) : shownTab === "config" ? <Config kind={d.kind} ctx={ctx} />
        : shownTab === "yaml" ? <ItemYaml key={path} draft={draft} scope={scope} path={path} extendsName={typeof own?.extends === "string" ? own.extends : undefined} />
          : <>
            {typeof own?.extends === "string" && (d.kind === "node" || d.kind === "task") && <LibraryHint section={d.kind === "node" ? "nodes" : "tasks"} name={own.extends} />}
            <Overview kind={d.kind} ctx={ctx} />
          </>}
    </Inspector>
    {card?.t === "move" && moveWhat && own && <MoveToLibraryCard anchor={anchor} draft={draft} path={path} id={d.id} own={own} what={moveWhat} onMoved={() => {}} onClose={closeCard} />}
    {card?.t === "remove" && <RemoveCard anchor={anchor} label={removeLabel} refs={refs} note={inherits ? "This chain will then own the step's list; ↺ on the node restores it." : undefined} onRemove={() => void remove()} onClose={closeCard} />}
    {card?.t === "extend" && <ExtendMenu anchor={anchor} title="Change base" note="Next, you'll see which of this chain's overrides fit the new base." onPick={(b) => void pickBase(b)} onClose={closeCard} />}
    {card?.t === "icon" && (
      <IconPicker anchor={anchor} current={typeof own?.icon === "string" ? own.icon : undefined} onClose={closeCard} onPick={(name) => {
        closeCard();
        draft.field(path, "icon", name);
      }} />
    )}
    {card?.t === "base" && <ChangeBaseCard anchor={anchor} node={path} base={card.base} check={card.check} onApply={() => void applyBase(card.base)} onClose={closeCard} />}
    </>
  );
}
