import type { BulkResult } from "../../../frontend/src/types";
import type { WorkItem } from "./api";

/** The verbs that act on a multi-selection; the rest act on the clicked row only. */
export type BulkVerb = "pause" | "resume" | "archive" | "restore";

export interface Skipped { id: string; why: string }

/** Which ids the server would admit `verb` for now, and why the rest are left
 *  out. A question stop needs an answer of its own, so resume leaves it out
 *  even though the door is open. */
export function bulkPlan(verb: BulkVerb, items: WorkItem[]): { ids: string[]; skipped: Skipped[] } {
  const ids: string[] = [];
  const skipped: Skipped[] = [];
  for (const i of items) {
    if (verb === "resume" && i.stop?.kind === "question") skipped.push({ id: i.id, why: "needs an answer" });
    else if (i.actions?.includes(verb)) ids.push(i.id);
    else skipped.push({ id: i.id, why: `can't ${verb} now` });
  }
  return { ids, skipped };
}

const PAST: Record<BulkVerb, string> = { pause: "paused", resume: "resumed", archive: "archived", restore: "restored" };

/** One line for the whole selection: how many it did, then each id it did not
 *  do and why, refusals before skips. */
export function bulkSummary(verb: BulkVerb, results: BulkResult[], skipped: Skipped[]): string {
  const done = results.filter((r) => r.ok).length;
  const not = [
    ...results.filter((r) => !r.ok).map((r) => `${r.id} (${r.error ?? "refused"})`),
    ...skipped.map((s) => `${s.id} (${s.why})`),
  ];
  const past = PAST[verb];
  const head = done || !not.length ? `${past[0].toUpperCase()}${past.slice(1)} ${done}.` : "";
  const tail = not.length ? `Not ${past}: ${not.join(", ")}.` : "";
  return [head, tail].filter(Boolean).join(" ");
}

/** A board row as a command receives it: `kind` is "item" for an item row. */
export type SelectionRow = { kind?: string; item?: WorkItem };

/** The items a pause, resume, archive or restore acts on: the selection when
 *  the clicked row is part of it, else the clicked row alone. VS Code passes
 *  the selection only when the click was inside it; only item rows count. */
export function selectedItems(node: SelectionRow | undefined, nodes: readonly SelectionRow[] | undefined): WorkItem[] {
  const clicked = node?.item;
  if (!clicked || (node.kind !== undefined && node.kind !== "item")) return [];
  const picked = (nodes ?? []).filter((n) => n.kind === "item" && n.item).map((n) => n.item!);
  if (!picked.some((i) => i.id === clicked.id)) return [clicked];
  return picked.filter((i, k) => picked.findIndex((j) => j.id === i.id) === k);
}
