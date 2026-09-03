import type { Health, KraftEvent, WorkItem, WorkerSession } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { accept: "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      /* non-JSON body */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const listWorkItems = () =>
  req<{ items: WorkItem[]; cursor: number }>("/work-items");

export const getWorkItem = (id: string) =>
  req<WorkItem & { worker_sessions: WorkerSession[] }>(`/work-items/${id}`);

export const getEvents = (id: string, afterSeq = 0) =>
  req<KraftEvent[]>(`/work-items/${id}/events?after_seq=${afterSeq}`);

export const createWorkItem = (body: {
  repo: string;
  title: string;
  chain_template?: string;
}) => req<{ id: string }>("/work-items", json("POST", body));

export const approveGate = (id: string, gate: string) =>
  req<void>(`/work-items/${id}/gates/${gate}/approve`, { method: "POST" });

export const rejectGate = (id: string, gate: string, note: string) =>
  req<void>(`/work-items/${id}/gates/${gate}/reject`, json("POST", { note }));

export const getTemplates = () => req<{ id: string }[]>("/templates");

export const getHealth = () => req<Health>("/health");

export const logUrl = (sessionId: string) => `/worker-sessions/${sessionId}/log`;
