import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import * as api from "../../../api";
import type { ChainNode, TemplateSummary } from "../../../types";
import { detailOf, request } from "../../http";
import { rejectTarget } from "../../item/graph";
import { listDrafts } from "../../templates/draft/draftApi";
import { stepsOf, taskName } from "../../item/paths";
import { pluginLabel } from "../../library/types";
import { AreaScreen } from "./AreaScreen";
import { Group, type RowSpec } from "./kit";

const FOOT = "The graph canvas, drag to reorder, adding steps, drafts and publish are on desktop. This is the same chain, read here.";

function useChains() {
  const [chains, setChains] = useState<TemplateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.getTemplates().then((c) => live && setChains(c), (e: Error) => live && setError(e.message));
    return () => void (live = false);
  }, []);
  return { chains, error };
}

/** A chain's open draft, if any, so the screen can say it is not what is published (nothing here edits it). */
function useChainDraft(id: string | null) {
  const [changes, setChanges] = useState(0);
  useEffect(() => {
    let live = true;
    void listDrafts().then((a) => live && a.status === 200 && Array.isArray(a.body) && setChanges(a.body.find((d) => d.area === "chains" && d.key === id)?.changes ?? 0));
    return () => void (live = false);
  }, [id]);
  return changes;
}

const nodeSub = (n: ChainNode) => (n.kind === "gate" ? `gate · reject to ${n.reject_to ?? "the nearest node before it"}` : `exec · ${(n.steps ?? n.tasks.map((t) => [t])).length} ${((n.steps ?? n.tasks.map((t) => [t])).length === 1) ? "step" : "steps"}${n.fix_loop ? " · fix loop" : ""}`);

/** `/templates/chains`: the chains, read only (W17 brief L.1). */
export function ChainsList() {
  const { chains, error } = useChains();
  const rows: RowSpec[] = (chains ?? []).map((c) => ({ key: c.id, label: c.id, mono: true, sub: c.error ? c.error : `${c.nodes.length} nodes · ${c.gates} ${c.gates === 1 ? "gate" : "gates"}`, to: `/templates/chains/${encodeURIComponent(c.id)}`, chips: c.plugin ? [{ label: pluginLabel(c.plugin) }] : undefined }));
  return (
    <AreaScreen title="Chains" sub="The chains an item runs: how it moves from spec to merge." status={{ label: "published" }} yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {chains && rows.length === 0 && <p className="ph-empty">No chains.</p>}
      <Group rows={rows} foot={FOOT} />
    </AreaScreen>
  );
}

/** `/templates/chains/:chain`: its nodes, and its YAML under the toggle. */
export function ChainView() {
  const { chain = "" } = useParams();
  const { chains, error } = useChains();
  const draftChanges = useChainDraft(chain);
  const [text, setText] = useState<string | undefined>();
  useEffect(() => {
    let live = true;
    void request<{ text?: string }>(`/templates/chains/${encodeURIComponent(chain)}`).then((r) => live && setText(r.status === 200 ? r.body.text ?? "" : `# ${detailOf(r.body)}`));
    return () => void (live = false);
  }, [chain]);
  const c = chains?.find((x) => x.id === chain);
  const nodes = c?.nodes ?? [];
  return (
    <AreaScreen title={chain} sub={c ? `${c.plugin ? `${pluginLabel(c.plugin)} · ` : ""}${nodes.length} nodes · ${c.gates} ${c.gates === 1 ? "gate" : "gates"}` : undefined} status={draftChanges ? { label: `draft · ${draftChanges} change${draftChanges === 1 ? "" : "s"}`, tone: "warn" } : { label: "published" }} yaml={text ?? ""}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {chains && !c && <p className="ph-empty">There is no chain {chain}.</p>}
      <Group title={`Nodes · ${nodes.length}`} rows={nodes.map((n) => ({ key: n.id, label: n.id, mono: true, sub: nodeSub(n), to: `/templates/chains/${encodeURIComponent(chain)}/nodes/${encodeURIComponent(n.id)}` }))} foot={FOOT} />
    </AreaScreen>
  );
}

/** `/templates/chains/:chain/nodes/:node`: one node's fields. */
export function ChainNodeView() {
  const { chain = "", node = "" } = useParams();
  const { chains } = useChains();
  const c = chains?.find((x) => x.id === chain);
  const n = c?.nodes.find((x) => x.id === node);
  const rows: RowSpec[] = !n ? [] : n.kind === "gate"
    ? [
        { label: "kind", value: "gate", mono: true },
        { label: "reject to", value: rejectTarget(c!.nodes, n.id) ?? "the gate reopens", mono: true },
        { label: "auto review", value: n.auto_escalate ? "an agent reads it first" : "off" },
        ...(n.covered_by ? [{ label: "covered by", value: n.covered_by, mono: true }] : []),
      ]
    : [
        { label: "kind", value: "exec", mono: true },
        ...stepsOf(n).steps.map((s) => ({ label: `step ${s.id}`, value: s.tasks.map(taskName).join(", "), mono: true })),
        ...(n.fix_loop ? [{ label: "fix loop", value: n.fix_loop, mono: true }] : []),
        ...(n.on_failure?.length ? [{ label: "on failure", value: n.on_failure.map(taskName).join(", "), mono: true }] : []),
        ...(n.rebase_bounce_to ? [{ label: "rebase bounces to", value: n.rebase_bounce_to, mono: true }] : []),
      ];
  return (
    <AreaScreen title={node} sub={chain} status={{ label: "published" }} yaml={false}>
      {c && !n && <p className="ph-empty">There is no node {node} in {chain}.</p>}
      <Group rows={rows} foot={FOOT} />
    </AreaScreen>
  );
}
