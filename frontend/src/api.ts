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
  Notify,
  Policy,
  Repo,
  Workspace,
  Theme,
  RepoProbe,
  NodeOverrides,
  SearchResponse,
  ChainFile,
  ChainNode,
  Library,
  ResolveResult,
  TemplateSummary,
  WorkItem,
  WorkItemArtifact,
  WorkItemDiff,
  WorkItemDocument,
  WorkerSession,
} from "./types";

/** Every JSON route lives under /api/ (Kraft-psuq). `req()` below is where
 *  most callers end up, but `logStreamUrl`/`getLogText` build a request
 *  outside it (EventSource, a plain-text fetch) and need the same prefix. */
const apiUrl = (path: string) => `/api${path}`;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      headers: { accept: "application/json", ...init?.headers },
    });
  } catch (e) {
    // A transport-level failure rejects with a bare `TypeError: Failed to
    // fetch`, which names neither the server nor the request — and every
    // caller renders the message straight into the UI. Named here, once,
    // rather than in each caller. Anything else rethrows untouched.
    if (e instanceof TypeError) {
      throw new Error(
        `could not reach the Kraft server (${init?.method ?? "GET"} ${path}) — it may have stopped`,
      );
    }
    throw e;
  }
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

// include_abandoned=true: abandoned items sit under Done until archived, so
// the board's normal (non-archived) view must still see them.
export const listWorkItems = () =>
  req<{ items: WorkItem[]; cursor: number }>(
    "/work-items?include_abandoned=true",
  );

export const getWorkItem = (id: string) =>
  req<WorkItem & { worker_sessions: WorkerSession[] }>(`/work-items/${id}`);

export const getEvents = (id: string, afterSeq = 0) =>
  req<KraftEvent[]>(`/work-items/${id}/events?after_seq=${afterSeq}`);

export const createWorkItem = (body: {
  repo: string;
  title: string;
  description?: string;
  chain_template?: string;
  /** A workspace item: the workspace `repo` roots, the member ids picked,
   *  and the root-pointer policy (`ignore`/`bump`). */
  workspace?: string;
  members?: string[];
  root_pointer_policy?: "ignore" | "bump";
  attachments?: { kind: "spec" | "plan"; path: string }[];
  /** Node ids to drop from the materialized chain at intake (UI v2 · 04
   *  point 6; design 10/m09's click-to-skip). */
  skip_nodes?: string[];
  /** `undefined`: policy default. `null`: explicit "no cap". A number: that cap. */
  budget_usd?: number | null;
  node_overrides?: NodeOverrides;
  /** False creates the item without running it (design §6 rule 1). Defaults
   *  server-side to true; the dialog sends it explicitly either way. */
  autostart?: boolean;
  /** Arms agent gate review for this item's `auto_escalate` gates (Kraft-zr3s). */
  auto_gate?: boolean;
}) => req<{ id: string }>("/work-items", json("POST", body));

/** Absent fields are untouched, not cleared: the title editor and the
 *  description editor each send one field and must not blank the other. The
 *  response echoes only the fields that were set.
 *
 *  `node_overrides`: `undefined` leaves overrides alone, `{}` resets every
 *  node to the template (409 once the item has started), a non-empty object
 *  is per node id and merges into what's stored (UI v2 · 04 point 1/2).
 *  `budget_usd`: `undefined` leaves the cap alone, `null` sets an explicit
 *  "no cap", a number sets that cap (point 4). */
export const updateWorkItem = (
  id: string,
  patch: {
    title?: string;
    description?: string;
    chain_template?: string;
    agent_overrides?: Record<string, unknown>;
    node_overrides?: NodeOverrides;
    budget_usd?: number | null;
  },
) =>
  req<{ id: string } & typeof patch>(`/work-items/${id}`, json("PATCH", patch));

/** Drop every node override back to the template -- refused (409) once the
 *  item has started (UI v2 · 04 point 2). */
export const resetChainOverrides = (id: string) => updateWorkItem(id, { node_overrides: {} });

/** Raise a work item's spend cap and continue it from wherever the budget
 *  stopped it (UI v2 · 04 point 5; Prototype `raiseBudget`). `null` sets "no
 *  cap". 409s unless the item is `needs_human`. */
export const raiseBudget = (id: string, budgetUsd: number | null) =>
  req<{ id: string; node_id: string; loop: string; steer: string | null }>(
    `/work-items/${id}/budget/raise`,
    json("POST", { budget_usd: budgetUsd }),
  );

export const approveGate = (id: string, gate: string) =>
  req<void>(`/work-items/${id}/gates/${encodeURIComponent(gate)}/approve`, { method: "POST" });

export const rejectGate = (id: string, gate: string, note: string) =>
  req<void>(`/work-items/${id}/gates/${encodeURIComponent(gate)}/reject`, json("POST", { note }));

export const pauseWorkItem = (id: string) =>
  req<{ id: string; paused_sessions: string[] }>(`/work-items/${id}/pause`, json("POST", {}));

export const resumeWorkItem = (id: string, steer?: string) =>
  req<{ id: string; node_id: string | null; steer: string | null }>(
    `/work-items/${id}/resume`,
    json("POST", { steer: steer ?? null }),
  );

export const abandonWorkItem = (id: string) =>
  req<{ id: string; status: string; worktree_removed: boolean }>(
    `/work-items/${id}/abandon`,
    json("POST", {}),
  );

