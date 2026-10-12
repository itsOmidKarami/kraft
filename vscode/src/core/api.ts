import type {
  CommentReview, Finding, GatelessChanges, ReviewComment, ReviewOutcome, ReviewThread, Suggestion, ThreadLabel, WorkItem as BoardItem,
} from "../../../frontend/src/types";
import type { WorkItemDiff } from "../../../frontend/src/types/documents";
import type { ConnectedRepo } from "./repoScope";
import type { ItemAction } from "../../../frontend/src/types/vocab.generated";

export type { CommentReview, Finding, GatelessChanges, ItemAction, ReviewComment, ReviewOutcome, ReviewThread, Suggestion, ThreadLabel, WorkItemDiff };
/** A board row, with the doors the server would admit now (absent from a server before `actions`). */
export type WorkItem = BoardItem & { actions?: ItemAction[] };

export interface Health { version: string; run_dir: string; status: string }
export type WorkItemDetail = WorkItem & {
  steerable?: boolean;
  deferred_findings?: Finding[];
  judge_stop_note?: { node_id: string; reasoning: string; findings: Finding[] }[];
};
export interface Artifact { path: string; title: string; content: string; digest?: string; truncated: boolean }
export interface Position { file: string; line: number; column: number }
export interface ConfigIssue { file: string; chain: string | null; message: string; line: number; column: number; related: Position | null }
export interface LintReport { valid: boolean; chains: string[]; issues: ConfigIssue[] }
export interface ReloadReport { valid: string[]; invalid_templates: Record<string, string>; refused_policy: string | null; refused_intake: string | null }
export interface ApplyItem { id: string; file: string; text: string; problem?: string }
export interface ApplyReport { restart: ApplyItem[]; reload: ApplyItem[]; managed: boolean }

/** A new thread: no `start_side`, `quote` or `anchor_sha` (the server fills those). */
export interface ThreadIn { body: string; file_path?: string; side?: "old" | "new"; start_line?: number; end_line?: number; label?: ThreadLabel | null }
export interface ReviewIn { outcome: ReviewOutcome; summary?: string; node?: string; digest?: string }
/** A gate's approve or reject answers with the item. */
export type ReviewResult = CommentReview | GatelessChanges | WorkItemDetail;

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

export class Api {
  constructor(
    readonly base: string,
    private token: string | undefined,
    private fetchImpl: typeof fetch = fetch,
  ) {}

  headers(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  wsUrl(afterSeq: number): string {
    return `${this.base.replace(/^http/, "ws")}/api/ws/events?after_seq=${afterSeq}`;
  }

  private async req<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.base}/api${path}`, {
        method,
        headers: { ...this.headers(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError(0, `Kraft is not reachable at ${this.base}: ${(e as Error).message}`);
    }
    const text = await res.text();
    const data = text ? JSON.parse(text) : undefined;
    if (!res.ok) throw new ApiError(res.status, typeof data?.detail === "string" ? data.detail : text || res.statusText);
    return data as T;
  }

  private item(id: string, rest = ""): string {
    return `/work-items/${encodeURIComponent(id)}${rest}`;
  }

  health = () => this.req<Health>("GET", "/health");
  repos = () => this.req<{ repos: ConnectedRepo[] }>("GET", "/repos");
  // Cancelled items stay listed: the board shows them under Done.
  listItems = () => this.req<{ items: WorkItem[]; cursor: number }>("GET", "/work-items?include_abandoned=true");
  getItem = (id: string) => this.req<WorkItemDetail>("GET", this.item(id));
  getDiff = (id: string) => this.req<WorkItemDiff & { worktree_path?: string }>("GET", this.item(id, "/diff"));

  async getArtifact(id: string): Promise<Artifact | null> {
    try {
      return await this.req<Artifact>("GET", this.item(id, "/artifact"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }

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
  /** At a gate, the gate's review route (409 when that gate moved on); with none, the item's. */
  submitReview = (id: string, gate: string | null, r: ReviewIn) =>
    this.req<ReviewResult>("POST", this.item(id, gate ? `/gates/${encodeURIComponent(gate)}/review` : "/review"), r);

  check = (file: string, text: string) => this.req<{ issues: ConfigIssue[] }>("POST", "/templates/check", { file, text });
  lint = () => this.req<LintReport>("GET", "/templates/lint");
  resolved = (id: string) => this.req<unknown>("GET", `/templates/chains/${encodeURIComponent(id)}/resolved`);
  parse = (text: string) => this.req<{ chain: unknown; error: string | null }>("POST", "/templates/parse", { text });
  resolve = (chain: unknown) => this.req<{ chains: unknown[]; issues: ConfigIssue[] }>("POST", "/templates/resolve", { chain });
  reload = () => this.req<ReloadReport>("POST", "/templates/reload");
  pending = () => this.req<ApplyReport>("GET", "/apply");
  restart = () => this.req<{ restarting: boolean }>("POST", "/apply/restart");
}
