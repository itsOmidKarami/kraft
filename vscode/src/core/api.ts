import type {
  BudgetToday, BulkResult, ChainNode, CommentReview, Finding, FixTarget, GatelessChanges, LogLine, Repo, ReviewComment, ReviewOutcome, ReviewThread, Suggestion, TemplateSummary,
  ThreadLabel, WorkerSession, WorkItem as BoardItem,
} from "../../../frontend/src/types";
import type { DocumentDetail, SearchResponse, WorkItemDiff } from "../../../frontend/src/types/documents";
import type { ItemAction } from "../../../frontend/src/types/vocab.generated";
import { parseSse } from "./logStream";

export type {
  BulkResult, CommentReview, DocumentDetail, Finding, FixTarget, GatelessChanges, ItemAction, Repo, ReviewComment, ReviewOutcome, ReviewThread, Suggestion, TemplateSummary,
  ThreadLabel,
  WorkItemDiff,
};
/** A board row, with the doors the server would admit now (absent from a server before `actions`). */
export type WorkItem = BoardItem & { actions?: ItemAction[] };

export interface Health { version: string; run_dir: string; status: string }
export type WorkItemDetail = WorkItem & {
  steerable?: boolean;
  deferred_findings?: Finding[];
  judge_stop_note?: { node_id: string; reasoning: string; findings: Finding[] }[];
  worker_sessions?: WorkerSession[];
};
/** A spec or plan as it was attached at intake (`GET /work-items/{id}/attachments/{kind}`). */
export interface AttachmentDoc { work_item_id: string; kind: "spec" | "plan"; path: string; title: string; content: string; truncated: boolean }
export interface Artifact { path: string; title: string; content: string; digest?: string; truncated: boolean }
export interface Position { file: string; line: number; column: number }
export interface ConfigIssue { file: string; chain: string | null; message: string; line: number; column: number; related: Position | null }
export interface LintReport { valid: boolean; chains: string[]; issues: ConfigIssue[] }
export interface ReloadReport { valid: string[]; invalid_templates: Record<string, string>; refused_policy: string | null; refused_intake: string | null }
export interface ApplyItem { id: string; file: string; text: string; problem?: string }
export interface ApplyReport { restart: ApplyItem[]; reload: ApplyItem[]; managed: boolean }
/** `POST /work-items`'s body as VS Code files it: `chain` absent is the repo's default. */
export interface NewWorkItem {
  title: string; description: string; repo: string; chain?: string;
  attachments?: { kind: "spec" | "plan"; path: string }[]; cwd?: string; autostart: boolean;
}
/** `?dry_run=1`'s answer: the chain as it would be filed, and what it drops. */
export interface DryRun {
  dry_run: true; nodes: ChainNode[]; gates: string[];
  skipped: { node: string; why: "covered_by" | "skip"; kind?: "spec" | "plan" }[];
}
/** What a create answers; the warnings are present only when there is one. */
export interface Created {
  id: string; status: string; bead_id?: string | null;
  duplicate_warning?: string; bead_warning?: string; repo_warning?: string;
  waiting_on?: { id: string; title: string; status: string }[];
  slots?: { busy: number; limit: number; storage?: { used_bytes: number; limit_bytes: number } };
}

/** A daemon that takes the connection and never answers must not hold a caller forever. */
export const REQUEST_TIMEOUT = 10_000;
/** For the calls that can legitimately take long: a big diff, a library lint, reload, apply. */
export const SLOW_TIMEOUT = 60_000;
export interface Timeouts { fast: number; slow: number }
/** A new thread: no `start_side` or `anchor_sha` (the server fills those); `quote` is the lines as the editor shows them. */
export interface ThreadIn { body: string; file_path?: string; side?: "old" | "new"; start_line?: number; end_line?: number; label?: ThreadLabel | null; quote?: string }
export interface ReviewIn { outcome: ReviewOutcome; summary?: string; node?: string; digest?: string }
/** A gate's approve or reject answers with the item. */
export type ReviewResult = CommentReview | GatelessChanges | WorkItemDetail;

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

export interface ApiFailure { method: string; path: string; status: number; detail: string }

const SLOW = { slow: true };
/** The most ids `POST /work-items/bulk` takes in one request. */
export const BULK_LIMIT = 200;

export class Api {
  /** Every failed call, just before it throws: the extension logs them. */
  onError?: (f: ApiFailure) => void;

  constructor(
    readonly base: string,
    private token: string | undefined,
    private fetchImpl: typeof fetch = fetch,
    private timeouts: Timeouts = { fast: REQUEST_TIMEOUT, slow: SLOW_TIMEOUT },
  ) {}

