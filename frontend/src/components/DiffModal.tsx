import { useEffect, useState } from "react";
import { X } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItemDiff } from "../types";
import { useModal } from "../useModal";

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

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="changes">
      <div className="dialog diff-modal" ref={ref}>
        <header className="diff-modal-head">
          <span className="mono">{diff?.base_ref?.slice(0, 10) ?? "—"}</span>
          {totals && (
            <span className="diff-totals">
              <span className="diff-add">+{totals.ins}</span>{" "}
              <span className="diff-del">−{totals.del}</span>
            </span>
          )}
          <button className="btn btn-ghost" onClick={onClose} aria-label="close">
            <X size={14} />
          </button>
        </header>

        {err && <p className="form-error">{err}</p>}

        {/* Two different empty states: no base_ref was ever pinned (nothing to
            diff against) versus a pinned base the worktree matches (a real,
            answerable "nothing changed yet"). */}
        {diff && diff.diff === "" && diff.untracked.length === 0 && (
          <p className="empty">
            {diff.base_ref
              ? "No changes yet — the worktree matches the base commit."
              : "No diff available for this work item yet."}
          </p>
        )}

        {diff && diff.files.length > 0 && (
          <ul className="diff-files">
            {diff.files.map((f) => (
              <li key={f.path}>
                <span className="mono">{f.path}</span>
                <span className="diff-add">+{f.insertions}</span>
                <span className="diff-del">−{f.deletions}</span>
              </li>
            ))}
          </ul>
        )}

        {diff && diff.diff !== "" && (
          <pre className="diff-body">
            {diff.diff.split("\n").map((line, i) => (
              <div key={i} className={lineClass(line)}>
                {line}
              </div>
            ))}
          </pre>
        )}

        {diff?.truncated && (
          <p className="field-hint">
            Diff truncated — the file list above is complete. Open the worktree for the
            full change.
          </p>
        )}

        {diff && diff.untracked.length > 0 && (
          <div className="diff-untracked">
            <span className="field-hint">New files (content not shown)</span>
            <ul>
              {diff.untracked.map((p) => (
                <li key={p} className="mono">
                  {p}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
