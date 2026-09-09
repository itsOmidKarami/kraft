import { useEffect, useMemo, useState } from "react";
import { X } from "@phosphor-icons/react";
import * as api from "../api";
import type { DiffFile, WorkItemDiff } from "../types";
import { backdropProps, useModal } from "../useModal";

/**
 * The changes an agent made, read-only, over the work item. Sibling to
 * DocumentModal rather than a mode of it: a diff shares none of that
 * component's markdown rendering or editor-launch menu.
 *
 * Not a route, for DocumentModal's reason: closing must put the reader back
 * where they were, not in history.
 */

/**
 * `---`/`+++` are file headers (one pair per changed file, ahead of every
 * hunk in real `git diff` output), not added/removed content — they must be
 * checked before the plain `+`/`-` tests or every file header miscolours as
 * a content line.
 */
const lineClass = (line: string) =>
  line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@") ? "diff-hunk"
  : line.startsWith("+") ? "diff-add"
  : line.startsWith("-") ? "diff-del"
  : "diff-ctx";

/** A single file bigger than this arrives collapsed however small the diff is. */
const MAX_FILE_LINES = 300;
/** Total body lines opened on arrival, walked over the rows in order. */
const MAX_OPEN_LINES = 600;

/** One `<details>` row, or — when `body` is null — a body-less list row. */
type Row = {
  key: string;
  path: string;
  ins: number | null;
  del: number | null;
  body: string[] | null;
  note: string | null;
  open: boolean;
};

/**
 * The path a `diff --git` chunk is about.
 *
 * Not read from the `diff --git a/x b/x` line: it names the path twice with no
 * separator that survives a path containing a space. `rename to` comes first
 * because a rename's `---`/`+++` pair names both ends; `+++ b/` before
 * `--- a/` because a deletion's `+++` is `/dev/null` and matches neither. A
 * chunk with none of the three (a pure mode change) is labelled by its header.
 */
function chunkPath(chunk: string): string {
  const lines = chunk.split("\n");
  for (const pattern of [
    /^rename to (.+)$/,
    /^\+\+\+ b\/(.+)$/,
    /^--- a\/(.+)$/,
    // a chunk with neither pair (no `---`/`+++` at all) still names its path
    // on the `diff --git a/x b/x` line itself.
    /^diff --git a\/(.+) b\/.+$/,
  ]) {
    for (const line of lines) {
      const m = line.match(pattern);
      if (m) return m[1];
    }
  }
  return lines[0];
}

/** `+n −n` for a chunk `--numstat` does not name (a rename, under git's default detection). */
const countOf = (body: string[]) => ({
  ins: body.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length,
  del: body.filter((l) => l.startsWith("-") && !l.startsWith("---")).length,
});

/**
 * One list of "what changed": a section per diff chunk, then the files
 * truncation cut, then the untracked ones. The backend only ever cuts on a
 * `diff --git` boundary (`_truncate_at_file_boundary`, src/kraft/api.py), so
 * splitting on a lookahead leaves every chunk headed.
 *
 * `allClosed` is the landed section: work earlier nodes already committed is
 * context, not the change under review, and must not spend the open-line
 * budget the in-flight side needs (Kraft-nceo).
 */
function rowsOf(
  diff: string,
  files: DiffFile[],
  untracked: string[],
  truncated: boolean,
  allClosed: boolean,
): Row[] {
  const chunks = diff ? diff.split(/\n(?=diff --git )/) : [];
  const byPath = new Map(files.map((f) => [f.path, f]));
  const seen = new Set<string>();
  const out: Omit<Row, "open">[] = [];

  for (const chunk of chunks) {
    const path = chunkPath(chunk);
    const lines = chunk.split("\n");
    const stat = byPath.get(path);
    const derived = countOf(lines);
    seen.add(path);
    out.push({
      key: `chunk:${path}`,
      path,
      ins: stat ? stat.insertions : derived.ins,
      del: stat ? stat.deletions : derived.del,
      body: lines,
      note: null,
    });
  }

  // Only when the body was actually cut: a rename reports `old => new` in
  // --numstat and `new` in the chunk, so an unconditional rule would print a
  // spurious "truncated" row for every renamed file.
  if (truncated) {
    for (const f of files) {
      if (seen.has(f.path)) continue;
      out.push({
        key: `cut:${f.path}`,
        path: f.path,
        ins: f.insertions,
        del: f.deletions,
        body: null,
        note: "not shown — diff truncated",
      });
    }
  }

  // No counts: --numstat never sees an untracked file.
  for (const path of untracked) {
    out.push({
      key: `new:${path}`,
      path,
      ins: null,
      del: null,
      body: null,
      note: "new file — content not shown",
    });
  }

  let opened = 0;
  // A file that doesn't fit closes everything after it too, not just
  // itself — a "the rest collapsed" cutoff rather than an independent
  // per-file coin flip a later small file could still win.
  let full = false;
  return out.map((r) => {
    const n = r.body?.length ?? 0;
    const open =
      !allClosed && !full && r.body != null && n <= MAX_FILE_LINES && opened + n <= MAX_OPEN_LINES;
    if (open) opened += n;
    else if (r.body != null) full = true;
    return { ...r, open };
  });
}

