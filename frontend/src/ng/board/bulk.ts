import { create } from "zustand";
import { useStore } from "../../store";
import { detailOf, jsonBody, request } from "../http";
import { showToast } from "../ui/Toast";

/** B9: one call, an answer per id (W6 brief D). */
export type BulkAction = "pause" | "cancel" | "archive" | "restore";
export type BulkItemResult = { id: string; ok: boolean; status?: string | null; error?: string };
export type BulkOutcome = { action: BulkAction; ids: string[]; results: BulkItemResult[] } | { action: BulkAction; ids: string[]; error: string };

export const VERB: Record<BulkAction, string> = { pause: "paused", cancel: "cancelled", archive: "archived", restore: "restored" };
export const items = (n: number) => `${n} item${n === 1 ? "" : "s"}`;

/** The window between confirming a bulk Cancel and sending it (R3): Undo in it sends nothing. */
export const CANCEL_WINDOW_MS = 5000;

export async function sendBulk(action: BulkAction, ids: string[], reason?: string): Promise<BulkOutcome> {
  const r = await request<{ results: BulkItemResult[] }>("/work-items/bulk", jsonBody("POST", { action, ids, ...(reason ? { reason } : {}) }));
  const out: BulkOutcome = r.status >= 200 && r.status < 300 ? { action, ids, results: r.body.results } : { action, ids, error: detailOf(r.body) };
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

/** A confirmed bulk Cancel, held for the window. Module state, not the
 *  board's: moving to another /ng page inside the window still sends it; a
 *  full reload drops it, and nothing is sent. Returns the Undo, true when it
 *  caught the send in time. */
export function cancelLater(ids: string[], reason: string): () => boolean {
  let sent = false;
  const timer = setTimeout(() => {
    sent = true;
    void sendBulk("cancel", ids, reason);
  }, CANCEL_WINDOW_MS);
  return () => {
    if (sent) return false;
    clearTimeout(timer);
    return true;
  };
}
