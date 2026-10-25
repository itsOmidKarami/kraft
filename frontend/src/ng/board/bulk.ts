import { create } from "zustand";
import { useStore } from "../../store";
import { detailOf, jsonBody, request } from "../http";
import { act } from "../item/actions";
import { sendLater, UNDO_WINDOW_MS } from "../later";
import { showToast } from "../ui/Toast";

/** B9: one call, an answer per id (W6 brief D). */
export type BulkAction = "pause" | "cancel" | "archive" | "restore" | "retry" | "resume";
export type BulkItemResult = { id: string; ok: boolean; status?: string | null; error?: string };
export type BulkOutcome = { action: BulkAction; ids: string[]; results: BulkItemResult[] } | { action: BulkAction; ids: string[]; error: string };

export const VERB: Record<BulkAction, string> = { pause: "paused", cancel: "cancelled", archive: "archived", restore: "restored", retry: "retried", resume: "resumed" };
export const items = (n: number) => `${n} item${n === 1 ? "" : "s"}`;

/** Retry and Resume have no bulk route: the item page's own call, one per item, answered in the bulk shape. */
async function eachOne(action: "retry" | "resume", ids: string[]): Promise<BulkOutcome> {
  const results = await Promise.all(ids.map(async (id): Promise<BulkItemResult> => {
    const r = await act[action](id);
    return r.ok ? { id, ok: true } : { id, ok: false, error: r.error };
  }));
  return { action, ids, results };
}

async function post(action: BulkAction, ids: string[], reason?: string, keepalive?: boolean): Promise<BulkOutcome> {
  const r = await request<{ results: BulkItemResult[] }>("/work-items/bulk", { ...jsonBody("POST", { action, ids, ...(reason ? { reason } : {}) }), ...(keepalive ? { keepalive } : {}) });
  return r.status >= 200 && r.status < 300 ? { action, ids, results: r.body.results } : { action, ids, error: detailOf(r.body) };
}

/** One id per call, so a slow run (archiving deletes worktrees) can say how far it got. */
async function oneByOne(action: BulkAction, ids: string[], reason: string | undefined, onEach: (ok: boolean) => void, keepalive?: boolean): Promise<BulkOutcome> {
  const results: BulkItemResult[] = [];
  for (const id of ids) {
    const out = await post(action, [id], reason, keepalive);
    results.push(...("results" in out ? out.results : [{ id, ok: false, error: out.error }]));
    onEach("results" in out && out.results.some((r) => r.id === id && r.ok));
  }
  return { action, ids, results };
}

/** `onEach`, when given, sends the ids one at a time and is told after each whether it went through.
 *  `keepalive` lets the browser finish the request after the page is gone. */
export async function sendBulk(action: BulkAction, ids: string[], reason?: string, onEach?: (ok: boolean) => void, keepalive?: boolean): Promise<BulkOutcome> {
  let out: BulkOutcome;
  if (action === "retry" || action === "resume") out = await eachOne(action, ids);
  else if (onEach) out = await oneByOne(action, ids, reason, onEach, keepalive);
  else out = await post(action, ids, reason, keepalive);
  // The list moves on every action; read it once rather than wait on each item's events.
  useStore.getState().bootstrap().catch(() => {});
  const ok = "results" in out ? out.results.filter((x) => x.ok).map((x) => x.id) : [];
  if (action === "archive" && ok.length) showToast(`${items(ok.length)} archived`, { ms: 6000, action: { label: "Undo", run: () => void sendBulk("restore", ok) } });
  else if (ok.length === ids.length) showToast(`${items(ok.length)} ${VERB[action]}`);
  // Kept only when something needs reading: the board shows it in the bar's place.
  useBulk.getState().set(ok.length === ids.length ? null : out);
  return out;
}

/** The last bulk answer, outside the board so a send that lands after the
 *  person left the board is still there when they come back. */
export const useBulk = create<{ last: BulkOutcome | null; set: (o: BulkOutcome | null) => void }>((set) => ({
  last: null,
  set: (last) => set({ last }),
}));

/** A confirmed bulk Cancel, held for the undo window. Leaving the page inside it sends it at
 *  once. Returns the Undo, true when it caught the send in time. */
export function cancelLater(ids: string[], reason: string): () => boolean {
  const { undo } = sendLater(({ keepalive }) => sendBulk("cancel", ids, reason, undefined, keepalive), UNDO_WINDOW_MS);
  return undo;
}
