export type NamedRepo = { path: string; name?: string | null };

export const repoLabel = (path: string, repos: NamedRepo[]) => repos.find((r) => r.path === path)?.name || path.split("/").filter(Boolean).pop() || path || "no repo";

/** A repo as a picker lists it: a path Kraft no longer knows (disconnected since, or mistyped in the file) says so, once the connected ones are loaded. */
export const repoChoice = (path: string, repos: NamedRepo[]) => (repos.length && !repos.some((r) => r.path === path) ? `${repoLabel(path, repos)} (not connected)` : repoLabel(path, repos));
