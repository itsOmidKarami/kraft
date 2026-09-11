import { useEffect, useMemo, useState } from "react";
import * as api from "../../../api";
import type { DiffFile, WorkItemDiff } from "../../../types";

/**
 * Right pane · Changes (UI v2 · 05, 13): `DiffModal`'s file-chunking and
 * truncation logic, without the dialog framing. `selectedFile` (set by
 * `Inspector/Changes.tsx`) opens and scrolls to that file's `<details>`.
 */

const lineClass = (line: string) =>
  line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@") ? "diff-hunk"
  : line.startsWith("+") ? "diff-add"
  : line.startsWith("-") ? "diff-del"
  : "diff-ctx";

const MAX_FILE_LINES = 300;
const MAX_OPEN_LINES = 600;

type Row = {
  key: string;
  path: string;
  ins: number | null;
  del: number | null;
  body: string[] | null;
  note: string | null;
  open: boolean;
};

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

function rowsOf(
  diff: string,
  files: DiffFile[],
  untracked: string[],
  truncated: boolean,
  allClosed: boolean,
  openPath: string | null,
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

  if (truncated) {
    for (const f of files) {
      if (seen.has(f.path)) continue;
      out.push({ key: `cut:${f.path}`, path: f.path, ins: f.insertions, del: f.deletions, body: null, note: "not shown — diff truncated" });
    }
  }
  for (const path of untracked) {
    out.push({ key: `new:${path}`, path, ins: null, del: null, body: null, note: "new file — content not shown" });
  }

  let opened = 0;
  let full = false;
  return out.map((r) => {
    if (r.path === openPath) return { ...r, open: true };
    const n = r.body?.length ?? 0;
    const open =
      !allClosed && !full && r.body != null && n <= MAX_FILE_LINES && opened + n <= MAX_OPEN_LINES;
    if (open) opened += n;
    else if (r.body != null) full = true;
    return { ...r, open };
  });
}

export function Diff({
  workItemId,
  selectedFile,
}: {
  workItemId: string;
  selectedFile?: string | null;
}) {
  const [diff, setDiff] = useState<WorkItemDiff | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getWorkItemDiff(workItemId)
      .then((d) => alive && setDiff(d))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [workItemId]);

  useEffect(() => {
    if (!selectedFile) return;
    document
      .querySelector(`[data-diff-file="${CSS.escape(selectedFile)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedFile, diff]);

  const totals = diff?.files.reduce(
    (a, f) => ({ ins: a.ins + f.insertions, del: a.del + f.deletions }),
    { ins: 0, del: 0 },
  );

  const sections = useMemo(() => {
    if (!diff) return [];
    const out = [
      {
        key: "in-flight",
        label: null as string | null,
        rows: rowsOf(diff.diff, diff.files, diff.untracked, diff.truncated, false, selectedFile ?? null),
      },
    ];
    const l = diff.landed;
    if (l && (l.diff || l.files.length > 0)) {
      out.push({
        key: "landed",
        label: `${l.commits.length} commit${l.commits.length === 1 ? "" : "s"} already on this branch`,
        rows: rowsOf(l.diff, l.files, [], l.truncated, true, selectedFile ?? null),
      });
    }
    return out;
  }, [diff, selectedFile]);

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
      </header>

      {err && <p className="form-error">{err}</p>}

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
                  <li key={r.key} data-diff-file={r.path}>
                    {r.body ? (
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
          Diff truncated — the file list above is complete. Open the worktree for the full change.
        </p>
      )}
    </div>
  );
}
