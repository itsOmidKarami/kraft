import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { KraftEvent, WorkItemDocument } from "../../../types";
import { lineCount } from "../../../format";
import { NodeGlyph } from "../../graph/NodeGlyph";
import { act } from "../../item/actions";
import { chainGraph, rejectTarget } from "../../item/graph";
import { escalationsOf, ESCALATION, FIX_LOOP, nodeGraph, passOf, stateWord } from "../../item/nodeGraph";
import { stepsOf, taskName } from "../../item/paths";
import { TestsLine } from "../../item/TestsLine";
import { placeUrl, type Place } from "../../item/url";
import type { ItemDetail } from "../../item/useItem";
import { Button } from "../../ui/Button";
import { LogLines } from "../log/LogLines";
import { useLogs } from "../log/useLogs";
import { PauseSheet } from "../item/PauseSheet";
import { useDo } from "../item/useDo";
import { gateSkipped } from "../../item/events";
import { nodeSub } from "../item/model";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { ScreenHeader } from "../nav/ScreenHeader";
import { ActionBar, Block, Facts, TabStrip } from "../ui/Rows";
import { fixLoopWords, nodeBar, overrideWords, plainName, reviewPath, wallWords, type NodeAct } from "./model";
import { Strip } from "./Strip";
import { chainName } from "../../item/chainName";
import { appliedRows } from "../../item/draft/applied";
import { useApplied } from "../../item/draft/useApplied";
import { gateMessage, loopPaths, materialized, nodeAt } from "../../item/chainValues";
import "../areas/areas.css";
import { yamlOf } from "../areas/yaml";
import "./node.css";

export type NodeTab = "overview" | "log" | "config" | "yaml";
const TABS: { id: NodeTab; label: string }[] = [{ id: "overview", label: "Overview" }, { id: "log", label: "Log" }, { id: "config", label: "Config" }, { id: "yaml", label: "YAML" }];
const SOURCES = ["all", "agent", "tool", "sys", "stdout"] as const;

export interface PlaceProps {
  item: ItemDetail;
  /** Moves on every read of the item (useItem): the thread is read again on it. */
  version: string;
  events: KraftEvent[];
  docs: WorkItemDocument[];
  place: Place;
  node: string;
  now: number;
  reload: () => void;
  setPlace: (patch: Partial<Place>) => void;
}

