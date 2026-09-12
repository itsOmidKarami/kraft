import type { DiffFile } from "../../../types";

export interface TreeNode {
  name: string;
  path: string; // full path for a file, prefix for a folder
  kind: "folder" | "file";
  children: TreeNode[]; // empty for a file
  fileCount: number; // folders only
  insertions?: number; // files only
  deletions?: number; // files only
}

interface Bucket {
  path: string;
  folders: Map<string, Bucket>;
  files: TreeNode[];
}

function bucketOf(path: string): Bucket {
  return { path, folders: new Map(), files: [] };
}

/** Sorts a bucket into `TreeNode[]`, folders before files, each
 *  alphabetically, summing `fileCount` on the way back up. */
function toNodes(bucket: Bucket): TreeNode[] {
  const folders: TreeNode[] = [...bucket.folders.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([name, child]) => {
      const children = toNodes(child);
      return {
        name,
        path: child.path,
        kind: "folder" as const,
        children,
        fileCount: children.reduce((n, c) => n + (c.kind === "file" ? 1 : c.fileCount), 0),
      };
    });
  const files = [...bucket.files].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return [...folders, ...files];
}

/**
 * Groups flat diff paths into a folder tree (UI v3 · 42, G4-01): split each
 * path on `/`, walk a map of folders keyed by their own full prefix, and
 * attach files at the leaf.
 */
export function buildFileTree(files: DiffFile[]): TreeNode[] {
  const root = bucketOf("");
  for (const f of files) {
    const parts = f.path.split("/");
    const fileName = parts.pop()!;
    let bucket = root;
    let prefix = "";
    for (const part of parts) {
      prefix = prefix ? `${prefix}/${part}` : part;
      let next = bucket.folders.get(part);
      if (!next) {
        next = bucketOf(prefix);
        bucket.folders.set(part, next);
      }
      bucket = next;
    }
    bucket.files.push({
      name: fileName,
      path: f.path,
      kind: "file",
      children: [],
      fileCount: 0,
      insertions: f.insertions,
      deletions: f.deletions,
    });
  }
  return toNodes(root);
}
