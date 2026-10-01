import type { ChainNode } from "../../../types";

/** Everything the draft item page holds (W6 brief G). Nothing reaches the
 *  server until Create; the page keeps this in sessionStorage. */
export interface DraftState {
  title: string;
  brief: string;
  repo: string;
  chain: string;
  spec: string;
  plan: string;
  /** A bead this item implements (the server closes it on completion); blank for none. */
  bead: string;
  /** Nodes skipped by the person (never a covered one: the attachment drops those). */
  skip: string[];
  /** Auto-escalate every gate that declares a reviewer. */
  autoEscalate: boolean;
  /** Agent reviews first (`auto_gate`). */
  autoGate: boolean;
  /** Blank: the policy's budget. */
  budget: string;
  /** Item-wide fix-loop caps, for every fix-loop node without its own. Blank: policy. */
  attempts: string;
  wallMin: string;
  nodeAttempts: Record<string, string>;
  nodeWallMin: Record<string, string>;
  /** Workspace members picked (member ids), and the root-pointer policy. */
  members: string[];
  pointer: "ignore" | "bump";
}

export const emptyDraft = (p: Partial<DraftState> = {}): DraftState => ({
  title: "", brief: "", repo: "", chain: "", spec: "", plan: "", bead: "", skip: [], autoEscalate: false, autoGate: true,
  budget: "", attempts: "", wallMin: "", nodeAttempts: {}, nodeWallMin: {}, members: [], pointer: "ignore", ...p,
});

const num = (s: string | undefined) => {
  const t = (s ?? "").replace(/^\$/, "").trim();
  return t && Number.isFinite(Number(t)) ? Number(t) : null;
};

/** The node overrides the draft sets, per node: auto-escalate on gates with a
 *  reviewer (the server refuses it elsewhere), and on each fix-loop node its
 *  own caps, else the item-wide ones. A node's own value wins. */
export function nodeOverrides(d: DraftState, nodes: ChainNode[]): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const n of nodes) {
    if (d.skip.includes(n.id)) continue;
    const o: Record<string, unknown> = {};
    if (d.autoEscalate && n.kind === "gate" && n.auto_escalate === true) o.auto_escalate = true;
    if (n.fix_loop) {
      const a = num(d.nodeAttempts[n.id]) ?? num(d.attempts);
      const w = num(d.nodeWallMin[n.id]) ?? num(d.wallMin);
      if (a != null) o.attempts = a;
      if (w != null) o.wall_clock_s = w * 60;
    }
    if (Object.keys(o).length) out[n.id] = o;
  }
  return out;
}

/** `POST /work-items`' body for the draft (and, with autostart false, its dry run). */
export function draftBody(d: DraftState, nodes: ChainNode[], workspace: string | null, autostart: boolean) {
  const attachments = (["spec", "plan"] as const).filter((k) => d[k].trim()).map((k) => ({ kind: k, path: d[k].trim() }));
  const budget = num(d.budget);
  const overrides = nodeOverrides(d, nodes);
  return {
    title: d.title.trim(),
    description: d.brief.trim(),
    repo: d.repo,
    chain_template: d.chain,
    attachments,
    ...(d.bead.trim() ? { implements_beads: [d.bead.trim()] } : {}),
    skip_nodes: d.skip,
    ...(budget != null ? { budget_usd: budget } : {}),
    ...(Object.keys(overrides).length ? { node_overrides: overrides } : {}),
    auto_gate: d.autoGate,
    ...(workspace && d.members.length ? { workspace, members: d.members, root_pointer_policy: d.pointer } : {}),
    autostart,
  };
}
