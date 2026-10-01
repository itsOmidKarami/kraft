/** `/compare`'s unified diff, split per file and per hunk (W8 B.1). Pure. */

export type LineKind = " " | "+" | "-";

export interface PatchLine {
  kind: LineKind;
  /** The line's number on the old side; null for an added line. */
  old: number | null;
  /** The line's number on the new side; null for a removed line. */
  new: number | null;
  text: string;
}

export interface Hunk {
  header: string;
  oldStart: number;
  newStart: number;
  lines: PatchLine[];
}

export interface PatchFile {
  /** The new path, as `/compare` reports the file. */
  path: string;
  /** The path before a rename; null otherwise. */
  oldPath: string | null;
  status: "modified" | "added" | "deleted" | "renamed";
  binary: boolean;
  /** Either side ends without a newline. */
  noNewline: boolean;
  hunks: Hunk[];
  /** This file's own patch text, from its `diff --git` line (Copy diff). */
  text: string;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;
// `diff --git a/x b/x`: the b-side, for a file whose other headers say nothing (a mode change).
const GIT = /^diff --git a\/(.*) b\/(.*)$/;
const unquote = (p: string) => (p.startsWith('"') && p.endsWith('"') ? p.slice(1, -1) : p);
const side = (line: string, prefix: string) => {
  const p = unquote(line.slice(4).replace(/\t.*$/, ""));
  return p === "/dev/null" ? null : p.startsWith(prefix) ? p.slice(prefix.length) : p;
};

export function parsePatch(diff: string): PatchFile[] {
  const files: PatchFile[] = [];
  const chunks = diff.split(/^(?=diff --git )/m).filter((c) => c.startsWith("diff --git "));
  for (const chunk of chunks) {
    const raw = chunk.endsWith("\n") ? chunk.slice(0, -1) : chunk;
    const rows = raw.split("\n");
    const git = GIT.exec(rows[0]);
    let oldPath: string | null = git ? git[1] : null;
    let newPath: string | null = git ? git[2] : null;
    let status: PatchFile["status"] = "modified";
    let binary = false;
    let noNewline = false;
    const hunks: Hunk[] = [];
    let hunk: Hunk | null = null;
    let o = 0;
    let n = 0;
    for (const row of rows.slice(1)) {
      if (hunk) {
        const c = row[0];
        if (c === "+") hunk.lines.push({ kind: "+", old: null, new: n++, text: row.slice(1) });
        else if (c === "-") hunk.lines.push({ kind: "-", old: o++, new: null, text: row.slice(1) });
        else if (c === " " || row === "") hunk.lines.push({ kind: " ", old: o++, new: n++, text: row.slice(1) });
        else if (c === "\\") noNewline = true;
        else if (HUNK.test(row)) hunk = null;
        if (hunk) continue;
      }
      const h = HUNK.exec(row);
      if (h) {
        o = +h[1];
        n = +h[2];
        hunk = { header: row, oldStart: o, newStart: n, lines: [] };
        hunks.push(hunk);
      } else if (row.startsWith("new file mode")) status = "added";
      else if (row.startsWith("deleted file mode")) status = "deleted";
      else if (row.startsWith("rename from ")) { oldPath = row.slice(12); status = "renamed"; }
      else if (row.startsWith("rename to ")) newPath = row.slice(10);
      else if (row.startsWith("Binary files ")) binary = true;
      else if (row.startsWith("--- ")) oldPath = side(row, "a/") ?? oldPath;
      else if (row.startsWith("+++ ")) newPath = side(row, "b/") ?? newPath;
    }
    const path = status === "deleted" ? oldPath! : newPath!;
    files.push({ path, oldPath: status === "renamed" ? oldPath : null, status, binary, noNewline, hunks, text: chunk });
  }
  return files;
}
