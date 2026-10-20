import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import type { ChainNode, TemplateSummary } from "../../../types";
import { detailOf, request } from "../../http";
import { displayName } from "../../board/rowText";
import { rejectTarget } from "../../item/graph";
import { useConfigDraft } from "../../templates/draft/useConfigDraft";
import { chainCapGroups, settingRows } from "../../templates/draft/view";
import { stepsOf, taskName } from "../../item/paths";
import { addonLabel, inheritanceText } from "../../library/types";
import { coveredText } from "../../item/chainName";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";
import { useSettingRows } from "./settings";

/** Said once, on a chain: the draft here edits values; what shapes the chain stays on the desktop. */
const STRUCTURE = "Adding, removing and reordering nodes and steps is on desktop.";

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

/** A chain's size, after what it extends: "extends default · 19 nodes · 5 gates". */
const sizeOf = (c: TemplateSummary) => `${c.extends ? `extends ${c.extends} · ` : ""}${c.nodes.length} nodes · ${c.gates} ${c.gates === 1 ? "gate" : "gates"}`;
/** On an extending chain, where a node comes from, in the canvas's words; an inherited one unchanged has no mark. */
const ORIGIN = { added: "added here", overridden: "overridden here" } as const;

const nodeSub = (n: ChainNode) => (n.kind === "gate" ? `gate · reject to ${n.reject_to ?? "the nearest node before it"}` : `exec · ${(n.steps ?? n.tasks.map((t) => [t])).length} ${((n.steps ?? n.tasks.map((t) => [t])).length === 1) ? "step" : "steps"}${n.fix_loop ? " · fix loop" : ""}`);

/** `/templates/chains`: the chains (W17 brief L.1). */
export function ChainsList() {
  const { chains, error } = useChains();
  const rows: RowSpec[] = (chains ?? []).map((c) => ({ key: c.id, label: c.id, mono: true, sub: c.error ? c.error : sizeOf(c), to: `/templates/chains/${encodeURIComponent(c.id)}`, chips: c.addon ? [{ label: addonLabel(c.addon) }, ...(inheritanceText(c.addon) ? [{ label: inheritanceText(c.addon)! }] : [])] : undefined }));
  return (
    <AreaScreen title="Chains" sub="The chains an item runs: how it moves from spec to merge." status={{ label: "published" }} yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {chains && rows.length === 0 && <p className="ph-empty">No chains.</p>}
      <Group rows={rows} />
    </AreaScreen>
  );
}

/** Group heads for a setting list: what is set, then what nothing sets. */
function Settings({ rows, title }: { rows: { set: RowSpec[]; unset: RowSpec[] }; title: string }) {
  return (
    <>
      {rows.set.length > 0 && <Group title={title} rows={rows.set} />}
      {rows.unset.length > 0 && <Group title={`Not set · ${rows.unset.length}`} rows={rows.unset} />}
    </>
  );
}

/** `/templates/chains/:chain`: its nodes and its chain-wide settings, edited into the chain's draft; its YAML under the toggle. */
export function ChainView() {
  const { chain = "" } = useParams();
  const { chains, error } = useChains();
  const draft = useConfigDraft("chains", chain);
  const { edit, node: sheet } = useEditor();
  const [text, setText] = useState<string | undefined>();
  useEffect(() => {
    let live = true;
    void request<{ text?: string }>(`/templates/chains/${encodeURIComponent(chain)}`).then((r) => live && setText(r.status === 200 ? r.body.text ?? "" : `# ${detailOf(r.body)}`));
    return () => void (live = false);
  }, [chain]);
  const c = chains?.find((x) => x.id === chain);
  const nodes = c?.nodes ?? [];
  // An add-on's chain is not edited here: every write to it is refused.
  const settings = useSettingRows(draft, edit, !draft.view || !!c?.addon || !!draft.view.addon);
  const origins = draft.view?.result.extending?.origins ?? {};
  const origin = (id: string) => (origins[id] === "added" || origins[id] === "overridden" ? [{ label: ORIGIN[origins[id] as keyof typeof ORIGIN] }] : undefined);
  return (
    <AreaScreen title={chain} sub={c ? `${c.addon ? `${addonLabel(c.addon)} · ${inheritanceText(c.addon) ? `${inheritanceText(c.addon)} · ` : ""}` : ""}${sizeOf(c)}` : undefined} draft={draft} yaml={draft.view ? true : text ?? ""}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {chains && !c && <p className="ph-empty">There is no chain {chain}.</p>}
      <Group title={`Nodes · ${nodes.length}`} note={c && !c.addon ? STRUCTURE : undefined} rows={nodes.map((n) => ({ key: n.id, label: displayName(n), sub: `${n.id} · ${nodeSub(n)}`, subMono: true, chips: origin(n.id), to: `/templates/chains/${encodeURIComponent(chain)}/nodes/${encodeURIComponent(n.id)}` }))} />
      {draft.view && c && chainCapGroups(draft.view.result, draft.scope).map((g) => {
        const rows = settings("", g.rows);
        return <Group key={g.head} title={g.head} rows={[...rows.set, ...rows.unset]} />;
      })}
      {sheet}
    </AreaScreen>
  );
}