  headers(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  wsUrl(afterSeq: number): string {
    return `${this.base.replace(/^http/, "ws")}/api/ws/events?after_seq=${afterSeq}`;
  }

  private async req<T>(method: string, path: string, body?: unknown, { slow = false }: { slow?: boolean } = {}): Promise<T> {
    const timeout = slow ? this.timeouts.slow : this.timeouts.fast;
    let res: Response;
    let text: string;
    try {
      res = await this.fetchImpl(`${this.base}/api${path}`, {
        method,
        headers: { ...this.headers(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeout),
      });
      text = await res.text();
    } catch (e) {
      const detail = (e as Error).name === "TimeoutError"
        ? `Kraft did not answer within ${timeout / 1000}s`
        : `Kraft is not reachable at ${this.base}: ${(e as Error).message}`;
      throw this.fail(method, path, 0, detail);
    }
    const data = text ? JSON.parse(text) : undefined;
    if (!res.ok) throw this.fail(method, path, res.status, typeof data?.detail === "string" ? data.detail : text || res.statusText);
    return data as T;
  }

  /** A session's log, tail first, until the server's `end` frame. No timeout: it
   *  lasts as long as the session. Throws `ApiError` on a refusal or a drop
   *  (status 0, so a stream that closes before `end` is a failure too); an
   *  abort through `signal` just ends it. */
  async *streamLog(sid: string, signal: AbortSignal): AsyncGenerator<LogLine> {
    const path = `/worker-sessions/${encodeURIComponent(sid)}/log?format=jsonl&follow=true`;
    const why = (e: unknown) => (e as Error).message;
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.base}/api${path}`, { headers: this.headers(), signal });
    } catch (e) {
      if (signal.aborted) return;
      throw this.fail("GET", path, 0, `Kraft is not reachable at ${this.base}: ${why(e)}`);
    }
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      let detail: unknown;
      try { detail = JSON.parse(text)?.detail; } catch { /* not JSON */ }
      throw this.fail("GET", path, res.status, typeof detail === "string" ? detail : text || res.statusText);
    }
    if (!res.body) throw this.fail("GET", path, 0, "the log stream has no body");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let rest = "";
    try {
      for (;;) {
        let chunk: Awaited<ReturnType<typeof reader.read>>;
        try {
          chunk = await reader.read();
        } catch (e) {
          if (signal.aborted) return;
          throw this.fail("GET", path, 0, `the log stream dropped: ${why(e)}`);
        }
        if (chunk.done) break;
        const parsed = parseSse(rest + decoder.decode(chunk.value, { stream: true }));
        rest = parsed.rest;
        for (const f of parsed.frames) {
          if (f.event === "end") return;
          if (f.event !== "message") continue;
          let l: LogLine;
          try {
            l = JSON.parse(f.data) as LogLine;
          } catch (e) {
            throw this.fail("GET", path, 0, `unreadable log line: ${why(e)}`);
          }
          yield l;
        }
      }
    } finally {
      reader.cancel().catch(() => {});
    }
    if (signal.aborted) return;
    throw this.fail("GET", path, 0, "the log stream closed before the session ended");
  }

  private fail(method: string, path: string, status: number, detail: string): ApiError {
    this.onError?.({ method, path, status, detail });
    return new ApiError(status, detail);
  }

  private item(id: string, rest = ""): string {
    return `/work-items/${encodeURIComponent(id)}${rest}`;
  }

  health = () => this.req<Health>("GET", "/health");
  budgetToday = () => this.req<BudgetToday>("GET", "/budget/today");
  repos = () => this.req<{ repos: Repo[] }>("GET", "/repos");
  /** 201 connected; 409 already connected, 422 no commit yet, 400 unreadable. Slow: it probes the repo. */
  connectRepo = (path: string) => this.req<unknown>("POST", "/repos", { path }, SLOW);
  chains = () => this.req<TemplateSummary[]>("GET", "/templates/chains");
  // Both resolve the chain on the server, and a create also runs intake.
  preview = (body: NewWorkItem) => this.req<DryRun>("POST", "/work-items?dry_run=1", body, SLOW);
  create = (body: NewWorkItem) => this.req<Created>("POST", "/work-items", body, SLOW);
  // Cancelled items stay listed: the board shows them under Done.
  listItems = () => this.req<{ items: WorkItem[]; cursor: number }>("GET", "/work-items?include_abandoned=true");
  getItem = (id: string) => this.req<WorkItemDetail>("GET", this.item(id));
  getDiff = (id: string) => this.req<WorkItemDiff & { worktree_path?: string }>("GET", this.item(id, "/diff"), undefined, SLOW);

  async getArtifact(id: string): Promise<Artifact | null> {
    try {
      return await this.req<Artifact>("GET", this.item(id, "/artifact"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }

  /** 404 when the item has none of that kind, or Kraft lost its copy. */
  attachment = (id: string, kind: "spec" | "plan") => this.req<AttachmentDoc>("GET", this.item(id, `/attachments/${kind}`));
  document = (docId: string) => this.req<DocumentDetail>("GET", `/documents/${encodeURIComponent(docId)}`);
  /** Blank `q` is a 422; the server clamps `limit` to 1–100. Slow: the first hybrid query can load the embedder. */
  search = (q: string, limit = 20) => this.req<SearchResponse>("GET", `/search?q=${encodeURIComponent(q)}&limit=${limit}`, undefined, SLOW);

  approve = (id: string, gate: string, digest?: string) =>
    this.req("POST", this.item(id, `/gates/${encodeURIComponent(gate)}/approve`), digest ? { digest } : {});
  reject = (id: string, gate: string, note: string) =>
    this.req("POST", this.item(id, `/gates/${encodeURIComponent(gate)}/reject`), { note });
  pause = (id: string) => this.req("POST", this.item(id, "/pause"));
  resume = (id: string, steer?: string) => this.req("POST", this.item(id, "/resume"), steer ? { steer } : {});
  retry = (id: string, steer?: string) => this.req("POST", this.item(id, "/retry"), steer ? { steer } : {});
  cancel = (id: string, reason: string) => this.req("POST", this.item(id, "/cancel"), { reason });
  skip = (id: string, note?: string) => this.req("POST", this.item(id, "/skip"), note ? { note } : {});
  escalate = (id: string, message: string) => this.req("POST", this.item(id, "/escalate"), { message });
  archive = (id: string) => this.req("POST", this.item(id, "/archive"));
  /** `usd` null lifts the cap. */
  raiseBudget = (id: string, usd: number | null) => this.req("POST", this.item(id, "/budget/raise"), { budget_usd: usd });
  unblock = (id: string) => this.req("POST", this.item(id, "/unblock"));
  complete = (id: string, reason: string) => this.req("POST", this.item(id, "/complete"), { reason });
  restore = (id: string) => this.req("POST", this.item(id, "/restore"));
  /** One request per 200 ids, the server's cap, in order; their results joined.
   *  Slow: the server runs each id's own route in turn. */
  async bulk(action: "pause" | "archive" | "restore", ids: string[]): Promise<BulkResult[]> {
    const results: BulkResult[] = [];
    for (let k = 0; k < ids.length; k += BULK_LIMIT) {
      const r = await this.req<{ results: BulkResult[] }>("POST", "/work-items/bulk", { action, ids: ids.slice(k, k + BULK_LIMIT) }, SLOW);
      results.push(...r.results);
    }
    return results;
  }
  abandon = (id: string) => this.req("POST", this.item(id, "/abandon"), {});

  listThreads = (id: string) => this.req<ReviewThread[]>("GET", this.item(id, "/threads"));
  createThread = (id: string, t: ThreadIn) => this.req<ReviewThread>("POST", this.item(id, "/threads"), t);
  /** Only the keys present change: `{ label: null }` clears the label and keeps the body. */
  patchThread = (tid: string, p: { body?: string; label?: ThreadLabel | null }) => this.req<ReviewThread>("PATCH", `/threads/${encodeURIComponent(tid)}`, p);
  deleteThread = (tid: string) => this.req<void>("DELETE", `/threads/${encodeURIComponent(tid)}`);
  addComment = (tid: string, body: string) => this.req<ReviewComment>("POST", `/threads/${encodeURIComponent(tid)}/comments`, { body });
  /** The server replaces `suggestion` with what is sent, so an edit sends the existing one back. */
  patchComment = (cid: string, p: { body: string; suggestion: Suggestion | null }) => this.req<ReviewComment>("PATCH", `/comments/${encodeURIComponent(cid)}`, p);
  deleteComment = (cid: string) => this.req<void>("DELETE", `/comments/${encodeURIComponent(cid)}`);
  resolveThread = (tid: string) => this.req<ReviewThread>("POST", `/threads/${encodeURIComponent(tid)}/resolve`);
  reopenThread = (tid: string) => this.req<ReviewThread>("POST", `/threads/${encodeURIComponent(tid)}/reopen`);
  /** Where Request changes would restart now; gateless it reads git per thread, so it may take a while. */
  fixTarget = (id: string) => this.req<FixTarget>("GET", this.item(id, "/fix-target"), undefined, SLOW);
  /** At a gate, the gate's review route (409 when that gate moved on); with none, the item's. */
  submitReview = (id: string, gate: string | null, r: ReviewIn) =>
    this.req<ReviewResult>("POST", this.item(id, gate ? `/gates/${encodeURIComponent(gate)}/review` : "/review"), r);

  check = (file: string, text: string) => this.req<{ issues: ConfigIssue[] }>("POST", "/templates/check", { file, text });
  lint = () => this.req<LintReport>("GET", "/templates/lint", undefined, SLOW);
  resolved = (id: string) => this.req<unknown>("GET", `/templates/chains/${encodeURIComponent(id)}/resolved`, undefined, SLOW);
  parse = (text: string) => this.req<{ chain: unknown; error: string | null }>("POST", "/templates/parse", { text }, SLOW);
  resolve = (chain: unknown) => this.req<{ chains: unknown[]; issues: ConfigIssue[] }>("POST", "/templates/resolve", { chain }, SLOW);
  reload = () => this.req<ReloadReport>("POST", "/templates/reload", undefined, SLOW);
  pending = () => this.req<ApplyReport>("GET", "/apply", undefined, SLOW);
  restart = () => this.req<{ restarting: boolean }>("POST", "/apply/restart", undefined, SLOW);
}
