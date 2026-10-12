import type { Finding, WorkItem as BoardItem } from "../../../frontend/src/types";
import type { WorkItemDiff } from "../../../frontend/src/types/documents";
import type { ConnectedRepo } from "./repoScope";
import type { ItemAction } from "../../../frontend/src/types/vocab.generated";

export type { Finding, ItemAction, WorkItemDiff };
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

/** A daemon that takes the connection and never answers must not hold a caller forever. */
export const REQUEST_TIMEOUT = 10_000;
/** For the calls that can legitimately take long: a big diff, a library lint, reload, apply. */
export const SLOW_TIMEOUT = 60_000;
export interface Timeouts { fast: number; slow: number }

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

export interface ApiFailure { method: string; path: string; status: number; detail: string }

const SLOW = { slow: true };

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

  private fail(method: string, path: string, status: number, detail: string): ApiError {
    this.onError?.({ method, path, status, detail });
    return new ApiError(status, detail);
  }

  private item(id: string, rest = ""): string {
    return `/work-items/${encodeURIComponent(id)}${rest}`;
  }

  health = () => this.req<Health>("GET", "/health");
  repos = () => this.req<{ repos: ConnectedRepo[] }>("GET", "/repos");
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

  check = (file: string, text: string) => this.req<{ issues: ConfigIssue[] }>("POST", "/templates/check", { file, text });
  lint = () => this.req<LintReport>("GET", "/templates/lint", undefined, SLOW);
  resolved = (id: string) => this.req<unknown>("GET", `/templates/chains/${encodeURIComponent(id)}/resolved`, undefined, SLOW);
  parse = (text: string) => this.req<{ chain: unknown; error: string | null }>("POST", "/templates/parse", { text }, SLOW);
  resolve = (chain: unknown) => this.req<{ chains: unknown[]; issues: ConfigIssue[] }>("POST", "/templates/resolve", { chain }, SLOW);
  reload = () => this.req<ReloadReport>("POST", "/templates/reload", undefined, SLOW);
  pending = () => this.req<ApplyReport>("GET", "/apply", undefined, SLOW);
  restart = () => this.req<{ restarting: boolean }>("POST", "/apply/restart", undefined, SLOW);
}
