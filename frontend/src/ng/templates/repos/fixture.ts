import type { DraftView, Problem, Result } from "../draft/types";
import type { RepoView } from "./types";

export const repo = (name: string, over: Partial<RepoView["entry"]> = {}, extra: Partial<RepoView> = {}): RepoView => ({
  path: `/src/${name}`,
  name,
  managed: true,
  entry: { path: `/src/${name}`, name, default_chain_template: "default", test_command: "make test", enabled: true, ...over },
  resolved: { steering: ["project-standards"], deny_tools: [], models: {}, policy: {} },
  sources: { steering: "library", deny_tools: "default", models: "default", policy: { time_cap_minutes: "default" } },
  ...extra,
});

export const REPOS = [repo("product_root"), repo("platform", { policy: { time_cap_minutes: 60 } }, { sources: { steering: "repo", deny_tools: "default", models: "default", policy: { time_cap_minutes: "repo" } } }), repo("docs-site", { enabled: false, default_chain_template: "docs_only" })];
export const DETECTED = [repo("plugins", {}, { managed: false })];

export function reposView(over: Partial<Result> = {}, draft = false): DraftView {
  return {
    area: "repos",
    key: "repos",
    draft,
    files: { "repos.yaml": "repos: []\n" },
    base: { "repos.yaml": "x" },
    published: { "repos.yaml": "repos: []\n" },
    updated_at: null,
    result: {
      model: {}, problems: [], sources: {}, changes: [], warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
      impact: { running: { "/src/platform": 1 } } as never,
      resolved: { repos: REPOS, detected: DETECTED } as never,
      ...over,
    },
  };
}

export const problemAt = (path: string, field: string | null, message: string): Problem => ({ path: field ?? "", field, message, file: "repos.yaml", line: 1, col: 1, repo: path });
