import { useState } from "react";
import type { CompareFile, ReviewThread, WorkItem, WorkItemArtifact } from "../../types";
import { useModal } from "../../useModal";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Markdown } from "../ui/Markdown";
import { X } from "../icons";
import { FileTree } from "./FileTree";
import { approveBlock, drafts } from "./finish";
import { codeBlock } from "./Thread";
import type { Fetched } from "./useReview";

/** The gate review overlay (prototype 292–321, `openGateReview`): the gate's
 *  document beside the list of what changed, with the gate's decision. A
 *  full-page layer over the review page, with its own header; Escape closes it
 *  to the page. */
export function GateReview({ item, gate, doc, files, threads, isViewed, approve, onReviewChanges, onRequestChanges, onClose }: {
  item: Pick<WorkItem, "id" | "pending_gate">;
  gate: string;
  /** GET /artifact, read by the page (its digest also rides on the page's Approve). */
  doc: Fetched<WorkItemArtifact> | null;
  files: CompareFile[];
  threads: ReviewThread[];
  isViewed: (path: string) => boolean;
  /** Approve through the review route, a chain revision's digest included; resolves to a refusal or null. */
  approve: () => Promise<string | null>;
  /** To the review page, on one file or the first. */
  onReviewChanges: (file?: string) => void;
  onRequestChanges: () => void;
  /** Back to the item. */
  onClose: () => void;
}) {
  const ref = useModal<HTMLDivElement>(() => onReviewChanges());
  const [error, setError] = useState<string | null>(null);
  const block = approveBlock(item, gate, threads);
  const pending = drafts(threads).length;
  const add = files.reduce((n, f) => n + f.insertions, 0);
  const del = files.reduce((n, f) => n + f.deletions, 0);
  const onApprove = async () => setError(await approve());
  return (
    <div ref={ref} className="rv-gate" role="dialog" aria-modal="true" aria-label={`Gate review: ${gate}`}>
      <div className="rv-gate-head">
        <span className="rv-mono rv-gate-name">{gate}</span>
        <span className="rv-gate-tag">WAITING FOR YOU</span>
        {pending > 0 && <span className="rv-muted">{pending} {pending === 1 ? "comment" : "comments"} pending</span>}
        {error && <span className="rv-error" role="alert">{error}</span>}
        <span className="rv-spacer" />
        <Button onClick={onRequestChanges}>Request changes</Button>
        <Button variant="primary" disabled={!!block} title={block ?? undefined} onClick={onApprove}>Approve</Button>
        <IconButton label="Close" onClick={onClose}><X size={16} aria-hidden /></IconButton>
      </div>
      <div className="rv-gate-body">
        <article className="rv-gate-doc" aria-label="Document">
          {(!doc || doc.state === "loading") && <p className="rv-muted">Loading the document…</p>}
          {doc?.state === "error" && <p className="rv-error" role="alert">{doc.error}</p>}
          {doc?.state === "ready" && (
            <>
              <div className="rv-gate-doc-head">
                <span className="rv-gate-doc-title">{doc.data.title}</span>
                <span className="rv-mono rv-muted">{doc.data.path}</span>
              </div>
              <Markdown text={doc.data.content} code={codeBlock} />
              {doc.data.truncated && <p className="rv-muted">The document stops at {Math.round(doc.data.artifact_max_bytes / 1024)} KB. The rest is in the worktree.</p>}
            </>
          )}
        </article>
        <aside className="rv-gate-side" aria-label="Changes">
          <div className="rv-gate-side-head">
            <span>CHANGES</span>
            <span>{files.length} files <span className="rv-add">+{add}</span> <span className="rv-del">−{del}</span></span>
          </div>
          <FileTree files={files} untracked={[]} notShown={new Set()} threads={threads} selected={null} isViewed={isViewed} onSelect={(f) => onReviewChanges(f)} error={null} />
          <div className="rv-gate-side-foot">
            <Button variant="primary" onClick={() => onReviewChanges()}>Review Changes</Button>
          </div>
        </aside>
      </div>
    </div>
  );
}
