import type { WorkItemDiff } from "./api";

export function reviewFiles(diff: WorkItemDiff): string[] {
  const all = new Set<string>([
    ...(diff.landed?.files ?? []).map((f) => f.path),
    ...(diff.files ?? []).map((f) => f.path),
    ...(diff.untracked ?? []),
  ]);
  return [...all].sort();
}

export const leftUri = (id: string, path: string, ref: string) => `kraft-git:/${id}/${path}?${encodeURIComponent(ref)}`;
export const rightUri = (id: string, path: string) => `kraft-wt:/${id}/${path}`;

export function parseReviewUri(path: string): { id: string; file: string } {
  const [, id, ...rest] = path.split("/");
  return { id, file: rest.join("/") };
}
