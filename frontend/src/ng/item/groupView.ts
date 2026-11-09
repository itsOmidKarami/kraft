import type { SliceChild } from "../../types";
import type { DisplayStatus, GroupPhase, WorkItemStatus } from "../../types/vocab.generated";
import { glyphOf, nodeWords, slicesDone } from "../board/rowText";
import type { ChainNode as Drawn } from "../graph/layout";
import type { NodeStep } from "../graph/nodeLayout";
import type { GlyphState, GraphItem } from "../graph/types";
import type { ItemDetail } from "./useItem";

/** A slice group's phase as the item page says it. */
export const PHASE_WORDS: Record<GroupPhase, string> = { deciding: "deciding", groundwork: "shared groundwork", slices: "slices", joining: "joining", joined: "joined" };

/** Plan task numbers as words: `tasks 6–9` for a run, `tasks 2, 4` otherwise, `task 3` for one. */
export function tasksWords(ns: number[]): string {
  const run = ns.length > 2 && ns.every((n, i) => i === 0 || n === ns[i - 1] + 1);
  return `${ns.length === 1 ? "task" : "tasks"} ${run ? `${ns[0]}–${ns[ns.length - 1]}` : ns.join(", ")}`;
}

/** The child item running slice `key`, of the parent's current generation. */
export const childOf = (item: ItemDetail, key: number): SliceChild | undefined => item.children?.find((c) => c.slice_key === key);

/** A child's state in words, as the board's row says it: the node it runs while it runs. */
export const childWords = (c: SliceChild) => (c.display_status === "running" && c.current_node_id ? nodeWords(c.current_node_id) : c.display_status.replace(/_/g, " "));

/** The group's slices: the plan's, else (a plan that no longer splits as it did) its children's. */
export const slicesOf = (item: ItemDetail): { key: number; title: string; tasks: number[] }[] =>
  item.group?.slices.length ? item.group.slices : (item.children ?? []).map((c) => ({ key: c.slice_key, title: c.title, tasks: [] }));

/** Which slice the join replays now, as `slice 2 of 3`. */
const joinWords = (item: ItemDetail) => `slice ${Math.min((item.group?.joined ?? 0) + 1, slicesOf(item).length)} of ${slicesOf(item).length}`;

/** The group's view: the shared groundwork the item builds itself (its body nodes, in the states the chain draws them,
 *  or the whole body when the plan was not split), then one box per slice in its child's state, then the join. Step and
 *  box ids: a body node's own id (`implementation`), `slices` with each slice's key, `join`. */
export function groupSteps(item: ItemDetail, drawn: Drawn | undefined): NodeStep[] {
  const g = item.group;
  const slices = slicesOf(item);
  // While the slices run or join the item runs none of its body, whatever node it last stood on (the groundwork's last);
  // once joined, a rework runs its first node again and the rest stay done.
  const away = g?.phase === "slices" || g?.phase === "joining";
  // With no groundwork the item runs none of its body: the slices do.
  const own = !slices.length || !!g?.groundwork.length;
  const steps: NodeStep[] = (own ? (drawn?.body ?? []) : []).map((b, i) => {
    const id = b.id.slice(b.id.indexOf(".") + 1);
    const state: GlyphState = away ? "done" : b.needs ? "amber" : b.state === "current" ? "current" : g?.phase === "joined" ? "done" : b.state === "plain" || !b.state ? "todo" : b.state;
    // "groundwork" over its column: the column is too narrow for the phase's full words, which the pane gives.
    return { id, label: i === 0 && slices.length ? "groundwork" : "", tasks: [{ id, noun: "node", state, running: state === "current" && b.running, paused: b.paused }] };
  });
  if (!slices.length) return steps;
  steps.push({
    id: "slices",
    label: "slices",
    tasks: slices.map((s): GraphItem => {
      const kid = childOf(item, s.key);
      // A child's statuses are the server's vocab words, as any item's: the board draws its rows from them the same way.
      const look = kid && glyphOf({ ...kid, display_status: kid.display_status as DisplayStatus, status: kid.status as WorkItemStatus });
      return { id: String(s.key), label: `slice ${s.key}`, noun: "slice", icon: "git-branch", state: look ? look.state : "todo", running: look?.running, meta: kid ? childWords(kid) : tasksWords(s.tasks), metaTone: look?.state === "amber" ? "amber" : undefined };
    }),
  });
  const joining = g?.phase === "joining";
  const join: GraphItem = { id: "join", noun: "join", icon: "git-merge", state: joining ? (item.display_status === "needs_you" ? "amber" : "current") : g?.phase === "joined" ? "done" : "todo", running: joining && item.display_status === "running", meta: joining ? joinWords(item) : undefined };
  steps.push({ id: "join", label: "", tasks: [join] });
  return steps;
}

/** A child that stops its parent for a person: one that needs you, failed or escalated (the parent then reads needs you). */
const STOPS_FOR_YOU = new Set(["needs_you", "failed", "escalated"]);

/** The words under a group's box on the chain: the phase while the item builds the groundwork or joins; while the slices
 *  run, that it waits for them and how far they are, and on its own line a slice that needs you. */
export function groupWords(item: ItemDetail, d: Drawn): Pick<Drawn, "sub" | "subTone" | "meta" | "metaTone" | "wait"> {
  const g = item.group;
  // A stop of the item's own (it needs you, it is paused) says so as for any node.
  if (!g?.phase || d.state !== "current" || d.paused || (d.needs && g.phase !== "slices")) return {};
  if (g.phase === "groundwork") return { sub: [PHASE_WORDS.groundwork, d.running ? d.sub : ""].filter(Boolean).join(" · "), subTone: d.subTone };
  if (g.phase === "joining") return { sub: `joining · ${joinWords(item)}`, subTone: d.subTone };
  if (g.phase !== "slices") return {};
  const needs = (item.children ?? []).filter((c) => STOPS_FOR_YOU.has(c.display_status));
  const done = slicesDone(item.slices) || undefined;
  const wait = "waiting for slices";
  if (!needs.length) return { wait, sub: done, subTone: "muted" };
  const one = needs.length === 1 && `slice ${needs[0].slice_key} ${needs[0].display_status === "needs_you" ? "needs you" : needs[0].display_status}`;
  return { wait, meta: done, sub: one || `${needs.length} slices need you`, subTone: "amber" };
}
