import type {
  Access,
  Analytics,
  AuthSession,
  Bead,
  Health,
  KraftEvent,
  Notify,
  Policy,
  Repo,
  Workspace,
  Theme,
  RepoProbe,
  SearchResponse,
  ChainFile,
  Harnesses,
  HarnessProviders,
  Library,
  TemplateSummary,
  WorkItem,
  WorkItemDiff,
  WorkItemDocument,
  WorkerSession,
} from "./types";

/** Every JSON route lives under /api/ (Kraft-psuq). `req()` below is where
 *  most callers end up, but `logStreamUrl`/`getLogText` build a request
 *  outside it (EventSource, a plain-text fetch) and need the same prefix. */
const apiUrl = (path: string) => `/api${path}`;

/** An error body's `detail` as one line: a string as it is, and FastAPI's
 *  validation list (`[{loc, msg, type}]`) as its messages, without pydantic's
 *  "Value error, " prefix. Every caller renders it straight into the UI. */
export function detailText(detail: unknown): string | undefined {
  if (detail == null) return undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => (typeof d?.msg === "string" ? d.msg.replace(/^Value error, /, "") : JSON.stringify(d))).join("; ");
  return JSON.stringify(detail);
}

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
      detail = detailText((await res.json())?.detail) ?? detail;
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

/** Raise a work item's spend cap and continue it from wherever the budget
 *  stopped it (UI v2 · 04 point 5; Prototype `raiseBudget`). `null` sets "no
 *  cap". 409s unless the item is `needs_human`. */
export const raiseBudget = (id: string, budgetUsd: number | null) =>
  req<{ id: string; node_id: string; loop: string; steer: string | null }>(
    `/work-items/${id}/budget/raise`,
    json("POST", { budget_usd: budgetUsd }),
  );

export const abandonWorkItem = (id: string) =>
  req<{ id: string; status: string; worktree_removed: boolean }>(
    `/work-items/${id}/abandon`,
    json("POST", {}),
  );

export const listArchivedWorkItems = () =>
  // include_abandoned=true: the archived view is the only way to restore an
  // abandoned item (the auto-archive poller and the Done group's Archive
  // action both archive abandoned items), so it must not drop them.
  req<{ items: WorkItem[]; cursor: number }>(
    "/work-items?archived=true&include_abandoned=true",
  );

export const stopEscalation = (id: string) =>
  req<{ id: string; session_id: string; status: string }>(
    `/work-items/${id}/escalate/stop`,
    { method: "POST" },
  );

export const getTemplates = () => req<TemplateSummary[]>("/templates/chains");

export const getHealth = () => req<Health>("/health");

export const getAnalytics = (params: { range: string; repo?: string; template?: string }) => {
  const qs = new URLSearchParams({ range: params.range });
  if (params.repo) qs.set("repo", params.repo);
  if (params.template) qs.set("template", params.template);
  return req<Analytics>(`/analytics?${qs}`);
};

export const logUrl = (sessionId: string) => `/worker-sessions/${sessionId}/log`;

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

/** Plain-text log URL for a direct browser download -- past the jsonl
 *  reader's cap (Kraft-2vvus), `getLogText` buffering the whole file into
 *  the page is the same choke it was meant to avoid; a `download` link lets
 *  the browser stream it from disk instead. */
export const logTextUrl = (sessionId: string) => apiUrl(logUrl(sessionId));

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

export const getWorkItemDocuments = (id: string) =>
  req<{ work_item_id: string; documents: WorkItemDocument[] }>(
    `/work-items/${encodeURIComponent(id)}/documents`,
  );

export const getWorkItemDiff = (id: string) =>
  req<WorkItemDiff>(`/work-items/${id}/diff`);

/* ── settings (design 5a–5e) ─────────────────────────────────────────────── */

export const getRepos = () =>
  req<{ repos: Repo[]; workspaces?: Record<string, Workspace> }>("/repos");
export const probeRepo = (path: string) => req<RepoProbe>("/repos/probe", json("POST", { path }));
export const addRepo = (body: Partial<Repo> & { path: string }) =>
  req<Repo>("/repos", json("POST", body));

export const getTemplate = (id: string) => req<ChainFile>(`/templates/chains/${encodeURIComponent(id)}`);
export const getLibrary = () => req<Library>("/templates/library");
export const getHarnesses = () => req<Harnesses>("/harnesses/profiles");
export const getHarnessProviders = () => req<HarnessProviders>("/harnesses/providers");

export const getPolicy = () => req<Policy>("/policy");

export const getTheme = () => req<Theme>("/theme");
// The server merges the body over theme.yaml, so a partial one is a patch.
export const putTheme = (theme: Partial<Theme>) => req<Theme>("/theme", json("PUT", theme));

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
