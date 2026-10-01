import type { Problem, Result } from "../draft/types";

/** One `repos` draft entry as `result.resolved.repos[]`/`detected[]` answers it (docsite "Repos ops"). */
export interface RepoView {
  path: string;
  name: string | null;
  managed: boolean;
  /** The entry as `repos.yaml` holds it, its own keys only. */
  entry: Record<string, unknown>;
  resolved: {
    steering: string[];
    deny_tools: string[];
    models: Record<string, string>;
    policy: Record<string, unknown>;
  };
  sources: {
    steering: "repo" | "library";
    deny_tools: "repo" | "default";
    models: "repo" | "default";
    policy: Record<string, "repo" | "default">;
  };
}

export interface ReposResolved {
  repos: RepoView[];
  detected: RepoView[];
}

/** `result.resolved` of a `repos` draft; null while it does not load. */
export const reposOf = (r: Result): ReposResolved | null => {
  const v = r.resolved as unknown as Partial<ReposResolved> | null;
  return v && Array.isArray(v.repos) ? { repos: v.repos, detected: v.detected ?? [] } : null;
};

/** Items not ended, per repo path (`impact.running` of a `repos` draft). */
export const runningOf = (r: Result): Record<string, number> => {
  const v = (r.impact as { running?: unknown } | null)?.running;
  return v && typeof v === "object" ? (v as Record<string, number>) : {};
};

export const repoName = (r: Pick<RepoView, "name" | "path">) => r.name || r.path.split("/").filter(Boolean).pop() || r.path;

/** The problems about one repo; `field` is the key it is about, or null. */
export const problemsOf = (r: Result, path: string): Problem[] => r.problems.filter((p) => p.repo === path);
