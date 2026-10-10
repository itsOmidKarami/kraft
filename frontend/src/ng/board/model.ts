import { repoName } from "../../format";
import type { DisplayStatus, WorkItem } from "../../types";

/** Every list decision of the board, once (W6 brief A.4). Reads the server's
 *  `display_status` and the raw row; nothing is derived on the client. */

export type GroupKey = "needs" | "running" | "not_started" | "done";
export type GroupBy = "status" | "repo" | "chain";
export type SortBy = "attention" | "updated" | "created" | "title";

export const STATUS_GROUPS: { key: GroupKey; label: string; empty: string }[] = [
  { key: "needs", label: "Needs you", empty: "nothing is waiting on you" },
  { key: "running", label: "Running", empty: "nothing here" },
  { key: "not_started", label: "Not started", empty: "nothing here" },
  { key: "done", label: "Done", empty: "nothing here" },
];

/** The board group of an item. A paused item with no current node was
 *  created and never resumed (the server's row says so, not a guess); one
 *  paused mid-chain waits on a person. A blocked item with no current node
 *  has not started either; one blocked mid-chain (resumed behind an
 *  unfinished item) stays with the running. `archived` is never on the list. */
const GROUP: Record<DisplayStatus, GroupKey | null> = {
  needs_you: "needs", failed: "needs", paused: null, blocked: null /* by current node, in groupOf */,
  done: "done", cancelled: "done", archived: "done",
  running: "running", waiting: "running", escalated: "running", queued: "running",
};

export function groupOf(i: Pick<WorkItem, "display_status" | "current_node_id">): GroupKey {
  const g = i.display_status ? GROUP[i.display_status] : undefined;
  if (g === undefined) return "running"; // none, or a display status from a newer server
  if (g !== null) return g;
  if (!i.current_node_id) return "not_started";
  return i.display_status === "blocked" ? "running" : "needs";
}

export interface Filter {
  q: string;
  repo: string;
  chain: string;
}

export const chainOf = (i: Pick<WorkItem, "chain_template"> & { chain_definition?: { template_id?: string } | null }) => (i.chain_template || i.chain_definition?.template_id) ?? "default";

export function matches(i: WorkItem, f: Filter): boolean {
  if (f.repo && i.repo !== f.repo) return false;
  if (f.chain && chainOf(i) !== f.chain) return false;
  const q = f.q.trim().toLowerCase();
  if (!q) return true;
  return [i.title, i.id, i.bead_id ?? "", i.repo].some((s) => s.toLowerCase().includes(q));
}

const desc = (a: string, b: string) => (a < b ? 1 : a > b ? -1 : 0);

export function sorter(by: SortBy): (a: WorkItem, b: WorkItem) => number {
  switch (by) {
    case "updated":
      return (a, b) => desc(a.updated_at, b.updated_at);
    case "created":
      return (a, b) => desc(a.created_at, b.created_at);
    case "title":
      return (a, b) => a.title.localeCompare(b.title);
    default:
      return (a, b) => Number(groupOf(b) === "needs") - Number(groupOf(a) === "needs") || desc(a.updated_at, b.updated_at);
  }
}

export interface Group {
  key: string;
  label: string;
  rows: WorkItem[];
  /** All rows the group holds; `rows` is cut to the Done cap. */
  total: number;
  empty: string;
  /** Done under Group by Status: the auto-archive note, Select all, the cap. */
  done: boolean;
}

export interface BoardView {
  filter: Filter;
  group: GroupBy;
  sort: SortBy;
  /** Done's cap (`theme.board.show_done`); null shows all. */
  doneCap: number | null;
}

/** The groups the board draws, in order. Needs you always comes first. */
export function groupsOf(items: WorkItem[], v: BoardView): Group[] {
  const filtered = !!(v.filter.q.trim() || v.filter.repo || v.filter.chain);
  const shown = items.filter((i) => i.display_status !== "archived" && matches(i, v.filter)).sort(sorter(v.sort));
  const emptyLine = (e: string) => (filtered ? "nothing here for this filter" : e);
  if (v.group === "status")
    return STATUS_GROUPS.map(({ key, label, empty }) => {
      const all = shown.filter((i) => groupOf(i) === key);
      const done = key === "done";
      const rows = done && v.doneCap != null ? newest(all, v.doneCap) : all;
      return { key, label, rows, total: all.length, empty: emptyLine(empty), done };
    });
  const needs = shown.filter((i) => groupOf(i) === "needs");
  const rest = shown.filter((i) => groupOf(i) !== "needs");
  const keyOf = (i: WorkItem) => (v.group === "repo" ? i.repo : chainOf(i));
  const keys = [...new Set(rest.map(keyOf))].sort((a, b) => a.localeCompare(b));
  return [
    ...(needs.length ? [{ key: "needs", label: "Needs you", rows: needs, total: needs.length, empty: "", done: false }] : []),
    ...keys.map((k) => {
      const rows = rest.filter((i) => keyOf(i) === k);
      return { key: `${v.group}:${k}`, label: v.group === "repo" ? repoName(k) : k, rows, total: rows.length, empty: "", done: false };
    }),
  ];
}

/** Done's newest `n`, kept in the sort the board shows. */
function newest(rows: WorkItem[], n: number): WorkItem[] {
  if (rows.length <= n) return rows;
  const keep = new Set([...rows].sort((a, b) => desc(a.updated_at, b.updated_at)).slice(0, n).map((i) => i.id));
  return rows.filter((i) => keep.has(i.id));
}
