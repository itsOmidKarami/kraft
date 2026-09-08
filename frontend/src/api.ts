import type {
  Access,
  Analytics,
  AuthSession,
  Bead,
  DocumentDetail,
  Health,
  Intake,
  KraftEvent,
  LogLine,
  SessionStatus,
  SteeringList,
  HookBinding,
  Notify,
  Policy,
  Repo,
  RepoProbe,
  SearchResponse,
  TemplateNode,
  TemplateSummary,
  TemplateValidation,
  WorkItem,
  WorkItemArtifact,
  WorkItemDiff,
  WorkItemDocument,
  WorkerSession,
} from "./types";

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
    // A 401 means the LAN bind is on and this browser has no session. Every
    // caller would otherwise have to recognise it; instead the app listens for
    // this once and routes to the login screen.
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("kraft:unauthenticated"));
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
  description?: string;
  chain_template?: string;
  submodules?: string[];
  root_merge_policy?: string;
  attachments?: { kind: "spec" | "plan"; path: string }[];
}) => req<{ id: string }>("/work-items", json("POST", body));

export const updateWorkItem = (id: string, description: string) =>
  req<{ id: string; description: string }>(
    `/work-items/${id}`,
    json("PATCH", { description }),
  );

export const approveGate = (id: string, gate: string) =>
  req<void>(`/work-items/${id}/gates/${gate}/approve`, { method: "POST" });

export const rejectGate = (id: string, gate: string, note: string) =>
  req<void>(`/work-items/${id}/gates/${gate}/reject`, json("POST", { note }));

export const pauseWorkItem = (id: string) =>
  req<{ id: string; paused_sessions: string[] }>(`/work-items/${id}/pause`, json("POST", {}));

export const resumeWorkItem = (id: string, steer?: string) =>
  req<{ id: string; node_id: string | null; steer: string | null }>(
    `/work-items/${id}/resume`,
    json("POST", { steer: steer ?? null }),
  );

export const retryWorkItem = (id: string, steer?: string) =>
  req<{ id: string; node_id: string; loop: string; steer: string | null }>(
    `/work-items/${id}/retry`,
    json("POST", { steer: steer ?? null }),
  );

export const openDocument = (id: string, editor?: string) =>
  req<{ document_id: string; path: string; editor: string }>(
    `/documents/${encodeURIComponent(id)}/open`,
    json("POST", { editor: editor ?? null }),
  );

export const getTemplates = () => req<TemplateSummary[]>("/templates");

export const getHealth = () => req<Health>("/health");

export const getAnalytics = (params: { range: string; repo?: string; template?: string }) => {
  const qs = new URLSearchParams({ range: params.range });
  if (params.repo) qs.set("repo", params.repo);
  if (params.template) qs.set("template", params.template);
  return req<Analytics>(`/analytics?${qs}`);
};

export const logUrl = (sessionId: string) => `/worker-sessions/${sessionId}/log`;

export const getLogLines = (sessionId: string) =>
  req<{ session_id: string; status: SessionStatus; lines: LogLine[] }>(
    `${logUrl(sessionId)}?format=jsonl`,
  );

/** SSE tail; the server closes the stream when the session stops running. */
export const logStreamUrl = (sessionId: string) => `${logUrl(sessionId)}?format=jsonl&follow=1`;

export const search = (params: {
  q: string;
  source_kind?: string;
  kind?: string;
  repo?: string;
  mode?: string;
  limit?: number;
}) => {
  const qs = new URLSearchParams({ q: params.q });
  if (params.mode) qs.set("mode", params.mode);
  if (params.source_kind) qs.set("source_kind", params.source_kind);
  if (params.kind) qs.set("kind", params.kind);
  if (params.repo) qs.set("repo", params.repo);
  if (params.limit) qs.set("limit", String(params.limit));
  return req<SearchResponse>(`/search?${qs}`);
};

export const getDocument = (id: string) =>
  req<DocumentDetail>(`/documents/${encodeURIComponent(id)}`);

