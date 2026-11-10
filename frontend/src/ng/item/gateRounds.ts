import { localTime } from "../../format";
import type { GateRound } from "../../types";
import { cursorOf } from "./cursor";
import type { Rounds } from "./nodeGraph";
import type { ItemDetail } from "./useItem";

/** A gate's rounds, oldest first: the server's reading of every time it was asked (`store.gate_rounds`), not the page's
 *  window of the newest events. */
export const roundsOf = (item: ItemDetail, gate: string): GateRound[] => item.gate_rounds?.[gate] ?? [];

/** The round a gate's view shows: the one picked when the gate has it, else the newest. */
export function roundShown(item: ItemDetail, gate: string, picked?: number): GateRound | undefined {
  const all = roundsOf(item, gate);
  return all.find((r) => r.n === picked) ?? all[all.length - 1];
}

/** Whether an earlier round than the newest is the one shown: what describes now (the decision, the document, the
 *  files) doesn't belong to it. */
export function isPastRound(item: ItemDetail, gate: string, picked?: number): boolean {
  const all = roundsOf(item, gate);
  return picked !== undefined && picked < all.length && all.some((r) => r.n === picked);
}

/** Who decided, in the one set of words every view uses. A round that was skipped, closed or is waiting names nobody. */
export function byWord(by: GateRound["by"]): string | null {
  if (by === "human") return "you";
  if (by === "assistant") return "assistant";
  if (by === "agent" || by === "kraft") return "auto";
  return null;
}

export const outcomeWord = (r: GateRound): string =>
  ({ approved: "approved", rejected: "changes requested", fixed: "fixed", skipped: "skipped", closed: "closed", pending: "waiting" })[r.outcome];

/** What the newest round came to, for a gate that is not pending and not passed: "changes requested · you". Undefined
 *  while it waits, once it is passed (its state says that), and for a gate with no round. */
export function newestOutcome(item: ItemDetail, gate: string): string | undefined {
  const newest = roundsOf(item, gate).at(-1);
  if (!newest || item.pending_gate === gate || gateState(item, gate).state === "done") return undefined;
  const who = byWord(newest.by);
  return `${outcomeWord(newest)}${who ? ` · ${who}` : ""}`;
}

/** The picker's rows, one per round; undefined below two rounds, where there is nothing to step through. */
export function gateRoundList(item: ItemDetail, gate: string, picked?: number): Rounds | undefined {
  const all = roundsOf(item, gate);
  if (all.length < 2) return undefined;
  const tones = { approved: "ok", fixed: "ok", skipped: "ok", rejected: "bad", closed: "bad", pending: "run" } as const;
  return {
    rows: all.map((r) => {
      const who = byWord(r.by);
      return { n: r.n, tone: tones[r.outcome], outcome: `${outcomeWord(r)}${who ? ` · ${who}` : ""}` };
    }),
    selected: roundShown(item, gate, picked)!.n,
    latest: all.length,
  };
}

/** Where a gate stands, from its newest round: waiting (it is the item's pending gate), passed or skipped (approved,
 *  fixed or skipped, and not reopened since), else not reached. A gate with no round that sits before the run's cursor
 *  was passed without being asked: a gate inside a group the run is behind. */
export function gateState(item: ItemDetail, gate: string): { state: "current" | "done" | "todo"; words: string } {
  if (item.pending_gate === gate) return { state: "current", words: "waiting for you" };
  const all = roundsOf(item, gate);
  const newest = all[all.length - 1];
  if (newest && !newest.reopened) {
    if (newest.outcome === "skipped") return { state: "done", words: "skipped" };
    if (newest.outcome === "approved" || newest.outcome === "fixed") {
      const who = byWord(newest.by);
      return { state: "done", words: `passed${who ? ` · ${who}` : ""}` };
    }
  }
  if (!newest) {
    const nodes = item.chain_definition.nodes;
    if (nodes.findIndex((n) => n.id === gate) < (cursorOf(nodes, item.current_node_id)?.index ?? -1)) return { state: "done", words: "passed" };
  }
  return { state: "todo", words: "not reached" };
}

/** A note's first paragraph: what a round's preview shows. A paragraph ends at a blank line. */
export const firstParagraph = (note: string): string => note.trim().split(/\n\s*\n/)[0].trim();

/** Whether the note has a paragraph after the preview's. A note that is one long paragraph is told by the layout, which
 *  knows what three lines hold. */
export const hasMoreParagraphs = (note: string): boolean => note.trim().split(/\n\s*\n/).length > 1;

/** A round's whole note as a document, for the drawer and the phone's sheet: "<gate> · round N note", written by
 *  "<outcome> · <who> · <time>". */
export function noteDoc(gate: string, r: GateRound): { title: string; by: string; content: string } {
  return {
    title: `${gate} · round ${r.n} note`,
    by: [outcomeWord(r), byWord(r.by), r.ended_at && localTime(r.ended_at)].filter(Boolean).join(" · "),
    content: r.note ?? "",
  };
}