export const archiveWorkItem = (id: string) =>
  req<{ id: string; archived_by: string; worktree_removed: boolean }>(
    `/work-items/${id}/archive`,
    json("POST", {}),
  );

export const restoreWorkItem = (id: string) =>
  req<{ id: string; status: string }>(`/work-items/${id}/restore`, json("POST", {}));

export const listArchivedWorkItems = () =>
  // include_abandoned=true: the archived view is the only way to restore an
  // abandoned item (the auto-archive poller and the Done group's Archive
  // action both archive abandoned items), so it must not drop them.
  req<{ items: WorkItem[]; cursor: number }>(
    "/work-items?archived=true&include_abandoned=true",
  );

export const retryWorkItem = (id: string, steer?: string) =>
  req<{ id: string; node_id: string; loop: string; steer: string | null }>(
    `/work-items/${id}/retry`,
    json("POST", { steer: steer ?? null }),
  );

export const skipWorkItem = (id: string, note?: string) =>
  req<void>(`/work-items/${id}/skip`, json("POST", { note: note ?? null }));

export const escalateWorkItem = (id: string, message: string, newThread = false) =>
  req<{ id: string; status: string }>(
    `/work-items/${id}/escalate`,
    json("POST", { message, new_thread: newThread }),
  );

export const stopEscalation = (id: string) =>
  req<{ id: string; session_id: string; status: string }>(
    `/work-items/${id}/escalate/stop`,
    { method: "POST" },
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
export const logStreamUrl = (sessionId: string) =>
  `${apiUrl(logUrl(sessionId))}?format=jsonl&follow=1`;

/** The log as plain text, whole -- `getLogLines` truncates each line for
 *  rendering, and a copied log has to be the file. */
export const getLogText = async (sessionId: string): Promise<string> => {
  const res = await fetch(apiUrl(logUrl(sessionId)), { headers: { accept: "text/plain" } });
  if (!res.ok) throw new Error(`could not read the log (${res.status})`);
  return res.text();
};

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

export const getRepos = () =>
  req<{ repos: Repo[]; workspaces?: Record<string, Workspace> }>("/repos");
export const probeRepo = (path: string) => req<RepoProbe>("/repos/probe", json("POST", { path }));
export const addRepo = (body: Partial<Repo> & { path: string }) =>
  req<Repo>("/repos", json("POST", body));
export const patchRepo = (path: string, body: Partial<Repo>) =>
  req<Repo>(`/repos?path=${encodeURIComponent(path)}`, json("PATCH", body));
export const deleteRepo = (path: string) =>
  req<void>(`/repos?path=${encodeURIComponent(path)}`, { method: "DELETE" });

export const getTemplate = (id: string) => req<ChainFile>(`/templates/${encodeURIComponent(id)}`);
/** A saved chain resolved, not materialized: its nodes in `ChainNode` shape. */
export const getResolvedTemplate = (id: string) =>
  req<{ id: string; nodes: ChainNode[] }>(`/templates/${encodeURIComponent(id)}/resolved`);
/** Save one chain file's text; the server refuses (422) a chain the library
 *  does not resolve. */
export const putTemplate = (id: string, text: string) =>
  req<{ id: string; file: string; text: string }>(
    `/templates/${encodeURIComponent(id)}`,
    json("PUT", { text }),
  );
export const getLibrary = () => req<Library>("/templates/library");
/** Save `library.yaml`'s text; the server refuses (422, naming why) a library
 *  that would stop any chain that resolves now from resolving. */
export const putLibrary = (text: string) => req<Library>("/templates/library", json("PUT", { text }));
/** Typed YAML into the mapping `resolveTemplate` checks. */
export const parseTemplateYaml = (text: string) =>
  req<{ chain: Record<string, unknown> | null; error: string | null }>(
    "/templates/parse",
    json("POST", { text }),
  );
/** Check an unsaved chain against the installed library; writes nothing. */
export const resolveTemplate = (chain: Record<string, unknown>) =>
  req<ResolveResult>("/templates/resolve", json("POST", { chain }));

export const getPolicy = () => req<Policy>("/policy");
export const putPolicy = (policy: Policy) => req<Policy>("/policy", json("PUT", policy));

export const getTheme = () => req<Theme>("/theme");
export const putTheme = (theme: Theme) => req<Theme>("/theme", json("PUT", theme));

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
  allowed_hosts?: string[];
}) => req<Access>("/access", json("PUT", body));

export const getNotify = () => req<Notify>("/notify");
export const putNotify = (body: {
  enabled?: boolean;
  url?: string;
  base_url?: string;
  events?: string[];
}) => req<Notify>("/notify", json("PUT", body));
export const testNotify = () =>
  req<{ at: string; status: number | null; ms: number | null; error: string | null }>(
    "/notify/test",
    { method: "POST" },
  );

export const getAuthSessions = () => req<{ sessions: AuthSession[] }>("/sessions");
export const revokeSession = (id: string) =>
  req<void>(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });

export const login = (password: string, stay_signed_in = true) =>
  req<{ ok: true }>("/login", json("POST", { password, stay_signed_in }));
export const logout = () => req<void>("/logout", { method: "POST" });

export const searchBeads = (q: string) =>
  req<{ query: string; beads: Bead[] }>(`/beads/search?q=${encodeURIComponent(q)}`);

export const openWorktree = (id: string, editor?: string) =>
  req<{ path: string; editor: string }>(
    `/work-items/${id}/open-worktree`,
    json("POST", { editor: editor ?? null }),
  );