export const getWorkItemDocuments = (id: string) =>
  req<{ work_item_id: string; documents: WorkItemDocument[] }>(
    `/work-items/${encodeURIComponent(id)}/documents`,
  );

export const getWorkItemDiff = (id: string) =>
  req<WorkItemDiff>(`/work-items/${id}/diff`);

export const getWorkItemArtifact = (id: string) =>
  req<WorkItemArtifact>(`/work-items/${encodeURIComponent(id)}/artifact`);

/* ── settings (design 5a–5e) ─────────────────────────────────────────────── */

export const getRepos = () => req<{ repos: Repo[] }>("/repos");
export const probeRepo = (path: string) => req<RepoProbe>("/repos/probe", json("POST", { path }));
export const addRepo = (body: Partial<Repo> & { path: string }) =>
  req<Repo>("/repos", json("POST", body));
export const patchRepo = (path: string, body: Partial<Repo>) =>
  req<Repo>(`/repos?path=${encodeURIComponent(path)}`, json("PATCH", body));
export const deleteRepo = (path: string) =>
  req<void>(`/repos?path=${encodeURIComponent(path)}`, { method: "DELETE" });

export const getTemplate = (id: string) =>
  req<{ id: string; nodes: TemplateNode[] }>(`/templates/${encodeURIComponent(id)}`);
export const validateTemplate = (id: string, nodes: TemplateNode[]) =>
  req<TemplateValidation>(`/templates/${encodeURIComponent(id)}/validate`, json("POST", { nodes }));
export const putTemplate = (id: string, nodes: TemplateNode[]) =>
  req<{ id: string; nodes: TemplateNode[] }>(
    `/templates/${encodeURIComponent(id)}`,
    json("PUT", { nodes }),
  );

export const getRegistry = () => req<{ hooks: Record<string, HookBinding> }>("/registry");
export const putRegistry = (hooks: Record<string, HookBinding>) =>
  req<{ hooks: Record<string, HookBinding>; invalid_templates: Record<string, string> }>(
    "/registry",
    json("PUT", { hooks }),
  );

export const getPolicy = () => req<Policy>("/policy");
export const putPolicy = (policy: Policy) => req<Policy>("/policy", json("PUT", policy));

export const getSteering = () => req<SteeringList>("/steering");
export const getSteeringFile = (name: string) =>
  req<{ name: string; body: string }>(`/steering/${encodeURIComponent(name)}`);
export const putSteeringFile = (name: string, body: string) =>
  req<{ name: string; body: string }>(
    `/steering/${encodeURIComponent(name)}`,
    json("PUT", { body }),
  );
export const deleteSteeringFile = (name: string) =>
  req<{ deleted: string }>(`/steering/${encodeURIComponent(name)}`, { method: "DELETE" });

export const getIntake = () => req<Intake>("/intake");
export const putIntake = (intake: Intake) => req<Intake>("/intake", json("PUT", intake));

export const getAccess = () => req<Access>("/access");
export const putAccess = (body: {
  bind?: string;
  port?: number;
  password?: string;
  session_expiry_days?: number;
}) => req<Access>("/access", json("PUT", body));

export const getNotify = () => req<Notify>("/notify");
export const putNotify = (body: {
  enabled?: boolean;
  url?: string;
  base_url?: string;
  events?: string[];
}) => req<Notify>("/notify", json("PUT", body));

export const getAuthSessions = () => req<{ sessions: AuthSession[] }>("/sessions");
export const revokeSession = (id: string) =>
  req<void>(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });

export const login = (password: string) => req<{ ok: true }>("/login", json("POST", { password }));
export const logout = () => req<void>("/logout", { method: "POST" });

export const searchBeads = (q: string) =>
  req<{ query: string; beads: Bead[] }>(`/beads/search?q=${encodeURIComponent(q)}`);

export const openWorktree = (id: string, editor?: string) =>
  req<{ path: string; editor: string }>(
    `/work-items/${id}/open-worktree`,
    json("POST", { editor: editor ?? null }),
  );
