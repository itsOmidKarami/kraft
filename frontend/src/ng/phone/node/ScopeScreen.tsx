import { loopRounds, passWords, roundShown } from "../../item/nodeGraph";
import { otherRounds, scopesView, stateWords, statusWords } from "../../item/scopeView";
import { selPath, type Place } from "../../item/url";
import { ScreenHeader } from "../nav/ScreenHeader";
import { useBack } from "../nav/trail";
import { Facts, TabStrip } from "../ui/Rows";
import type { PlaceProps } from "./NodeScreen";
import { TaskLog } from "./TaskScreen";
import "./node.css";

const TABS = [{ id: "overview" as const, label: "Overview" }, { id: "log" as const, label: "Log" }];

/** One scope of a changed-test-scope task, as the desktop's scope pane has it: the command it ran, the repository
 *  it ran in, how the other rounds treated it, and its log once there is a run to read. */
export function ScopeScreen({ item, place, node: nodeId, now, setPlace }: PlaceProps & { place: Place & { scope: string; sel: { kind: "task"; node: string; step: string; task: string } } }) {
  const path = selPath(place.sel)!;
  const apiNode = item.chain_definition.nodes.find((n) => n.id === nodeId)!;
  const round = roundShown(item, apiNode, place.round) ?? 1;
  const rounds = loopRounds(item, apiNode);
  const total = rounds?.total;
  const pass = passWords(item, nodeId, place.pass);
  const view = scopesView(item, path, round, now);
  const hit = view.rows.flatMap((row) => row.chips.map((chip) => ({ row, chip }))).find((x) => x.chip.key === place.scope);
  const at = place.scope.indexOf(":");
  // A scope the round has no chip for: its repository ran without it, or was not reached at all.
  const repo = hit?.row ?? view.rows.find((r) => (r.id ?? "") === place.scope.slice(0, at));
  const dropped = !hit && !!repo && repo.chips.length > 0;
  // One repository: nothing to tell apart, so the screen does not name it, as the desktop's frame does not.
  const several = view.rows.length > 1;
  const s = hit?.chip.session;
  const tab = s && place.tab === "log" ? "log" : "overview";
  // The task fact is Back: a second entry for the task would bring the phone's own back gesture to it twice.
  const toTask = useBack().go;
  const mono = (v: string) => <span className="ph-mono">{v}</span>;
  const others = hit ? otherRounds(item, path, place.scope, round, rounds?.first) : [];
  return (
    <>
      <ScreenHeader />
      <div className="ph-content">
        <div className="ph-node-head">
          <p className="ph-crumb">{item.bead_id ?? item.id.slice(0, 8)} › {nodeId} › {place.sel.step} › {place.sel.task}{several && repo ? ` › ${repo.name}` : ""}</p>
          <h1 className="ph-node-title">{hit ? (hit.chip.setup ? hit.chip.name : hit.chip.command) : place.scope.slice(at + 1) || place.scope}</h1>
          <p className={`ph-node-sub${hit?.chip.state === "failed" ? " ph-tone-bad" : hit?.chip.state === "running" ? " ph-tone-info" : ""}`}>
            {hit?.chip.setup ? "area setup" : "test scope"} · {pass && `${pass} · `}round {round}{total ? ` of ${total}` : ""} · {hit ? stateWords(hit.chip) : dropped ? "not picked" : "not reached"}
          </p>
        </div>
        {s && <TabStrip label="Scope" tabs={TABS} value={tab} onChange={(t) => setPlace({ tab: t === "overview" ? undefined : t })} />}
        {!hit ? (
          <p className="ph-note">{dropped ? "Not picked: no changed path reaches it this round." : "Its repository was not reached this round."}</p>
        ) : tab === "log" ? (
          <TaskLog session={s} />
        ) : (
          <Facts rows={[
            ["status", statusWords(hit.chip)],
            ["command", mono(hit.chip.command)],
            ...(hit.chip.paths ? ([["paths", mono(hit.chip.paths)]] as [string, React.ReactNode][]) : []),
            ...(several ? ([["repo", mono(hit.row.name)]] as [string, React.ReactNode][]) : []),
            ["task", <button key="t" type="button" className="ph-linkbtn ph-mono" onClick={toTask}>{place.sel.task}</button>],
            ["execution", mono(view.execution)],
            ...(others.length ? ([["other rounds", others.join(" · ")]] as [string, React.ReactNode][]) : []),
          ]} />
        )}
      </div>
    </>
  );
}
