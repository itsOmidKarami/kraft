import { useMemo, useRef, useState } from "react";
import type { ChainArc, ChainNode, Seam } from "../graph/layout";
import { StageGraph } from "../graph/StageGraph";
import type { Op, Result } from "./draft/types";
import { authoredNodes, changeAt, kindOf, problemsAt, type NodeA } from "./draft/view";
import { SeamMenu } from "./menus/SeamMenu";
import { problemWord } from "./problems";

type Props = {
  chain: string;
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
};

/** The editor opens readable: no smaller than 80%, a long chain starting at its left end (Templates prototype `fit1`). */
export const EDITOR_FIT = { floor: 0.8, left: 12 } as const;

/** The node an op touches, for the pending highlight (Decided 3). */
const opNode = (o: Op) => String(o.path ?? o.node ?? o.container ?? o.id ?? "").split(".")[0];

/** R23: a gate with no reject_to rejects to the nearest earlier exec node. */
function rejectTarget(r: Result, nodes: NodeA[], i: number): string | undefined {
  const g = nodes[i];
  if (typeof g.reject_to === "string") return g.reject_to;
  for (let k = i - 1; k >= 0; k--) if (kindOf(r, nodes[k]) === "exec") return nodes[k].id;
}

/** Level 1 of the editor: the chain drawn from the draft's model (brief B.2). */
export function ChainCanvas({ chain, result: r, selected, pending, reserve, refused, onSelect, onOpen, onFocusNode, onEscape, onBackground, onAdd }: Props) {
  const authored = authoredNodes(r, chain);
  const busy = useMemo(() => new Set((pending ?? []).map(opNode)), [pending]);
  const nodes: ChainNode[] = authored.map((n) => {
    const kind = kindOf(r, n);
    const change = changeAt(r, n.id);
    // A change inside a node marks the node changed (Decisions §9 Publish: "changed ones get amber ~").
    const inside = r.changes.some((c) => c.path.startsWith(`${n.id}.`));
    const probs = problemsAt(r, n.id, true);
    const gate = kind === "gate";
    return {
      id: n.id,
      kind,
      icon: typeof n.icon === "string" ? n.icon : r.resolved?.nodes.find((x) => x.id === n.id)?.icon ?? undefined,
      state: "plain",
      mark: change?.kind === "add" ? "add" : change?.kind === "change" || inside ? "change" : undefined,
      prob: probs.length > 0,
      // Gates on the chain show only the diamond and name (Decisions §9).
      meta: probs.length && !gate ? problemWord(probs[0]) : undefined,
      metaTone: probs.length ? "red" : undefined,
      pending: busy.has(n.id),
    };
  });
  const [menu, setMenu] = useState<number | null>(null);
  const anchor = useRef<HTMLElement | null>(null);
  const seams: Seam[] = Array.from({ length: nodes.length + 1 }, (_, at) => ({ at, open: menu === at, always: nodes.length === 0 }));
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
        name={chain}
        nodes={nodes}
        selected={selected}
        arcs={arcs}
        seams={seams}
        opening="fit"
        fit={EDITOR_FIT}
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
      {!nodes.length && <p className="tpl-empty-chain">Add the first node with +</p>}
      {menu !== null && (
        <SeamMenu
          anchor={anchor}
          taken={authored.map((n) => n.id)}
          refused={refused}
          onClose={close}
          onCreate={async (kind, id) => {
            if (await onAdd(menu, kind, id)) setMenu(null);
          }}
        />
      )}
    </>
  );
}
