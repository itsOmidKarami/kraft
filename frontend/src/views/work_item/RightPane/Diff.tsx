import { useMemo } from "react";
import { ArrowsOutSimple } from "@phosphor-icons/react";
import type { DiffFile, WorkItemDiff } from "../../../types";
import { fileSection, filePath } from "../selection";

/**
 * Right pane · Changes (UI v2 · 05, 13 · UI v3 spec 2026-09-12 §1, supersedes
 * screens 42/48): shows exactly the file the tree selected, and nothing
 * else — no scrollable list of every file, no scroll-driven tree highlight.
 * `selectedFile` (set by `Inspector/Changes.tsx`) is a `selection.ts` file
 * key: a bare path for the in-flight diff, `landed:`-prefixed for a path
 * that already landed. The diff is fetched once, in `index.tsx`, and passed
 * down here and to the tree — this pane used to fetch its own copy.
 */

const lineClass = (line: string) =>
  line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@") ? "diff-hunk"
  : line.startsWith("+") ? "diff-add"
  : line.startsWith("-") ? "diff-del"
  : "diff-ctx";

/** `git diff --numstat` renders a rename as `common/{old => new}/tail` (or
 *  bare `old => new` with no common prefix/suffix); returns the path the
 *  diff body actually uses (`rename to` / `+++ b/`), so a renamed file's
 *  tree key can be matched against its chunk. */
function renderedPath(path: string): string {
  const braced = path.match(/^(.*)\{.* => (.*)\}(.*)$/);
  if (braced) return `${braced[1]}${braced[2]}${braced[3]}`;
  const bare = path.match(/^.* => (.*)$/);
  if (bare) return bare[1];
  return path;
}

function chunkPath(chunk: string): string {
  const lines = chunk.split("\n");
  for (const pattern of [
    /^rename to (.+)$/,
    /^\+\+\+ b\/(.+)$/,
    /^--- a\/(.+)$/,
    /^diff --git a\/(.+) b\/.+$/,
  ]) {
    for (const line of lines) {
      const m = line.match(pattern);
      if (m) return m[1];
    }
  }
  return lines[0];
}

const countOf = (body: string[]) => ({
  ins: body.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length,
  del: body.filter((l) => l.startsWith("-") && !l.startsWith("---")).length,
});

type FileEntry = {
  path: string;
  ins: number | null;
  del: number | null;
  body: string[] | null;
  note: string | null;
};

/** Finds one file's own chunk in a unified diff, falling back to the
 *  truncated-file and untracked-file notes `Inspector/Changes.tsx`'s tree
 *  also renders a row for. `null` only when `targetPath` names nothing in
 *  this diff — a stale selection left over from before the diff refetched. */
function findFile(
  diffText: string,
  files: DiffFile[],
  untracked: string[],
  truncated: boolean,
  targetPath: string,
): FileEntry | null {
  const target = renderedPath(targetPath);
  const chunks = diffText ? diffText.split(/\n(?=diff --git )/) : [];
  for (const chunk of chunks) {
    const path = chunkPath(chunk);
    if (path !== target) continue;
    const lines = chunk.split("\n");
    const stat = files.find((f) => renderedPath(f.path) === path);
    const derived = countOf(lines);
    return {
      path,
      ins: stat ? stat.insertions : derived.ins,
      del: stat ? stat.deletions : derived.del,
      body: lines,
      note: null,
    };
  }
  if (truncated) {
    const f = files.find((f) => f.path === targetPath);
    if (f) return { path: f.path, ins: f.insertions, del: f.deletions, body: null, note: "not shown — diff truncated" };
  }
  if (untracked.includes(targetPath)) {
    return { path: targetPath, ins: null, del: null, body: null, note: "new file — content not shown" };
  }
  return null;
}

export function Diff({
  diff,
  diffError,
  selectedFile,
  maximized,
  onToggleMaximize,
}: {
  diff: WorkItemDiff | null;
  diffError: string | null;
  selectedFile?: string | null;
  maximized?: boolean;
  onToggleMaximize?: () => void;
}) {
  const file = useMemo(() => {
    if (!diff || !selectedFile) return null;
    const path = filePath(selectedFile);
    if (fileSection(selectedFile) === "landed") {
      const l = diff.landed;
      return l ? findFile(l.diff, l.files, [], l.truncated, path) : null;
    }
    return findFile(diff.diff, diff.files, diff.untracked, diff.truncated, path);
  }, [diff, selectedFile]);

  const totals = diff?.files.reduce(
    (a, f) => ({ ins: a.ins + f.insertions, del: a.del + f.deletions }),
    { ins: 0, del: 0 },
  );

  const hasContent = !!diff && (diff.diff !== "" || diff.untracked.length > 0 || !!diff.landed?.diff);

  return (
    <div className="pane diff-pane" data-testid="right-pane-diff">
      <header className="diff-modal-head">
        <span className="mono">{diff?.base_ref?.slice(0, 10) ?? "—"}</span>
        {totals && diff && (
          <span className="diff-totals">
            <span>{diff.files.length} files ·</span> <span className="diff-add">+{totals.ins}</span>{" "}
            <span className="diff-del">−{totals.del}</span>
          </span>
        )}
        {diff?.landed && diff.landed.files.length > 0 && (
          <span className="diff-landed-totals">landed {diff.landed.files.length} files</span>
        )}
        {/* ponytail: unified/split chips and "Open in editor" (spec §4's pane
         *  header) are skipped — Copy is the one action every diff needs;
         *  add the rest if a real review session asks for a split view. */}
        {diff?.diff && (
          <button
            className="btn btn-ghost btn-icon"
            title="Copy diff"
            onClick={() => navigator.clipboard.writeText(diff.diff)}
          >
            Copy
          </button>
        )}
        {onToggleMaximize && (
          <button
            className="btn btn-icon btn-ghost"
            title={maximized ? "Collapse" : "Maximize"}
            aria-pressed={maximized}
            onClick={onToggleMaximize}
          >
            <ArrowsOutSimple size={14} />
          </button>
        )}
      </header>

      {diffError && <p className="form-error">{diffError}</p>}

      {diff && !hasContent && (
        <p className="empty">
          {diff.base_ref
            ? "No changes yet — the worktree matches the base commit."
            : "No diff available for this work item yet."}
        </p>
      )}

      {hasContent && !selectedFile && <p className="empty">select a file in the tree to see its diff</p>}

      {hasContent && selectedFile && !file && (
        <p className="empty">this file is not in the current diff</p>
      )}

      {file && (
        <div className="diff-section">
          <ul className="diff-files">
            <li data-diff-file={file.path}>
              {file.body ? (
                <>
                  <span className="diff-file-head" data-file-header data-file-path={file.path}>
                    <span className="mono">{file.path}</span>
                    <span className="diff-add">+{file.ins}</span>
                    <span className="diff-del">−{file.del}</span>
                  </span>
                  <pre className="diff-body">
                    {file.body.map((line, i) => (
                      <div key={i} className={lineClass(line)}>
                        {line}
                      </div>
                    ))}
                  </pre>
                </>
              ) : (
                <span className="diff-file-head" data-file-header data-file-path={file.path}>
                  <span className="mono">{file.path}</span>
                  {file.ins != null && <span className="diff-add">+{file.ins}</span>}
                  {file.del != null && <span className="diff-del">−{file.del}</span>}
                  <span className="field-hint">{file.note}</span>
                </span>
              )}
            </li>
          </ul>
        </div>
      )}

      {diff?.truncated && (
        <p className="field-hint">
          Diff truncated — the file list above is complete. Open the worktree for the full change.
        </p>
      )}
    </div>
  );
}
