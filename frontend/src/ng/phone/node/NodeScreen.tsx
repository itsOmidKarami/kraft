import { breakable } from "../../ui/breakable";
import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { KraftEvent, WorkItemDocument } from "../../../types";
import { lineCount } from "../../../format";
import { NodeGlyph } from "../../graph/NodeGlyph";
import { GATE_ROUNDS } from "../../graph/sharedWords";
import { act } from "../../item/actions";
import { chainGraph, drawnOf, rejectTarget } from "../../item/graph";
import { escalationsIn, ESCALATION, FIX_LOOP, nodeGraph, passList, passOf, stateWord } from "../../item/nodeGraph";
import { stepsOf, taskName } from "../../item/paths";
import { TestsLine } from "../../item/TestsLine";
import { placeUrl, type Place } from "../../item/url";
import type { ItemDetail } from "../../item/useItem";
import { Button } from "../../ui/Button";
import { LogLines } from "../log/LogLines";
import { useLogs } from "../log/useLogs";
import { PauseSheet } from "../item/PauseSheet";
import { useDo } from "../item/useDo";
import { byWord, gateState, isReviewRound, newestOutcome, noteDoc, outcomeWord, ownOutcome, reviewDoc, roundShown, roundsOf, shownNote, threadCountLine, threadWhere } from "../../item/gateRounds";
import { roundReviewUrl } from "../../review/url";
import { LABEL_WORD } from "../../review/sharedWords";
import { nodeSub } from "../item/model";
import { ConfirmSheet, ReadSheet, useSheet } from "../nav/Sheet";
import { InlineMarkdown, Markdown } from "../../ui/Markdown";
import { NotePreview } from "../../ui/NotePreview";
import { ScreenHeader } from "../nav/ScreenHeader";
import { ActionBar, Block, Facts, TabStrip } from "../ui/Rows";
import { fixLoopWords, nodeBar, overrideWords, plainName, reviewPath, wallWords, type NodeAct } from "./model";
import { Strip } from "./Strip";
import { chainName } from "../../item/chainName";
import { appliedRows } from "../../item/draft/applied";
import { useApplied } from "../../item/draft/useApplied";
import { gateMessage, loopPaths, materialized, nodeAt } from "../../item/chainValues";
import { nameOf } from "../../board/rowText";
import { findNode, nextNode } from "../../item/cursor";
import { childOf, groupSteps, PHASE_WORDS, tasksWords } from "../../item/groupView";
import "../areas/areas.css";
import { yamlOf } from "../areas/yaml";
import "./node.css";
import { FIX_LOOP_ROUNDS } from "../../graph/sharedWords";
import { Chip } from "../ui/Chip";
import { Badge } from "../ui/Badge";

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
export function NodeScreen({ item, seen = item, version, events, docs, place, node: nodeId, now, reload, setPlace }: PlaceProps & { /** The item as the pass shown left it (`asOfPass`): what the tasks, the fix loop and the log read. */ seen?: ItemDetail }) {
  const navigate = useNavigate();
  const sheet = useSheet();
  const { busy, run } = useDo(reload);
  const name = nameOf(item.chain_definition.nodes, nodeId);
  const api = findNode(item.chain_definition.nodes, nodeId)!;
  const { nodes } = useMemo(() => chainGraph(item, now), [item, now]);
  const graph = drawnOf(nodes, nodeId)!;
  const gate = api.kind === "gate";
  const kindWords = gate ? "gate" : api.kind === "group" ? "group node" : "exec node";
  // A gate sent back says what it came to ("changes requested · you"), as the desktop's pane does; else today's words.
  const outcome = gate ? newestOutcome(item, nodeId) : undefined;
  const base = nodeSub(graph, gate && gateState(item, nodeId).words === "skipped");
  const sub = outcome ? { ...base, text: outcome } : base;
  const tab = (TABS.some((t) => t.id === place.tab) ? place.tab : "overview") as NodeTab;
  const bar = nodeBar(item, api, graph);
  const message = gate ? gateMessage(materialized(item), nodeId) : undefined;
  // An applied draft edits the frozen chain, so `node_overrides` never shows it: read it from the events, as the desktop's Config does.
  const drafted = appliedRows(useApplied(item.id, version, tab === "config"), nodeId).map((r) => `${r.path} ${r.text} · applied by the draft`);
  const mine = overrideWords(item, nodeId);
  const overridden = !!mine || drafted.length > 0;
  const next = nextNode(item.chain_definition.nodes, nodeId);
  // The round in the URL, as the desktop canvas has its picker: every task, the repair and the judge follow it.
  const { steps, side, loop, onFailure, rounds } = useMemo(() => nodeGraph(seen, api, now, events, place.round), [seen, api, now, events, place.round]);
  const passes = passList(item, nodeId, place.pass);
  // A gate's passes are its rounds: the same `?pass=`, read from the server's rounds.
  const gateRounds = gate ? roundsOf(item, nodeId) : [];
  const round = gate ? roundShown(item, nodeId, place.pass) : undefined;
  // A finished review round shows its review where a note round shows its note: the pending round has no review yet.
  const reviewed = !!round?.ended_at && isReviewRound(round);
  const roundNote = round && shownNote(round) ? noteDoc(nodeId, round) : null;
  const roundReview = round && reviewed ? reviewDoc(nodeId, round) : null;
  const paths = stepsOf(api).steps;
  const escalations = escalationsIn(seen, nodeId);
  const openTask = (step: string, task: string) => navigate(placeUrl(item.id, { node: nodeId, sel: { kind: "task", node: nodeId, step, task }, round: place.round, pass: place.pass }));
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
      case "retry-node": return void run(act.retry(item.id, { path: nodeId }), `Retrying ${name} from its first step.`);
      case "review": return navigate(`/work-items/${encodeURIComponent(item.id)}/review?gate=${encodeURIComponent(nodeId)}`);
    }
  };
  const btn = (a: NodeAct | null, primary: boolean) => a && <Button key={a.id} className={`ph-btn${primary ? " ph-btn-primary" : ""}`} variant={primary ? "primary" : "secondary"} disabled={busy} onClick={() => act1(a)}>{a.label}</Button>;

  return (
    <>
      <ScreenHeader id={item.bead_id ?? item.id.slice(0, 8)} />
      <Strip nodes={nodes} current={nodeId} name={(id) => nameOf(item.chain_definition.nodes, id)} onPick={(id) => setPlace({ node: id, sel: { kind: "node", node: id }, tab: undefined, attempt: undefined })} />
      <div className="ph-content">
        <div className="ph-node-head">
          <h1 className="ph-node-title">{breakable(nodeId)}</h1>
          <p className={`ph-node-sub ph-tone-${sub.tone}`}>{kindWords} · {sub.text}</p>
        </div>
        <TabStrip label="Node" tabs={TABS} value={tab} onChange={(t) => setPlace({ tab: t === "overview" ? undefined : t })} />
        {passes && !gate && (tab === "overview" || tab === "log") && (
          <div className="ph-attempts" role="group" aria-label="Passes">
            {passes.rows.map((p) => (
              // Another pass has its own rounds: the round picked does not carry over.
              <button key={p.n} type="button" className={`ph-attempt${p.n === passes.selected ? " ph-is-on" : ""}`} aria-pressed={p.n === passes.selected} onClick={() => setPlace({ pass: p.n === passes.latest ? undefined : p.n, round: undefined })}>
                pass {p.n} · {p.outcome}
              </button>
            ))}
          </div>
        )}
        {gate && round && (tab === "overview" || tab === "log") && (
          <>
            {gateRounds.length > 1 && (
              <div className="ph-attempts ph-is-wrap" role="group" aria-label={GATE_ROUNDS}>
                {gateRounds.map((r) => (
                  <button key={r.n} type="button" className={`ph-attempt${r.n === round.n ? " ph-is-on" : ""}`} aria-pressed={r.n === round.n} onClick={() => setPlace({ pass: r.n === gateRounds.length ? undefined : r.n })}>
                    round {r.n} · {outcomeWord(r)}
                  </button>
                ))}
              </div>
            )}
            {round.outcome !== "pending" && (
              <div className="ph-turn" aria-label={`Round ${round.n}`}>
                <p className="ph-turn-head"><span>{outcomeWord(round)}{byWord(round.by) ? ` · ${byWord(round.by)}` : ""}{round.outcome === "rejected" && round.sent_to ? ` · sent to ${nameOf(item.chain_definition.nodes, round.sent_to)}` : ""}</span></p>
                {/* A person's note shows beside the review: it is what the next agent was told. The thread bounce's does not (it is the threads as text). */}
                {shownNote(round) && <NotePreview className="ph-turn-text" note={shownNote(round)!} buttonClass="ph-linkbtn" onRead={() => sheet.open("round-note")} />}
                {reviewed && (
                  <>
                    {ownOutcome(round) && <p className="ph-turn-text">{ownOutcome(round)}</p>}
                    {round.review?.summary && <NotePreview className="ph-turn-text" note={round.review.summary} label="Read the review" buttonClass="ph-linkbtn" onRead={() => sheet.open("round-review")} />}
                    <p className="ph-turn-count">{threadCountLine(round)}</p>
                    {round.first_threads.length > 0 && (
                      <ul className="ph-turn-threads">
                        {round.first_threads.map((t) => <li key={t.id}>{[threadWhere(t), t.label && LABEL_WORD[t.label]].filter(Boolean).join(" · ")} · <InlineMarkdown text={t.body} /></li>)}
                      </ul>
                    )}
                    <Link className="ph-linkbtn" to={roundReviewUrl(item.id, nodeId, round.n)}>Open in review</Link>
                  </>
                )}
              </div>
            )}
          </>
        )}
        {rounds && rounds.rows.length > 1 && (tab === "overview" || tab === "log") && (
          <div className="ph-attempts" role="group" aria-label={FIX_LOOP_ROUNDS}>
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
              ["status", `${kindWords} · ${sub.text}`],
              ...(item.group?.phase && api.kind === "group" ? ([["phase", PHASE_WORDS[item.group.phase]]] as [string, React.ReactNode][]) : []),
              ...(gate
                ? ([
                    ...(message ? [["message", message]] : []),
                    ...(item.test_result ? [["tests", <TestsLine key="t" workItemId={item.id} result={item.test_result} linkClass="ph-linkbtn ph-mono" />]] as [string, React.ReactNode][] : []),
                    ...(doc ? [["document", <button key="d" type="button" className="ph-linkbtn ph-mono" onClick={() => navigate(`${placeUrl(item.id, { node: nodeId, sel: { kind: "node", node: nodeId } })}?doc=${encodeURIComponent(doc.document_id)}`)}>{doc.path.split("/").at(-1)}</button>]] as [string, React.ReactNode][] : []),
                    ["reject to", rejectTarget(item.chain_definition.nodes, nodeId) ? <button key="r" type="button" className="ph-linkbtn" onClick={() => setPlace({ node: rejectTarget(item.chain_definition.nodes, nodeId)!, sel: { kind: "node", node: rejectTarget(item.chain_definition.nodes, nodeId)! } })}>{nameOf(item.chain_definition.nodes, rejectTarget(item.chain_definition.nodes, nodeId)!)}</button> : "reopens the gate"],
                  ] as [string, React.ReactNode][])
                : ([
                    ...(api.fix_loop ? [["fix loop", fixLoopWords(seen, api)]] : []),
                    ...(wall ? [["wall", wall]] : []),
                    ...(api.on_failure?.length ? [["on failure", `${api.on_failure.map(plainName).join(", ")}, once`]] : []),
                  ] as [string, React.ReactNode][])),
              ...(next ? ([["then", <button key="n" type="button" className="ph-linkbtn ph-mono" onClick={() => setPlace({ node: next.id, sel: { kind: "node", node: next.id } })}>{next.id}</button>]] as [string, React.ReactNode][]) : []),
            ]} />
            {gate && (
              <Block title="Review path">
                <ol className="ph-path">
                  {reviewPath(item, api, place.pass).map((r) => (
                    <li key={r.title} className="ph-path-step">
                      <span className="ph-path-title">{r.title}{r.chip && <Badge tone={r.chip.tone}>{r.chip.word}</Badge>}</span>
                      <span className="ph-path-text">{r.text}</span>
                    </li>
                  ))}
                </ol>
              </Block>
            )}
            {api.kind === "group" && <GroupBlocks item={seen} steps={groupSteps(seen, graph)} onNode={(id) => setPlace({ node: `${nodeId}.${id}`, sel: { kind: "node", node: `${nodeId}.${id}` } })} onItem={(id) => navigate(`/work-items/${encodeURIComponent(id)}`)} />}
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
            {!gate && onFailure?.map((h) => (
              <Block key={`${h.step}/${h.at?.task ?? ""}`} title={h.label.replace(/^on/, "On")}>
                <div className="ph-list">
                  {h.tasks.map((t) => (
                    <button key={t.id} type="button" className="ph-row ph-task-row" onClick={() => openTask(h.step, t.id)}>
                      <NodeGlyph kind="exec" size="sm" state={t.state} taskKind={t.taskKind} running={t.running} paused={t.paused} />
                      <span className="ph-row-text">
                        <span className="ph-row-label ph-mono">{t.label ?? t.id}</span>
                        <span className="ph-row-hint">{t.meta ?? stateWord(t.state)}</span>
                      </span>
                      <ChevronRight size={16} className="ph-chev" aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </Block>
            ))}
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
        {tab === "log" && <NodeLog item={seen} node={nodeId} round={rounds && rounds.selected < rounds.latest ? rounds.selected : undefined} />}
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
              ...(api.fix_loop ? ([["fix loop", fixLoopWords(seen, api)]] as [string, React.ReactNode][]) : []),
              ["on failure", api.on_failure?.length ? `${api.on_failure.map(plainName).join(", ")}, once` : gate ? `reject to ${nameOf(item.chain_definition.nodes, rejectTarget(item.chain_definition.nodes, nodeId) ?? nodeId)}` : "—"],
              ["overrides", overridden ? [mine && `${mine} · changed for this item`, ...drafted].filter(Boolean).join(" · ") : "none"],
            ]} />
            <p className="ph-note">{overridden ? "An override applies to this item only. The chain file is unchanged." : "No item override on this node."}</p>
          </>
        )}
      </div>
      {(bar.secondary || bar.primary) && <ActionBar>{btn(bar.secondary, false)}{btn(bar.primary, true)}</ActionBar>}
      {sheet.is("round-review") && roundReview && <ReadSheet title={roundReview.title} text={roundReview.by} onClose={sheet.close}><Markdown text={roundReview.content} /></ReadSheet>}
      {sheet.is("round-note") && roundNote && <ReadSheet title={roundNote.title} text={roundNote.by} onClose={sheet.close}><Markdown text={roundNote.content} /></ReadSheet>}
      {sheet.is("pause") && <PauseSheet item={item} node={nodeId} sheet={sheet} reload={reload} />}
      {sheet.is("skip") && (
        <ConfirmSheet
          title={`Skip ${name}?`}
          text="The node is marked skipped without running. The chain carries on at the next node."
          busy={busy}
          confirm={{ label: "Skip node", run: async () => { const r = await run(act.skip(item.id, nodeId, undefined, item.review_head), `Skipped ${name}.`); if (r.ok) sheet.close(); } }}
          onClose={sheet.close}
        />
      )}
      {sheet.is("rewind") && (
        <ConfirmSheet
          title={`Retry from ${name}?`}
          text={`The item rewinds to ${name} and runs it again from its first step. Every node after it runs again afterwards.`}
          busy={busy}
          confirm={{ label: "Rewind and retry", danger: true, run: async () => { const r = await run(act.retry(item.id, { path: nodeId }), `Rewound to ${name}.`); if (r.ok) sheet.close(); } }}
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
  const ran = round ? passOf(item, node).filter((s) => s.round === (repair.has(s.hook_point) ? round : round - 1)) : [...passOf(item, node), ...escalationsIn(item, node)];
  for (const s of [...ran].sort((a, b) => a.created_at.localeCompare(b.created_at))) latest.set(s.hook_point, s);
  const sessions = [...latest.values()].map((s) => ({ id: s.id, who: taskName(s.hook_point), running: s.status === "running" || s.status === "pending" }));
  const lines = useLogs(item.id, sessions);
  const shown = (lines ?? []).filter((l) => src === "all" || l.src === src);
  return (
    <>
      <div className="ph-chips ph-chips-inline" role="group" aria-label="Sources">
        {SOURCES.map((s) => <Chip key={s} pressed={src === s} onClick={() => setSrc(s)}>{s}</Chip>)}
        <span className="ph-spacer" />
        <span className="ph-count">{lines ? lineCount(lines.length) : "Reading…"}</span>
      </div>
      <LogLines lines={shown} empty="No lines to show. Clear the filter, or the node has not started." />
    </>
  );
}

/** A slice group's screen, as its desktop view in a list: the shared groundwork the item builds itself (each node opens
 *  its own screen), then each slice (opening its child item), then the join. */
function GroupBlocks({ item, steps, onNode, onItem }: { item: ItemDetail; steps: ReturnType<typeof groupSteps>; onNode: (id: string) => void; onItem: (id: string) => void }) {
  const own = steps.filter((st) => st.id !== "slices" && st.id !== "join");
  const slices = steps.find((st) => st.id === "slices");
  const join = steps.find((st) => st.id === "join")?.tasks[0];
  const row = (t: (typeof own)[number]["tasks"][number], onClick?: () => void) => {
    const inner = (
      <>
        <NodeGlyph kind="exec" size="sm" state={t.state} icon={t.icon} running={t.running} paused={t.paused} />
        <span className="ph-row-text">
          <span className="ph-row-label ph-mono">{t.label ?? t.id}</span>
          <span className="ph-row-hint">{t.meta ?? stateWord(t.state)}</span>
        </span>
      </>
    );
    return onClick
      ? <button key={t.id} type="button" className="ph-row ph-task-row" onClick={onClick}>{inner}<ChevronRight size={16} className="ph-chev" aria-hidden="true" /></button>
      : <div key={t.id} className="ph-row ph-task-row">{inner}</div>;
  };
  const g = item.group;
  return (
    <>
      {own.length > 0 && (
        <Block title={slices && g?.groundwork.length ? `Shared groundwork · ${tasksWords(g.groundwork)}` : "In this item"}>
          <div className="ph-list">{own.map((st) => row(st.tasks[0], () => onNode(st.id)))}</div>
        </Block>
      )}
      {slices && (
        <Block title="Slices">
          <div className="ph-list">{slices.tasks.map((t) => { const kid = childOf(item, Number(t.id)); return row(t, kid ? () => onItem(kid.id) : undefined); })}</div>
        </Block>
      )}
      {join && <Block title="Join"><div className="ph-list">{row(join)}</div></Block>}
    </>
  );
}
