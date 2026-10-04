import type { ReactNode } from "react";
import { elapsed } from "../../../format";
import type { ChainNode } from "../../../types";
import { loopRounds } from "../nodeGraph";
import { otherRounds, scopesView, type Chip, type ScopesView } from "../scopeView";
import type { ItemDetail } from "../useItem";
import { Log } from "./Log";
import type { PaneContent } from "./paneContent";

const fact = (k: string, v: ReactNode) => (v == null || v === "" ? null : <div key={k}><dt>{k}</dt><dd>{v}</dd></div>);

/** A chip's state in the words a pane's subtitle uses: "passed 24s", "failed 1m", "running", "not picked". */
function stateWords(c: Chip): string {
  if (c.state === "skipped") return "not picked";
  const took = c.session?.wall_ms != null ? ` ${elapsed(c.session.wall_ms)}` : "";
  if (c.state === "done") return `passed${took}`;
  if (c.state === "failed") return `failed${took}`;
  return c.meta || (c.state === "waiting" ? "waiting" : "running");
}

/** The status row: the same, with a dot before the time, and why a dropped scope is. */
const statusWords = (c: Chip) => (c.state === "skipped" ? "not picked · no changed path reaches it this round" : stateWords(c).replace(/^(passed|failed) (?=\d)/, "$1 · "));

type Args = { item: ItemDetail; node: ChainNode; step: string; task: string; scope: string; round: number; now: number; crumbs: PaneContent["crumbs"]; tab: string; toTask: () => void };

/** The pane for one scope of an open changed-test-scope task (the chips in its frame): the command it ran, the
 *  repository it ran in, how the round before and after treated it. Its log once there is a session to read. */
export function scopePane({ item, node, step, task, scope, round, now, crumbs, tab, toTask }: Args): PaneContent {
  const path = `${node.id}.${step}.${task}`;
  const view = scopesView(item, path, round, now);
  const hit = view.rows.flatMap((r) => r.chips.map((c) => ({ row: r, chip: c }))).find((x) => x.chip.key === scope);
  const total = loopRounds(item, node)?.total;
  const place = `round ${round}${total ? ` of ${total}` : ""}`;
  const toTaskCrumb = { label: task, onClick: toTask };
  if (!hit) {
    const repo = view.rows.find((r) => (r.id ?? "") === scope.slice(0, scope.indexOf(":")));
    const dropped = repo ? repo.chips.length > 0 : false;
    return { crumbs: [...crumbs, toTaskCrumb, ...(repo ? [{ label: repo.name }] : [])], title: scope.slice(scope.indexOf(":") + 1) || scope, sub: `test scope · ${place} · ${dropped ? "not picked" : "not reached"}`, body: <p className="item-muted">{dropped ? "Not picked: no changed path reaches it this round." : "Its repository was not reached this round."}</p> };
  }
  const { row, chip } = hit;
  const s = chip.session;
  const tabs = s ? [{ value: "overview", label: "Overview" }, { value: "log", label: "Log" }] : undefined;
  const on = tabs?.some((t) => t.value === tab) ? tab : "overview";
  const others = otherRounds(item, path, scope, round);
  return {
    crumbs: [...crumbs, toTaskCrumb, { label: row.name }],
    // The scope is its command: the whole of it here, where the chip trims it.
    title: chip.setup ? chip.name : chip.command,
    sub: `${chip.setup ? "area setup" : "test scope"} · ${place} · ${stateWords(chip)}`,
    tabs,
    body: on === "log" && s
      ? <Log key={s.id} sessionId={s.id} running={s.status === "running"} title={chip.command} crumb={[...crumbs.map((c) => c.label), task, row.name].join(" › ")} />
      : (
        <dl className="item-facts ip-facts">
          {fact("status", statusWords(chip))}
          {fact("command", <span className="is-mono">{chip.command}</span>)}
          {fact("paths", chip.paths && <span className="is-mono" style={{ overflowWrap: "anywhere" }}>{chip.paths}</span>)}
          {fact("repo", <span className="is-mono">{row.name}</span>)}
          {fact("task", <button type="button" className="item-link is-mono" onClick={toTask}>{task}</button>)}
          {fact("execution", <span className="is-mono">{view.execution}</span>)}
          {fact("other rounds", others.length ? others.join(" · ") : null)}
        </dl>
      ),
  };
}

/** The open task's own Overview: how its round went in a line each, then what the chips and rings on its canvas mean. */
export function ScopeOverview({ view }: { view: ScopesView }) {
  const chips = view.rows.flatMap((r) => r.chips).filter((c) => c.state !== "skipped");
  const reached = view.rows.filter((r) => r.state !== "unreached" && r.state !== "waiting").length;
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("this round", `${chips.filter((c) => c.state === "done").length} of ${chips.length} scopes passed`)}
        {fact("repos", `${reached} of ${view.rows.length} reached · run in order, stop at the first failure`)}
        {fact("scopes", view.execution === "parallel" ? "in parallel within a repo" : "one after another within a repo, all run")}
        {fact("config", <span className="is-mono">execution: {view.execution}</span>)}
      </dl>
      <div className="scope-legend">
        <div><span className="scope-new">new</span><span>picked for the first time this round: the changed paths now reach it</span></div>
        <div><span className="scope-key">not picked</span><span>ran in the previous round, but no changed path reaches it this round</span></div>
        <div><span className="scope-key is-ring" aria-hidden="true" /><span>repo not reached: repos run in order and stop at the first one that fails</span></div>
      </div>
    </>
  );
}