export function DiffModal({
  workItemId,
  onClose,
}: {
  workItemId: string;
  onClose: () => void;
}) {
  const ref = useModal<HTMLDivElement>(onClose);
  const [diff, setDiff] = useState<WorkItemDiff | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .getWorkItemDiff(workItemId)
      .then(setDiff)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [workItemId]);

  const totals = diff?.files.reduce(
    (a, f) => ({ ins: a.ins + f.insertions, del: a.del + f.deletions }),
    { ins: 0, del: 0 },
  );

  /** In-flight first and expanded, landed second and collapsed. */
  const sections = useMemo(() => {
    if (!diff) return [];
    const out = [
      {
        key: "in-flight",
        label: null as string | null,
        rows: rowsOf(diff.diff, diff.files, diff.untracked, diff.truncated, false),
      },
    ];
    const l = diff.landed;
    if (l && (l.diff || l.files.length > 0)) {
      out.push({
        key: "landed",
        label: `${l.commits.length} commit${l.commits.length === 1 ? "" : "s"} already on this branch`,
        rows: rowsOf(l.diff, l.files, [], l.truncated, true),
      });
    }
    return out;
  }, [diff]);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="changes" {...backdropProps(onClose)}>
      <div className="dialog diff-modal" ref={ref}>
        <header className="diff-modal-head">
          <span className="mono">{diff?.base_ref?.slice(0, 10) ?? "—"}</span>
          {totals && diff && (
            <span className="diff-totals">
              <span>{diff.files.length} files ·</span>{" "}
              <span className="diff-add">+{totals.ins}</span>{" "}
              <span className="diff-del">−{totals.del}</span>
            </span>
          )}
          {diff?.landed && diff.landed.files.length > 0 && (
            <span className="diff-landed-totals">landed {diff.landed.files.length} files</span>
          )}
          <button className="btn btn-ghost" onClick={onClose} aria-label="close">
            <X size={14} />
          </button>
        </header>

        {err && <p className="form-error">{err}</p>}

        {/* Two different empty states: no base_ref was ever pinned (nothing to
            diff against) versus a pinned base the worktree matches (a real,
            answerable "nothing changed yet"). A worktree with committed work
            and a clean tree is neither: it has a landed section to show. */}
        {diff && diff.diff === "" && diff.untracked.length === 0 && !diff.landed?.diff && (
          <p className="empty">
            {diff.base_ref
              ? "No changes yet — the worktree matches the base commit."
              : "No diff available for this work item yet."}
          </p>
        )}

        {sections.map(
          (s) =>
            s.rows.length > 0 && (
              <div key={s.key} className="diff-section">
                {s.label && <p className="section-label">{s.label}</p>}
                <ul className="diff-files" data-section={s.key}>
                  {s.rows.map((r) => (
                    <li key={r.key}>
                      {r.body ? (
                        /* `<details>` is the whole interaction: no state, no
                           toggle handler, keyboard-accessible for free. `open`
                           is a plain attribute, and `diff` is fetched once, so
                           React never fights a reader's own toggling. */
                        <details open={r.open}>
                          <summary>
                            <span className="diff-file-head">
                              <span className="mono">{r.path}</span>
                              <span className="diff-add">+{r.ins}</span>
                              <span className="diff-del">−{r.del}</span>
                            </span>
                          </summary>
                          <pre className="diff-body">
                            {r.body.map((line, i) => (
                              <div key={i} className={lineClass(line)}>
                                {line}
                              </div>
                            ))}
                          </pre>
                        </details>
                      ) : (
                        <span className="diff-file-head">
                          <span className="mono">{r.path}</span>
                          {r.ins != null && <span className="diff-add">+{r.ins}</span>}
                          {r.del != null && <span className="diff-del">−{r.del}</span>}
                          <span className="field-hint">{r.note}</span>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ),
        )}

        {diff?.truncated && (
          <p className="field-hint">
            Diff truncated — the file list above is complete. Open the worktree for the
            full change.
          </p>
        )}
      </div>
    </div>
  );
}
