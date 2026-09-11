import { useEffect, useState } from "react";
import * as api from "../../../api";
import type { WorkItemDiff } from "../../../types";

/**
 * Inspector · Changes (UI v2 · 05, 13): the file list, `+142 −38` per file.
 * Selecting a row scrolls `RightPane/Diff.tsx` to that file.
 *
 * ponytail: a flat list, not folders-as-headers — the notes' directory tree
 * grouping is skipped; add it if a real item's file count makes a flat list
 * hard to scan. Fetches the diff a second time (`RightPane/Diff.tsx` fetches
 * its own copy) rather than lifting the fetch up — each pane already owns
 * its own data in this file, and a diff is small enough that the duplicate
 * request is not worth threading state through the tab boundary for.
 */
export function Changes({
  workItemId,
  selected,
  onSelect,
}: {
  workItemId: string;
  selected: string | null;
  onSelect: (path: string) => void;
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

  const files = [...(diff?.files ?? [])].sort((a, b) => a.path.localeCompare(b.path));

  return (
    <div className="inspector-list" data-testid="inspector-changes">
      {err && <p className="form-error">{err}</p>}
      {diff && files.length === 0 && <p className="empty">no changes yet</p>}
      {files.map((f) => (
        <button
          key={f.path}
          className="doc-row change-row"
          data-selected={f.path === selected}
          onClick={() => onSelect(f.path)}
        >
          <span className="doc-text">
            <span className="mono doc-title">{f.path}</span>
          </span>
          <span className="diff-add">+{f.insertions}</span>
          <span className="diff-del">−{f.deletions}</span>
        </button>
      ))}
      {diff?.untracked.map((path) => (
        <button key={path} className="doc-row change-row" data-selected={path === selected} onClick={() => onSelect(path)}>
          <span className="doc-text">
            <span className="mono doc-title">{path}</span>
          </span>
          <span className="tag tag-outline tag-tight">new</span>
        </button>
      ))}
      <p className="inspector-foot">unified diff · Copy and Open in editor are in the pane</p>
    </div>
  );
}
