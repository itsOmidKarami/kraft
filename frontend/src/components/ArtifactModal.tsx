import { useEffect, useState } from "react";
import { X } from "@phosphor-icons/react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "../api";
import type { WorkItemArtifact } from "../types";
import { backdropProps, useModal } from "../useModal";

/**
 * The spec or plan the pending gate is a decision about, read-only.
 *
 * Sibling to DiffModal rather than a mode of it, for the same reason DiffModal
 * is a sibling of DocumentModal: this one renders markdown and that one colours
 * a diff, and neither shares the other's body.
 *
 * Not DocumentModal itself: this reads the *worktree*, before the indexer has
 * seen the file. A reviewer at an open gate must see what the agent just wrote.
 */
export function ArtifactModal({
  workItemId,
  onClose,
}: {
  workItemId: string;
  onClose: () => void;
}) {
  const ref = useModal<HTMLDivElement>(onClose);
  const [doc, setDoc] = useState<WorkItemArtifact | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .getWorkItemArtifact(workItemId)
      .then(setDoc)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [workItemId]);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="document" {...backdropProps(onClose)}>
      <div className="dialog diff-modal" ref={ref}>
        <header className="diff-modal-head">
          <span>{doc?.title ?? "—"}</span>
          <span className="mono">{doc?.path}</span>
          <button className="btn btn-ghost" onClick={onClose} aria-label="close">
            <X size={14} />
          </button>
        </header>
        <div className="doc-modal-body">
          {err && <p className="form-error">{err}</p>}
          {doc && <Markdown remarkPlugins={[remarkGfm]}>{doc.content}</Markdown>}
          {doc?.truncated && (
            <p className="control-hint">
              This document is too large to show whole; the rest is in the worktree.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
