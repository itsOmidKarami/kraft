import { useContext, useMemo, useRef, useState } from "react";
import type { ChainArc, ChainNode, Seam } from "../graph/layout";
import { EDITOR_FIT } from "../graph/camera";
import { StageGraph } from "../graph/StageGraph";
import type { Op, Result, Scope } from "./draft/types";
import { authoredNodes, changeAt, extendsOf, inheritedAt, inheritedName, kindOf, nodeGlyph, originOf, problemsAt, type NodeA } from "./draft/view";
import { SeamMenu } from "./menus/SeamMenu";
import { ReadOnly } from "./addon";
import { problemWord } from "./problems";
import { Empty } from "../ui/Empty";

type Props = {
  scope: Scope;
  result: Result;
  selected?: string;
  pending: Op[] | null;
  reserve: number;
  /** The server's refusal of the last add, shown on the id field. */
  refused?: string | null;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  onFocusNode: (id: string) => void;
  onEscape: () => void;
  onBackground: () => void;
  onAdd: (at: number, kind: "exec" | "gate", id: string) => Promise<boolean>;
  /** Review & publish (Decisions §9 Publish): the published node order, for the
   *  removed nodes' ghosts, and the node a change row points at. */
  review?: { published: { id: string; kind: "exec" | "gate" }[]; highlight?: string };
  /** Nodes an open remove card lists: ringed red while it is open. */
  marked?: string[];
  /** Drag to reorder, and the strip that checks the move live (Decisions §9 Reorder). */
  onDrag?: { over: (id: string, to: number) => void; drop: (id: string, to: number) => void; end: () => void };
  strip?: { ok: boolean; text: string } | null;
};

/** The node an op touches, for the pending highlight (Decided 3). */
const opNode = (o: Op) => String(o.path ?? o.node ?? o.container ?? o.id ?? "").split(".")[0];

/** R23: a gate with no reject_to rejects to the nearest earlier exec node. */
function rejectTarget(r: Result, nodes: NodeA[], i: number): string | undefined {
  const g = nodes[i];
  if (typeof g.reject_to === "string") return g.reject_to;
  for (let k = i - 1; k >= 0; k--) if (kindOf(r, nodes[k]) === "exec") return nodes[k].id;
}

