import { useEffect, useMemo, useRef } from "react";
import { ArrowsOutSimple } from "@phosphor-icons/react";
import type { DiffFile, WorkItemDiff } from "../../../types";
import { fileKey, filePath, fileSection } from "../selection";

/**
 * Right pane · Changes (UI v2 · 05, 13): `DiffModal`'s file-chunking and
 * truncation logic, without the dialog framing. `selectedFile` (set by
 * `Inspector/Changes.tsx`) opens and scrolls to that file's `<details>`. The
 * diff is fetched once, in `index.tsx`, and passed down here and to the
 * tree — this pane used to fetch its own copy.
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
  diff,
  diffError,
  selectedFile,
  onVisibleFile,
  maximized,
  onToggleMaximize,
}: {
  diff: WorkItemDiff | null;
  diffError: string | null;
  selectedFile?: string | null;
  /** Two-way sync (G4-05): the pane's own scroll moves the tree's
   *  highlight. Called with a section-qualified key (`selection.ts`'s
   *  `fileKey`), not a bare path. Omitted on the phone page, which has no
   *  tree beside it. */
  onVisibleFile?: (key: string) => void;
  maximized?: boolean;
  onToggleMaximize?: () => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  // Scrolling the pane moves the tree highlight (48). The guard stops the
  // loop: a selection-driven scrollIntoView must not re-drive the selection.
  const scrolling = useRef(false);
  // The other direction: a selection caused by the observer (user scrolled
  // past a file boundary) must not then scrollIntoView and snap the pane.
  const observerSelected = useRef<string | null>(null);

  useEffect(() => {
    if (!selectedFile) return;
    if (observerSelected.current === selectedFile) {
      observerSelected.current = null;
      return;
    }
    scrolling.current = true;
    // Scoped to the selection's own section: the same path can appear both
    // in-flight and landed, and an unscoped query always found the first.
    rootRef.current
      ?.querySelector(
        `[data-section="${fileSection(selectedFile)}"] [data-file-header][data-file-path="${CSS.escape(filePath(selectedFile))}"]`,
      )
      ?.scrollIntoView({ block: "start" });
    const t = setTimeout(() => {
      scrolling.current = false;
    }, 400);
    return () => clearTimeout(t);
  }, [selectedFile, diff]);

  // `onVisibleFile` is a fresh function every render (it closes over
  // `select`, which `useItemUrlState` recreates each render). Reading it
  // through a ref keeps the observer effect's deps to [diff], so a re-render
  // doesn't tear down and re-observe mid-scroll and drop the click that
  // triggered it.
  const onVisibleFileRef = useRef(onVisibleFile);
  onVisibleFileRef.current = onVisibleFile;

  useEffect(() => {
    if (!onVisibleFileRef.current) return;
    const root = rootRef.current?.closest<HTMLElement>(".item-right-pane") ?? null;
    const io = new IntersectionObserver(
      (entries) => {
        if (scrolling.current) return;
        const top = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) {
          const el = top.target as HTMLElement;
          const section = el.closest<HTMLElement>("[data-section]")?.dataset.section;
          const key = fileKey(section === "landed" ? "landed" : "in-flight", el.dataset.filePath!);
          observerSelected.current = key;
          onVisibleFileRef.current?.(key);
        }
      },
      { root, rootMargin: "0px 0px -80% 0px" },
    );
    rootRef.current?.querySelectorAll("[data-file-header]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [diff]);

  const totals = diff?.files.reduce(
    (a, f) => ({ ins: a.ins + f.insertions, del: a.del + f.deletions }),
    { ins: 0, del: 0 },
  );

  const sections = useMemo(() => {
    if (!diff) return [];
    // `selectedFile` carries its section (see `selection.ts`); only the
    // section that owns the selection force-opens a row for it.
    const openIn = (section: "in-flight" | "landed") =>
      selectedFile && fileSection(selectedFile) === section ? filePath(selectedFile) : null;
    const out = [
      {
        key: "in-flight",
        label: null as string | null,
        rows: rowsOf(diff.diff, diff.files, diff.untracked, diff.truncated, false, openIn("in-flight")),
      },
    ];
    const l = diff.landed;
    if (l && (l.diff || l.files.length > 0)) {
      out.push({
        key: "landed",
        label: `${l.commits.length} commit${l.commits.length === 1 ? "" : "s"} already on this branch`,
        rows: rowsOf(l.diff, l.files, [], l.truncated, true, openIn("landed")),
      });
    }
    return out;
  }, [diff, selectedFile]);

  return (
    <div className="pane diff-pane" data-testid="right-pane-diff" ref={rootRef}>
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
                          <span className="diff-file-head" data-file-header data-file-path={r.path}>
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
                      <span className="diff-file-head" data-file-header data-file-path={r.path}>
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
