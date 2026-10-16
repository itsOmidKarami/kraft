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
  /** Stop the item coming after what it still waits on; Kraft queues a blocked one on its next pass. */
  unblock: (id: string) => post(`${at(id)}/unblock`),
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
  /** A budget stop's way on: raise the cap and retry in one call. */
  raiseBudget: (id: string, budgetUsd: number | null) => post(`${at(id)}/budget/raise`, { budget_usd: budgetUsd }),
  patch: (id: string, body: Record<string, unknown>) => post(at(id), body, "PATCH"),
  /** Apply the item's draft (Review & apply's own call), for a surface with no draft dialog. */
  applyDraft: (id: string) => post(`${at(id)}/draft/apply`),
  approve: (id: string, gate: string) => post(`${at(id)}/gates/${encodeURIComponent(gate)}/approve`),
  reject: (id: string, gate: string, note: string) => post(`${at(id)}/gates/${encodeURIComponent(gate)}/reject`, { note }),
};

/** `GET /work-items/{id}/cancel-preview` (B4). */
export async function cancelPreview(id: string) {
  const r = await request<import("../../types").CancelPreview>(`${at(id)}/cancel-preview`);
  return r.status === 200 ? { ok: true as const, body: r.body } : { ok: false as const, error: detailOf(r.body) };
}

/** Where a Start pressed outside the item page goes when the item's draft
 *  holds changes: the item page, which asks there what its header's Start
 *  asks (Apply and start, or Start without them). */
export const askStartUrl = (id: string) => `${at(id)}?start=1`;

/** Whether a Start pressed outside the item page must ask first (the board's
 *  peek, the phone): Start never applies a draft, so with an op in it the run
 *  has not passed, it asks. A draft it cannot read asks too, rather than start
 *  past one it could not see. */
export async function draftWaits(id: string): Promise<boolean> {
  return (await draftToStart(id)).waits;
}

/** `draftWaits`, with the ops it would ask about (the ones the run has not
 *  passed), for a surface that lists them before it asks (the phone's sheet, R10b-12). */
export async function draftToStart(id: string): Promise<{ waits: boolean; ops: { passed?: boolean }[] }> {
  const d = await request<{ ops?: { passed?: boolean }[] }>(`${at(id)}/draft`);
  if (d.status === 404) return { waits: false, ops: [] };
  const read = d.status === 200 && Array.isArray(d.body?.ops);
  const ops = read ? d.body.ops!.filter((o) => !o.passed) : [];
  return { waits: !read || ops.length > 0, ops };
}