/** Level 1 of the editor: the chain drawn from the draft's model (brief B.2). */
export function ChainCanvas({ scope, result: r, selected, pending, reserve, refused, onSelect, onOpen, onFocusNode, onEscape, onBackground, onAdd, review, marked = [], onDrag, strip }: Props) {
  const authored = authoredNodes(r, scope);
  // An extending chain: each node marked by where it comes from, its parent's removed ones ghosts.
  const parent = extendsOf(r, scope);
  const busy = useMemo(() => new Set((pending ?? []).map(opNode)), [pending]);
  const nodes: ChainNode[] = authored.map((n) => {
    const kind = kindOf(r, n);
    const change = changeAt(r, n.id);
    // A change inside a node marks the node changed (Decisions §9 Publish: "changed ones get amber ~").
    const inside = r.changes.some((c) => c.path.startsWith(`${n.id}.`));
    const probs = problemsAt(r, n.id, true);
    const gate = kind === "gate";
    const glyph = gate ? {} : nodeGlyph(r, n.id);
    const origin = parent ? originOf(r, n.id) : undefined;
    const originMeta = origin === "added" ? "added here" : origin === "overridden" ? "overridden here" : undefined;
    // A child add-on's own task at this node ranks below a problem and below the chain-extends origin.
    const inh = inheritedAt(r, n.id);
    const inhMeta = inh && `${inheritedName(inh.entry)} ${inh.origin} here`;
    return {
      id: n.id,
      kind,
      icon: typeof n.icon === "string" ? n.icon : r.resolved?.nodes.find((x) => x.id === n.id)?.icon ?? glyph.icon,
      taskKind: glyph.taskKind,
      state: "plain",
      mark: change?.kind === "add" ? "add" : change?.kind === "change" || inside ? "change" : undefined,
      prob: probs.length > 0 || marked.includes(n.id),
      // Gates on the chain show only the diamond and name (Decisions §9).
      meta: probs.length && !gate ? problemWord(probs[0]) : originMeta ?? inhMeta,
      metaTone: probs.length ? "red" : origin === "added" ? "green" : origin === "overridden" ? "amber" : inh ? (inh.origin === "added" ? "green" : "amber") : undefined,
      pending: busy.has(n.id),
    };
  });
  if (parent && !review) {
    // Each removed parent node right after the node it followed (the server knows the parent's order).
    for (const g of r.extending?.removed ?? []) {
      let at = g.after ? nodes.findIndex((n) => n.id === g.after) + 1 : 0;
      while (nodes[at]?.state === "ghost") at++; // a run of removals keeps the parent's order
      nodes.splice(at, 0, { id: g.id, kind: g.kind, state: "ghost", meta: "removed" });
    }
  }
  if (review) {
    // Unchanged nodes fade; changed ones carry their one-line summary; removed ones are ghosts where they were.
    nodes.forEach((n) => {
      const own = r.changes.filter((c) => c.path === n.id || c.path.startsWith(`${n.id}.`));
      n.faded = !n.mark;
      n.meta = n.mark === "add" ? `new ${n.kind === "gate" ? "gate" : "exec node"}` : own.length === 1 ? `${own[0].path === n.id ? "" : `${own[0].path.slice(n.id.length + 1)} · `}${own[0].summary}` : own.length ? `${own.length} changes` : undefined;
      n.metaTone = n.mark === "add" ? "green" : n.mark ? "amber" : undefined;
    });
    review.published.forEach((p, i) => {
      if (nodes.some((n) => n.id === p.id) || !r.changes.some((c) => c.path === p.id && c.kind === "remove")) return;
      nodes.splice(Math.min(i, nodes.length), 0, { id: p.id, kind: p.kind, state: "ghost", meta: "removed" });
    });
  }
  const [menu, setMenu] = useState<number | null>(null);
  const anchor = useRef<HTMLElement | null>(null);
  // No seams while reviewing: the review is read-only (the prototype's `!s.review`).
  const readOnly = useContext(ReadOnly);
  /** The live nodes before display index `k` (without `skip`): what `add_node` and `move` index; a ghost is no node. */
  const live = (k: number, skip?: string) => nodes.filter((n) => n.id !== skip).slice(0, k).filter((n) => n.state !== "ghost").length;
  // An extending chain adds after one of its parent's nodes: no seam before the first.
  const seams: Seam[] = review || readOnly ? [] : Array.from({ length: nodes.length + 1 }, (_, at) => ({ at, open: menu === at, always: nodes.length === 0 })).filter((x) => !parent || live(x.at) > 0);
  const drag = parent && onDrag ? { ...onDrag, over: (id: string, to: number) => onDrag.over(id, live(to, id)), drop: (id: string, to: number) => onDrag.drop(id, live(to, id)) } : onDrag;
  const unapplied = !!parent && r.extending?.nodes === null;
  const sel = authored.findIndex((n) => n.id === selected);
  const target = sel >= 0 && kindOf(r, authored[sel]) === "gate" ? rejectTarget(r, authored, sel) : undefined;
  const arcs: ChainArc[] = target && selected ? [{ kind: "reject", from: selected, to: target }] : [];
  const close = () => {
    setMenu(null);
    anchor.current?.focus();
  };
  return (
    <>
      <StageGraph
        name={scope.key}
        nodes={nodes}
        selected={review?.highlight ?? selected}
        arcs={arcs}
        seams={seams}
        opening="fit"
        fit={EDITOR_FIT}
        // A long chain draws under its overview; its nodes keep their ids, which are what an author edits.
        overview
        onDrag={review || readOnly ? undefined : drag}
        reserve={reserve}
        onSelect={onSelect}
        onOpen={onOpen}
        onFocusNode={onFocusNode}
        onEscape={onEscape}
        onBackground={onBackground}
        onSeam={(at, el) => {
          anchor.current = el;
          setMenu(at);
        }}
      />
      {!nodes.length && !readOnly && !unapplied && <Empty className="tpl-empty-chain">Add the first node with +</Empty>}
      {strip && <p className={`tpl-strip${strip.ok ? "" : " is-bad"}`} role="status">{strip.text}</p>}
      {menu !== null && (
        <SeamMenu
          anchor={anchor}
          taken={[...authored.map((n) => n.id), ...(r.extending?.removed.map((g) => g.id) ?? [])]}
          refused={refused}
          onClose={close}
          onCreate={async (kind, id) => {
            if (await onAdd(parent ? live(menu) : menu, kind, id)) setMenu(null);
          }}
        />
      )}
    </>
  );
}