/** `/work-items/:id/nodes/:node` (W17 brief D): the strip, Overview / Log / Config, and the node's one pair. */
export function NodeScreen({ item, version, events, docs, place, node: nodeId, now, reload, setPlace }: PlaceProps) {
  const navigate = useNavigate();
  const sheet = useSheet();
  const { busy, run } = useDo(reload);
  const api = item.chain_definition.nodes.find((n) => n.id === nodeId)!;
  const { nodes } = useMemo(() => chainGraph(item, events, now), [item, events, now]);
  const graph = nodes.find((n) => n.id === nodeId)!;
  const gate = api.kind === "gate";
  const sub = nodeSub(graph, gate && gateSkipped(events, nodeId));
  const tab = (TABS.some((t) => t.id === place.tab) ? place.tab : "overview") as NodeTab;
  const bar = nodeBar(item, api, graph);
  const message = gate ? gateMessage(materialized(item), nodeId) : undefined;
  // An applied draft edits the frozen chain, so `node_overrides` never shows it: read it from the events, as the desktop's Config does.
  const drafted = appliedRows(useApplied(item.id, version, tab === "config"), nodeId).map((r) => `${r.path} ${r.text} · applied by the draft`);
  const mine = overrideWords(item, nodeId);
  const overridden = !!mine || drafted.length > 0;
  const next = item.chain_definition.nodes[item.chain_definition.nodes.findIndex((n) => n.id === nodeId) + 1];
  // The round in the URL, as the desktop canvas has its picker: every task, the repair and the judge follow it.
  const { steps, side, loop, rounds } = useMemo(() => nodeGraph(item, api, now, events, place.round), [item, api, now, events, place.round]);
  const paths = stepsOf(api).steps;
  const escalations = escalationsOf(item, nodeId);
  const openTask = (step: string, task: string) => navigate(placeUrl(item.id, { node: nodeId, sel: { kind: "task", node: nodeId, step, task }, round: place.round }));
  const wall = wallWords(item, api, events, now);
  const m = materialized(item);
  const frozen = m ? nodeAt(m, nodeId) : undefined;
  // What will run: the frozen node with this item's overrides over it, as the server folds them; an overridden key says so.
  const own = item.node_overrides?.[nodeId] ?? {};
  const effectiveYaml = yamlOf({ ...(frozen ?? api) as unknown as Record<string, unknown>, ...own })
    .split("\n")
    .map((l) => (Object.keys(own).some((k) => l.startsWith(`${k}:`)) ? `${l}  # override` : l))
    .join("\n");
  const doc = docs.find((d) => d.path === item.gate_artifact);

  const act1 = (a: NodeAct) => {
    switch (a.id) {
      case "pause": return sheet.open("pause");
      case "resume": return void run(act.resume(item.id), "Resumed.");
      case "skip": return sheet.open("skip");
      case "retry-from": return sheet.open("rewind");
      case "retry-node": return void run(act.retry(item.id, { path: nodeId }), `Retrying ${nodeId} from its first step.`);
      case "review": return navigate(`/work-items/${encodeURIComponent(item.id)}/review?gate=${encodeURIComponent(nodeId)}`);
    }
  };
  const btn = (a: NodeAct | null, primary: boolean) => a && <Button key={a.id} className={`ph-btn${primary ? " ph-btn-primary" : ""}`} variant={primary ? "primary" : "secondary"} disabled={busy} onClick={() => act1(a)}>{a.label}</Button>;

  return (
    <>
      <ScreenHeader id={item.bead_id ?? item.id.slice(0, 8)} />
      <Strip nodes={nodes} current={nodeId} onPick={(id) => setPlace({ node: id, sel: { kind: "node", node: id }, tab: undefined, attempt: undefined })} />
      <div className="ph-content">
        <div className="ph-node-head">
          <h1 className="ph-node-title">{nodeId}</h1>
          <p className={`ph-node-sub ph-tone-${sub.tone}`}>{gate ? "gate" : "exec node"} · {sub.text}</p>
        </div>
        <TabStrip label="Node" tabs={TABS} value={tab} onChange={(t) => setPlace({ tab: t === "overview" ? undefined : t })} />
        {rounds && rounds.latest > 1 && (tab === "overview" || tab === "log") && (
          <div className="ph-attempts" role="group" aria-label="Fix loop rounds">
            {rounds.rows.map((r) => (
              <button key={r.n} type="button" className={`ph-attempt${r.n === rounds.selected ? " ph-is-on" : ""}`} aria-pressed={r.n === rounds.selected} onClick={() => setPlace({ round: r.n === rounds.latest ? undefined : r.n })}>
                round {r.n} · {r.outcome}
              </button>
            ))}
          </div>
        )}
        {tab === "overview" && (
          <>
            <Facts rows={[
              ["status", `${gate ? "gate" : "exec node"} · ${sub.text}`],
              ...(gate
                ? ([
                    ...(message ? [["message", message]] : []),
                    ...(item.test_result ? [["tests", <TestsLine key="t" result={item.test_result} linkClass="ph-linkbtn ph-mono" />]] as [string, React.ReactNode][] : []),
                    ...(doc ? [["document", <button key="d" type="button" className="ph-linkbtn ph-mono" onClick={() => navigate(`${placeUrl(item.id, { node: nodeId, sel: { kind: "node", node: nodeId } })}?doc=${encodeURIComponent(doc.document_id)}`)}>{doc.path.split("/").at(-1)}</button>]] as [string, React.ReactNode][] : []),
                    ["reject to", rejectTarget(item.chain_definition.nodes, nodeId) ? <button key="r" type="button" className="ph-linkbtn ph-mono" onClick={() => setPlace({ node: rejectTarget(item.chain_definition.nodes, nodeId)!, sel: { kind: "node", node: rejectTarget(item.chain_definition.nodes, nodeId)! } })}>{rejectTarget(item.chain_definition.nodes, nodeId)}</button> : "reopens the gate"],
                  ] as [string, React.ReactNode][])
                : ([
                    ...(api.fix_loop ? [["fix loop", fixLoopWords(item, api)]] : []),
                    ...(wall ? [["wall", wall]] : []),
                    ...(api.on_failure?.length ? [["on failure", `${api.on_failure.map(plainName).join(", ")}, once`]] : []),
                  ] as [string, React.ReactNode][])),
              ...(next ? ([["then", <button key="n" type="button" className="ph-linkbtn ph-mono" onClick={() => setPlace({ node: next.id, sel: { kind: "node", node: next.id } })}>{next.id}</button>]] as [string, React.ReactNode][]) : []),
            ]} />
            {gate && (
              <Block title="Review path">
                <ol className="ph-path">
                  {reviewPath(item, api, events).map((r) => (
                    <li key={r.title} className="ph-path-step">
                      <span className="ph-path-title">{r.title}{r.chip && <span className={`ph-chip-word ph-tone-${r.chip.tone}`}>{r.chip.word}</span>}</span>
                      <span className="ph-path-text">{r.text}</span>
                    </li>
                  ))}
                </ol>
              </Block>
            )}
            {!gate && steps.map((st, i) => (
              <Block key={st.id} title={`${st.id}${st.tasks.length > 1 ? ` · ${st.tasks.length} tasks in parallel` : ""}`}>
                <div className="ph-list">
                  {st.tasks.map((t, j) => (
                    <button key={t.id} type="button" className="ph-row ph-task-row" onClick={() => openTask(st.id, t.id)}>
                      <NodeGlyph kind="exec" size="sm" state={t.state} taskKind={t.taskKind} running={t.running} paused={t.paused} attempt={t.attempt} attemptStopped={t.attemptStopped} />
                      <span className="ph-row-text">
                        <span className="ph-row-label ph-mono">{taskName(paths[i]?.tasks[j] ?? t.id)}</span>
                        <span className="ph-row-hint">{t.meta ?? stateWord(t.state)}</span>
                      </span>
                      <ChevronRight size={16} className="ph-chev" aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </Block>
            ))}
            {!gate && loop && (
              <Block title={`Fix loop · ${loop.label}`}>
                <div className="ph-list">
                  {loop.tasks.map((t) => (
                    <button key={t.id} type="button" className="ph-row ph-task-row" onClick={() => openTask(FIX_LOOP, t.id)}>
                      <NodeGlyph kind="exec" size="sm" state={t.state} taskKind={t.taskKind} icon={t.icon} running={t.running} paused={t.paused} />
                      <span className="ph-row-text">
                        <span className="ph-row-label ph-mono">{t.label ?? t.id}</span>
                        <span className="ph-row-hint">{t.meta ?? stateWord(t.state)}</span>
                      </span>
                      <ChevronRight size={16} className="ph-chev" aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </Block>
            )}
            {!gate && side && (
              <Block title="Handlers">
                <div className="ph-list">
                  <button type="button" className="ph-row ph-task-row" onClick={() => openTask(ESCALATION, ESCALATION)}>
                    <NodeGlyph kind="exec" size="sm" state={side.state} icon="siren" />
                    <span className="ph-row-text"><span className="ph-row-label ph-mono">escalation</span><span className="ph-row-hint">{side.meta}{escalations.length > 1 ? ` · ${escalations.length} turns` : ""}</span></span>
                    <ChevronRight size={16} className="ph-chev" aria-hidden="true" />
                  </button>
                </div>
              </Block>
            )}
          </>
        )}
        {tab === "log" && <NodeLog item={item} node={nodeId} round={rounds && rounds.selected < rounds.latest ? rounds.selected : undefined} />}
        {tab === "yaml" && (
          <div className="ph-yaml">
            <p className="ph-yaml-file">As frozen at intake, with this item's overrides.</p>
            <pre className="ph-yaml-text">{effectiveYaml}</pre>
          </div>
        )}
        {tab === "config" && (
          <>
            <Facts rows={[
              ["chain", `${chainName(item)} · frozen at intake`],
              ...(api.fix_loop ? ([["fix loop", fixLoopWords(item, api)]] as [string, React.ReactNode][]) : []),
              ["on failure", api.on_failure?.length ? `${api.on_failure.map(plainName).join(", ")}, once` : gate ? `reject to ${rejectTarget(item.chain_definition.nodes, nodeId) ?? "this gate"}` : "—"],
              ["overrides", overridden ? [mine && `${mine} · changed for this item`, ...drafted].filter(Boolean).join(" · ") : "none"],
            ]} />
            <p className="ph-note">{overridden ? "An override applies to this item only. The chain file is unchanged." : "No item override on this node."}</p>
          </>
        )}
      </div>
      {(bar.secondary || bar.primary) && <ActionBar>{btn(bar.secondary, false)}{btn(bar.primary, true)}</ActionBar>}
      {sheet.is("pause") && <PauseSheet item={item} node={nodeId} sheet={sheet} reload={reload} />}
      {sheet.is("skip") && (
        <ConfirmSheet
          title={`Skip ${nodeId}?`}
          text="The node is marked skipped without running. The chain carries on at the next node."
          busy={busy}
          confirm={{ label: "Skip node", run: async () => { const r = await run(act.skip(item.id, nodeId), `Skipped ${nodeId}.`); if (r.ok) sheet.close(); } }}
          onClose={sheet.close}
        />
      )}
      {sheet.is("rewind") && (
        <ConfirmSheet
          title={`Retry from ${nodeId}?`}
          text={`The item rewinds to ${nodeId} and runs it again from its first step. Every node after it runs again afterwards.`}
          busy={busy}
          confirm={{ label: "Rewind and retry", danger: true, run: async () => { const r = await run(act.retry(item.id, { path: nodeId }), `Rewound to ${nodeId}.`); if (r.ok) sheet.close(); } }}
          onClose={sheet.close}
        />
      )}
    </>
  );
}

/** Every task's latest attempt on the node, one list, a task after the other; in an earlier fix-loop round, that round's. */
function NodeLog({ item, node, round }: { item: ItemDetail; node: string; round?: number }) {
  const [src, setSrc] = useState<(typeof SOURCES)[number]>("all");
  const latest = new Map<string, ItemDetail["worker_sessions"][number]>();
  // A repair carries the round it leads into; a task and the judge, the round they measured.
  const repair = new Set(loopPaths(materialized(item), node).repair);
  const ran = round ? passOf(item, node).filter((s) => s.round === (repair.has(s.hook_point) ? round : round - 1)) : item.worker_sessions.filter((x) => x.node_id === node);
  for (const s of [...ran].sort((a, b) => a.created_at.localeCompare(b.created_at))) latest.set(s.hook_point, s);
  const sessions = [...latest.values()].map((s) => ({ id: s.id, who: taskName(s.hook_point), running: s.status === "running" || s.status === "pending" }));
  const lines = useLogs(sessions);
  const shown = (lines ?? []).filter((l) => src === "all" || l.src === src);
  return (
    <>
      <div className="ph-chips ph-chips-inline" role="group" aria-label="Sources">
        {SOURCES.map((s) => <button key={s} type="button" className={`ph-chip${src === s ? " ph-is-on" : ""}`} aria-pressed={src === s} onClick={() => setSrc(s)}>{s}</button>)}
        <span className="ph-spacer" />
        <span className="ph-count">{lines ? lineCount(lines.length) : "Reading…"}</span>
      </div>
      <LogLines lines={shown} empty="No lines to show. Clear the filter, or the node has not started." />
    </>
  );
}
