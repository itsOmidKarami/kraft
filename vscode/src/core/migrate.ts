import { ApiError, type ThreadIn } from "./api";

/** Where the 1.x extension kept an item's unsent review comments in `workspaceState`. */
export const DRAFT_KEY = /^kraft\.draft\.(.+)$/;

/** `line` is one-based; `side: "base"` is the old side. */
export interface LegacyComment { file: string; line: number; body: string; side?: "base" }
export interface LegacyDraft { comments: LegacyComment[]; summary?: string }

const legacyBody = (c: LegacyComment) => `${c.file}:${c.line}${c.side === "base" ? " (old)" : ""} — ${c.body}`;

/**
 * One whole-change thread per comment, naming its file and line in the body
 * (D26: the lines may have moved since), and one for a summary.
 */
export function toThreadIns(d: LegacyDraft): ThreadIn[] {
  return [...d.comments.map((c) => ({ body: legacyBody(c) })), ...(d.summary?.trim() ? [{ body: d.summary.trim() }] : [])];
}

export type DropReason = "unreviewable" | "refused";
const WHY: Record<DropReason, string> = { unreviewable: "it has nothing to review any more", refused: "Kraft refused them" };

/** What is said, and offered to copy, when drafts cannot go anywhere. `name` is what the item is called. */
export function dropMessage(name: string, d: LegacyDraft, why: DropReason = "unreviewable"): string {
  const n = d.comments.length || 1; // a summary alone is one comment
  return [
    `Kraft dropped ${n} local review comment${n === 1 ? "" : "s"} on ${name}: ${WHY[why]}. Their text:`,
    ...d.comments.map((c) => `${c.file}:${c.line}${c.side === "base" ? " (base)" : ""}: ${c.body}`),
    ...(d.summary?.trim() ? [`(summary): ${d.summary.trim()}`] : []),
  ].join("\n");
}

export interface MigrateDeps {
  /** False when the item has nothing to review; throws when that cannot be told now. */
  reviewable(id: string): Promise<boolean>;
  /** The first-comment bodies of the item's threads. */
  existing(id: string): Promise<string[]>;
  post(id: string, t: ThreadIn): Promise<void>;
  /** `undefined` clears the item's key. */
  save(id: string, d: LegacyDraft | undefined): Promise<void>;
  /** Asked before each post: a window that turned read-only mid-run posts nothing more. */
  writable?(): boolean;
}

export interface Migrated { posted: number; dropped: LegacyDraft | null; kept: LegacyDraft | null; why?: DropReason }

const isEmpty = (d: LegacyDraft) => !d.comments.length && !d.summary?.trim();
const droppedOf = (d: LegacyDraft) => (isEmpty(d) ? { dropped: null } : { dropped: d, why: "refused" as const });

/** A refusal about this draft itself; a timeout or rate limit is worth another try. */
const refused = (e: unknown) => e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429;

/**
 * Posts an item's local drafts, each at most once: what was posted leaves the
 * store as it goes, so an interrupted run leaves only the rest. A draft the
 * server already holds (an answer lost on the way back) is not posted again.
 */
export async function migrateItem(id: string, d: LegacyDraft, deps: MigrateDeps): Promise<Migrated> {
  if (isEmpty(d)) {
    await deps.save(id, undefined);
    return { posted: 0, dropped: null, kept: null };
  }
  let existing: Set<string>;
  try {
    if (!(await deps.reviewable(id))) {
      await deps.save(id, undefined);
      return { posted: 0, dropped: d, kept: null, why: "unreviewable" };
    }
    existing = new Set(await deps.existing(id));
  } catch {
    return { posted: 0, dropped: null, kept: d };
  }
  const rest: LegacyDraft = { comments: [...d.comments], ...(d.summary?.trim() && { summary: d.summary }) };
  const dropped: LegacyDraft = { comments: [] };
  let posted = 0;
  const units: ({ comment: LegacyComment } | { summary: string })[] = [
    ...d.comments.map((comment) => ({ comment })),
    ...(rest.summary ? [{ summary: rest.summary }] : []),
  ];
  for (const u of units) {
    const [t] = toThreadIns("comment" in u ? { comments: [u.comment] } : { comments: [], summary: u.summary });
    const done = () => ("comment" in u ? rest.comments.shift() : delete rest.summary);
    if (!existing.has(t.body)) {
      if (deps.writable && !deps.writable()) return { posted, ...droppedOf(dropped), kept: rest };
      try {
        await deps.post(id, t);
        posted++;
      } catch (e) {
        if (!refused(e)) return { posted, ...droppedOf(dropped), kept: rest };
        if ("comment" in u) dropped.comments.push(u.comment);
        else dropped.summary = u.summary;
      }
    }
    done();
    await deps.save(id, isEmpty(rest) ? undefined : rest);
  }
  return { posted, ...droppedOf(dropped), kept: null };
}

/** Every item's local drafts, one item at a time; nothing while read-only. */
export async function migrateAll(
  keys: readonly string[],
  get: (key: string) => unknown,
  deps: MigrateDeps,
  readOnly: boolean,
): Promise<(Migrated & { id: string })[]> {
  if (readOnly) return [];
  const out: (Migrated & { id: string })[] = [];
  for (const key of keys) {
    const id = DRAFT_KEY.exec(key)?.[1];
    const d = get(key) as LegacyDraft | undefined;
    if (!id || !d || !Array.isArray(d.comments)) continue;
    out.push({ id, ...(await migrateItem(id, d, deps)) });
  }
  return out;
}
