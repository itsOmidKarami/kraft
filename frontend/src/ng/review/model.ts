/** What the review page's toolbar and tree show, from the API's data (W8 D). Pure. */
import type { CompareFile, CompareTarget, ReviewThread, WorkItem } from "../../types";

export interface TargetOption {
  /** null: an informational line, never picked. */
  value: CompareTarget | null;
  label: string;
  sub: string;
  disabled?: boolean;
}

type PickerItem = Pick<WorkItem, "attempts" | "last_review_sha">;

/** The attempts' empty case (R43): `attempt:N` answers only while a gate is pending. */
export const NO_ATTEMPTS = "Attempts are listed while a gate waits for you";

export function targetLabel(t: CompareTarget, item: PickerItem): string {
  if (t === "last_review") return "your last review";
  if (t === "latest") return item.attempts?.length ? `latest (attempt ${item.attempts.length})` : "latest";
  return t.replace(":", " ");
}

/** Compare from: the base, every attempt but the latest, the last review (prototype `subFor`). */
export function fromOptions(item: PickerItem): TargetOption[] {
  const att = item.attempts?.length ?? 0;
  const out: TargetOption[] = [{ value: "base", label: "base", sub: "the item's starting point" }];
  if (!att) out.push({ value: null, label: "attempts", sub: NO_ATTEMPTS, disabled: true });
  for (let k = 1; k < att; k++)
    out.push({ value: `attempt:${k}`, label: `attempt ${k}`, sub: k === 1 ? (att > 2 ? "first pass, superseded" : "first pass") : `superseded by attempt ${k + 1}` });
  out.push(
    item.last_review_sha
      ? { value: "last_review", label: "your last review", sub: "changes since you started reviewing" }
      : { value: "last_review", label: "your last review", sub: "No review submitted yet", disabled: true },
  );
  return out;
}

/** Compare to: the latest, then each earlier attempt, newest first. */
export function toOptions(item: PickerItem): TargetOption[] {
  const att = item.attempts?.length ?? 0;
  const out: TargetOption[] = [{ value: "latest", label: targetLabel("latest", item), sub: "the current state of the branch" }];
  if (!att) out.push({ value: null, label: "attempts", sub: NO_ATTEMPTS, disabled: true });
  for (let k = att - 1; k >= 1; k--) out.push({ value: `attempt:${k}`, label: `attempt ${k}`, sub: `superseded by attempt ${k + 1}` });
  return out;
}

export interface NodeRow {
  id: string;
  files: number;
}

/** Every node that touched a file in this comparison, in chain order, with its file count. */
export function nodeRows(files: CompareFile[], chainOrder: string[]): NodeRow[] {
  const count = new Map<string, number>();
  for (const f of files) for (const n of new Set(f.touched_by)) count.set(n, (count.get(n) ?? 0) + 1);
  const rank = (n: string) => {
    const i = chainOrder.indexOf(n);
    return i < 0 ? chainOrder.length : i;
  };
  return [...count].map(([id, files]) => ({ id, files })).sort((a, b) => rank(a.id) - rank(b.id));
}

/** The files the nodes filter keeps; null keeps all. */
export const byNodes = (files: CompareFile[], nodes: string[] | null) =>
  nodes === null ? files : files.filter((f) => f.touched_by.some((n) => nodes.includes(n)));

export interface Folder {
  /** The folder's path with a trailing slash; `""` for the top level. */
  dir: string;
  /** What its row reads: its own name, or a run of folders that each hold only the next ("src/kraft/"). */
  name: string;
  folders: Folder[];
  files: CompareFile[];
}

/** The files as a folder tree: at each level folders first, then files, each in path order. */
export function tree(files: CompareFile[], filter = ""): Folder {
  const q = filter.trim().toLowerCase();
  const root: Folder = { dir: "", name: "", folders: [], files: [] };
  for (const f of files) {
    if (q && !f.path.toLowerCase().includes(q)) continue;
    let at = root;
    for (const part of f.path.split("/").slice(0, -1)) {
      const dir = `${at.dir}${part}/`;
      let next = at.folders.find((d) => d.dir === dir);
      if (!next) at.folders.push((next = { dir, name: `${part}/`, folders: [], files: [] }));
      at = next;
    }
    at.files.push(f);
  }
  const settle = (d: Folder): Folder => {
    while (d.dir && !d.files.length && d.folders.length === 1) d = { ...d.folders[0], name: d.name + d.folders[0].name };
    return { ...d, folders: d.folders.map(settle).sort((a, b) => a.dir.localeCompare(b.dir)), files: d.files.sort((a, b) => a.path.localeCompare(b.path)) };
  };
  return settle(root);
}

/** A tree's files top to bottom, as the tree lists them. */
export const inTreeOrder = (d: Folder): CompareFile[] => [...d.folders.flatMap(inTreeOrder), ...d.files];

/** A thread not yet resolved: what the tree counts per file. */
export const unresolved = (t: ReviewThread) => t.state !== "resolved";

/** "N threads: 2 pending, 1 open, 1 claimed, 1 resolved", in the API's states (prototype `sumParts`). */
export function threadSummary(threads: ReviewThread[]): string {
  if (!threads.length) return "No threads";
  const n = (p: (t: ReviewThread) => boolean) => threads.filter(p).length;
  const parts: [number, string][] = [
    [n((t) => t.draft), "pending"],
    [n((t) => !t.draft && t.state === "open"), "open"],
    [n((t) => t.state === "claimed"), "claimed"],
    [n((t) => t.state === "resolved"), "resolved"],
  ];
  const tail = parts.filter(([k]) => k).map(([k, w]) => `${k} ${w}`).join(", ");
  return `${threads.length} thread${threads.length === 1 ? "" : "s"}: ${tail}`;
}
