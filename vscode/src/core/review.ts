import type { WorkItemDiff } from "./api";

export function reviewFiles(diff: WorkItemDiff): string[] {
  const all = new Set<string>([
    ...(diff.landed?.files ?? []).map((f) => f.path),
    ...(diff.files ?? []).map((f) => f.path),
    ...(diff.untracked ?? []),
  ]);
  return [...all].sort();
}

// Segments are percent-encoded so `#`, `?` and `%` in a filename survive Uri.parse.
const encodePath = (path: string) => path.split("/").map(encodeURIComponent).join("/");
export const leftUri = (id: string, path: string, ref: string) => `kraft-git:/${id}/${encodePath(path)}?${encodeURIComponent(ref)}`;
export const rightUri = (id: string, path: string) => `kraft-wt:/${id}/${encodePath(path)}`;

export function parseReviewUri(path: string): { id: string; file: string } {
  const [, id, ...rest] = path.split("/");
  return { id, file: rest.join("/") };
}

type UriLike = { scheme: string; path: string };

// The item a command acts on: an id, a board node, or a kraft-* document. From an editor's
// title bar VS Code passes the tab's Uri; with no argument, fall back to the active editor.
export function commandItemId(arg: unknown, active: UriLike | undefined, schemes: readonly string[]): string | undefined {
  if (typeof arg === "string") return arg;
  const node = (arg as { item?: { id?: string } } | undefined)?.item?.id;
  if (node) return node;
  const uri = typeof (arg as UriLike | undefined)?.scheme === "string" ? (arg as UriLike) : active;
  return uri && schemes.includes(uri.scheme) ? parseReviewUri(uri.path).id || undefined : undefined;
}