/** `/templates/chains/:chain/nodes/:node`: one node's structure and settings, or, under `?sel=`, one of its tasks'. Settings are edited into the chain's draft. */
export function ChainNodeView() {
  const { chain = "", node = "" } = useParams();
  const [params] = useSearchParams();
  const { chains } = useChains();
  const draft = useConfigDraft("chains", chain);
  const { edit, node: sheet } = useEditor();
  const c = chains?.find((x) => x.id === chain);
  const n = c?.nodes.find((x) => x.id === node);
  const task = params.get("sel");
  const settings = useSettingRows(draft, edit, !draft.view || !!c?.addon || !!draft.view.addon);
  // The path the draft's sources are keyed by: the node, or one of its tasks.
  const path = task && n && stepsOf(n).steps.some((s) => s.tasks.includes(task)) ? task : node;
  const mine = draft.view && n ? settings(path, settingRows(draft.view.result, path)) : null;
  const here = `/templates/chains/${encodeURIComponent(chain)}/nodes/${encodeURIComponent(node)}`;
  const structure: RowSpec[] = !n || path !== node ? [] : n.kind === "gate"
    ? [
        { label: "kind", value: "gate", mono: true },
        // Without the draft the gate's reject target and document are read from the published chain.
        ...(draft.view ? [] : [{ label: "reject to", value: rejectTarget(c!.nodes, n.id) ?? "the gate reopens", mono: true }]),
        { label: "auto review", value: n.auto_escalate ? "an agent reads it first" : "off" },
        ...(!draft.view && n.covered_by ? [{ label: "document", value: coveredText(n.covered_by), mono: true }] : []),
      ]
    : [
        { label: "kind", value: "exec", mono: true },
        ...stepsOf(n).steps.map((s) => ({ key: `step:${s.id}`, label: `step ${s.id}`, value: s.tasks.map(taskName).join(", "), mono: true, ...(s.tasks.length === 1 && draft.view ? { to: `${here}?sel=${encodeURIComponent(s.tasks[0])}` } : {}) })),
        ...(n.fix_loop ? [{ label: "fix loop", value: n.fix_loop, mono: true }] : []),
        ...(n.on_failure?.length ? [{ label: "on failure", value: n.on_failure.map(taskName).join(", "), mono: true }] : []),
        ...(n.rebase_bounce_to ? [{ label: "rebase bounces to", value: n.rebase_bounce_to, mono: true }] : []),
      ];
  // A step with several tasks has no one task to open: each is a row of its own.
  const tasks: RowSpec[] = !n || path !== node || !draft.view ? [] : stepsOf(n).steps.filter((s) => s.tasks.length > 1).flatMap((s) => s.tasks.map((t) => ({ key: `task:${t}`, label: taskName(t), sub: `step ${s.id}`, mono: true, to: `${here}?sel=${encodeURIComponent(t)}` })));
  return (
    <AreaScreen title={path === node ? node : taskName(path)} sub={path === node ? chain : `${chain} · ${node}`} draft={draft} yaml={false}>
      {c && !n && <p className="ph-empty">There is no node {node} in {chain}.</p>}
      <Group rows={structure} />
      {tasks.length > 0 && <Group title="Tasks" rows={tasks} />}
      {mine && <Settings title="Settings" rows={mine} />}
      {draft.view && n && !c?.addon && path === node && <p className="ph-note">{STRUCTURE}</p>}
      {sheet}
    </AreaScreen>
  );
}
