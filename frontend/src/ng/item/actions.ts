import { detailOf, jsonBody, request } from "../http";

/** An action's outcome: ok, or the server's own words for why not (rule 0.5). */
export type Done<T = unknown> = { ok: true; body: T } | { ok: false; error: string };

async function post<T = unknown>(path: string, body?: unknown, method = "POST"): Promise<Done<T>> {
  const r = await request<T>(path, jsonBody(method, body ?? {}));
  return r.status >= 200 && r.status < 300 ? { ok: true, body: r.body } : { ok: false, error: detailOf(r.body) };
}

const at = (id: string) => `/work-items/${encodeURIComponent(id)}`;

/** Every write the item page makes. Cancel is `/cancel` (R17): it keeps the branch and worktree; the route that deletes them stays CLI-only. */
export const act = {
  pause: (id: string) => post(`${at(id)}/pause`),
  resume: (id: string, steer?: string | null, steers?: Record<string, string>) => post(`${at(id)}/resume`, { steer: steer || null, ...(steers ? { steers } : {}) }),
  retry: (id: string, body: { path?: string; steer?: string | null; task_config?: Record<string, unknown> } = {}) => post<{ attempt?: number }>(`${at(id)}/retry`, body),
  skip: (id: string, path: string, note?: string) => post(`${at(id)}/skip`, { path, ...(note ? { note } : {}) }),
  cancel: (id: string, reason: string, closeMr: boolean) => post<{ close_mr?: { ok: boolean; error?: string } }>(`${at(id)}/cancel`, { reason, close_mr: closeMr }),
  complete: (id: string, reason: string, closeBeads: boolean) => post(`${at(id)}/complete`, { reason, close_beads: closeBeads }),
  escalate: (id: string, message: string, newThread: boolean) => post(`${at(id)}/escalate`, { message, new_thread: newThread }),
  stopEscalation: (id: string) => post(`${at(id)}/escalate/stop`),
  archive: (id: string) => post(`${at(id)}/archive`),
  restore: (id: string) => post(`${at(id)}/restore`),
  duplicate: (id: string) => post<{ id: string; duplicate_warning?: string }>(`${at(id)}/duplicate`),
  reopenMr: (id: string) => post(`${at(id)}/reopen-mr`),
  openWorktree: (id: string) => post(`${at(id)}/open-worktree`, { editor: null }),
  patch: (id: string, body: Record<string, unknown>) => post(at(id), body, "PATCH"),
  approve: (id: string, gate: string) => post(`${at(id)}/gates/${encodeURIComponent(gate)}/approve`),
  reject: (id: string, gate: string, note: string) => post(`${at(id)}/gates/${encodeURIComponent(gate)}/reject`, { note }),
  /** B5 (R2): the worker capability's routes, unreachable until it ships — the card that calls them renders only with its fields. */
  reassign: (id: string) => post(`${at(id)}/reassign`),
  keepWaiting: (id: string) => post(`${at(id)}/keep-waiting`),
};

/** `GET /work-items/{id}/cancel-preview` (B4). */
export async function cancelPreview(id: string) {
  const r = await request<import("../../types").CancelPreview>(`${at(id)}/cancel-preview`);
  return r.status === 200 ? { ok: true as const, body: r.body } : { ok: false as const, error: detailOf(r.body) };
}
