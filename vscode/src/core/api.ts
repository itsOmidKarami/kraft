import type { Finding, WorkItem } from "../../../frontend/src/types";
import type { WorkItemDiff } from "../../../frontend/src/types/documents";

export type { Finding, WorkItem, WorkItemDiff };

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
export interface ReloadReport { valid: string[]; invalid_templates: Record<string, string>; refused_policy: string | null }

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
  listItems = () => this.req<{ items: WorkItem[]; cursor: number }>("GET", "/work-items");
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

  check = (file: string, text: string) => this.req<{ issues: ConfigIssue[] }>("POST", "/templates/check", { file, text });
  lint = () => this.req<LintReport>("GET", "/templates/lint");
  resolved = (id: string) => this.req<unknown>("GET", `/templates/chains/${encodeURIComponent(id)}/resolved`);
  parse = (text: string) => this.req<{ chain: unknown; error: string | null }>("POST", "/templates/parse", { text });
  resolve = (chain: unknown) => this.req<{ chains: unknown[]; issues: ConfigIssue[] }>("POST", "/templates/resolve", { chain });
  reload = () => this.req<ReloadReport>("POST", "/templates/reload");
}
