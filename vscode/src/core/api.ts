import type { ChainNode, Finding, Repo, TemplateSummary, WorkItem as BoardItem } from "../../../frontend/src/types";
import type { WorkItemDiff } from "../../../frontend/src/types/documents";
import type { ItemAction } from "../../../frontend/src/types/vocab.generated";

export type { Finding, ItemAction, Repo, TemplateSummary, WorkItemDiff };
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
  repos = () => this.req<{ repos: Repo[] }>("GET", "/repos");
  chains = () => this.req<TemplateSummary[]>("GET", "/templates/chains");
  preview = (body: NewWorkItem) => this.req<DryRun>("POST", "/work-items?dry_run=1", body);
  create = (body: NewWorkItem) => this.req<Created>("POST", "/work-items", body);
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
  abandon = (id: string) => this.req("POST", this.item(id, "/abandon"), {});

  check = (file: string, text: string) => this.req<{ issues: ConfigIssue[] }>("POST", "/templates/check", { file, text });
  lint = () => this.req<LintReport>("GET", "/templates/lint");
  resolved = (id: string) => this.req<unknown>("GET", `/templates/chains/${encodeURIComponent(id)}/resolved`);
  parse = (text: string) => this.req<{ chain: unknown; error: string | null }>("POST", "/templates/parse", { text });
  resolve = (chain: unknown) => this.req<{ chains: unknown[]; issues: ConfigIssue[] }>("POST", "/templates/resolve", { chain });
  reload = () => this.req<ReloadReport>("POST", "/templates/reload");
  pending = () => this.req<ApplyReport>("GET", "/apply");
  restart = () => this.req<{ restarting: boolean }>("POST", "/apply/restart");
}
