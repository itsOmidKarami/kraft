import type { ReactNode } from "react";
import type { ChainNode } from "../../../types";
import { loopRounds } from "../nodeGraph";
import { chipOfRun, otherRounds, roundWords, scopeAt, scopeRuns, scopesView, stateWords, statusWords, type ScopesView } from "../scopeView";
import type { ItemDetail } from "../useItem";
import { Log } from "./Log";
import type { PaneContent } from "./paneContent";
import { AttemptMenu } from "./TaskPane";

const fact = (k: string, v: ReactNode) => (v == null || v === "" ? null : <div key={k}><dt>{k}</dt><dd>{v}</dd></div>);

type Args = { item: ItemDetail; node: ChainNode; step: string; task: string; scope: string; round: number; now: number; crumbs: PaneContent["crumbs"]; tab: string; toTask: () => void; attempt?: number; setAttempt: (attempt: number | undefined) => void };

/** The pane for one scope of an open changed-test-scope task (the chips in its frame): the command it ran, the
 *  repository it ran in when the item has several, how the round before and after treated it. Its log once there is
 *  a session to read, and its runs to pick between when the round ran it more than once. */
export function scopePane({ item, node, step, task, scope, round, now, crumbs, tab, toTask, attempt, setAttempt }: Args): PaneContent {
  const path = `${node.id}.${step}.${task}`;
  const view = scopesView(item, path, round, now);
  const hit = scopeAt(item, path, scope, view);
  const rounds = loopRounds(item, node);
  const place = `round ${round}${rounds?.total ? ` of ${rounds.total}` : ""}`;
  // One repository: nothing to tell apart, so it is not named, as the frame does not name it.
  const repo = view.rows.length > 1 && hit.row ? [hit.row.name] : [];
  const above = [...crumbs, { label: task, onClick: toTask }, ...repo.map((label) => ({ label }))];
  if (!hit.chip) return { crumbs: above, title: hit.command, sub: `test scope · ${place} · ${hit.miss}`, body: <p className="item-muted">{hit.why}</p> };
  // Run again inside the round, the scope has a run to pick, as a task has its attempts; the newest is the chip's.
  const runs = scopeRuns(item, path, scope, round);
  const s = runs.find((x) => x.attempt === attempt) ?? runs.find((x) => x.id === hit.chip!.session?.id) ?? hit.chip.session;
  const chip = s && s.id !== hit.chip.session?.id ? chipOfRun(hit.chip, s, now) : hit.chip;
  const lead = `${chip.setup ? "area setup" : "test scope"} · ${place}`;
  const tabs = s ? [{ value: "overview", label: "Overview" }, { value: "log", label: "Log" }] : undefined;
  const on = tabs?.some((t) => t.value === tab) ? tab : "overview";
  const others = otherRounds(item, path, scope, round, rounds?.first);
  return {
    crumbs: above,
    // The scope is its command: the whole of it here, where the chip trims it.
    title: chip.setup ? chip.name : chip.command,
    sub: runs.length > 1 && s ? <>{lead} · <AttemptMenu sessions={runs} at={s} onAt={(n) => setAttempt(n === runs.at(-1)!.attempt ? undefined : n)} now={now} inRound /> · {stateWords(chip)}</> : `${lead} · ${stateWords(chip)}`,
    tabs,
    body: on === "log" && s
      ? <Log key={s.id} sessionId={s.id} running={s.status === "running"} title={chip.command} crumb={[...crumbs.map((c) => c.label), task, ...repo].join(" › ")} />
      : (
        <dl className="item-facts ip-facts">
          {fact("status", statusWords(chip))}
          {fact("command", <span className="is-mono">{chip.command}</span>)}
          {fact("paths", chip.paths && <span className="is-mono" style={{ overflowWrap: "anywhere" }}>{chip.paths}</span>)}
          {fact("repo", repo.length ? <span className="is-mono">{repo[0]}</span> : null)}
          {fact("task", <button type="button" className="item-link is-mono" onClick={toTask}>{task}</button>)}
          {fact("execution", <span className="is-mono">{view.execution}</span>)}
          {fact("other rounds", others.length ? others.join(" · ") : null)}
        </dl>
      ),
  };
}

/** The open task's own Overview: how its round went in a line each, then what the chips and rings on its canvas mean. */
export function ScopeOverview({ view }: { view: ScopesView }) {
  const words = roundWords(view);
  // One repository is not counted, nor is "not reached" a thing that can happen to it.
  const several = view.rows.length > 1;
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("this round", words.scopes)}
        {several && fact("repos", words.repos)}
        {fact("scopes", view.execution === "parallel" ? "in parallel within a repo" : "one after another within a repo, all run")}
        {fact("config", <span className="is-mono">execution: {view.execution}</span>)}
      </dl>
      <div className="scope-legend">
        <div><span className="scope-new">new</span><span>picked for the first time this round: the changed paths now reach it</span></div>
        <div><span className="scope-key">not picked</span><span>ran in the previous round, but no changed path reaches it this round</span></div>
        {several && <div><span className="scope-key is-ring" aria-hidden="true" /><span>repo not reached: repos run in order and stop at the first one that fails</span></div>}
      </div>
    </>
  );
}
