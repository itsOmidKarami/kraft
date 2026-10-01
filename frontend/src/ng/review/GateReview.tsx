import { useEffect, useState } from "react";
import type { CompareFile, ReviewThread, WorkItem, WorkItemArtifact } from "../../types";
import { useModal } from "../../useModal";
import { detailOf, jsonBody, request } from "../http";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Markdown } from "../ui/Markdown";
import { X } from "../icons";
import { FileTree } from "./FileTree";
import { approveBlock, drafts } from "./finish";
import { codeBlock } from "./Thread";

/** The gate review overlay (prototype 292–321, `openGateReview`): the gate's
 *  document beside the list of what changed, with the gate's decision. A
 *  full-page layer over the review page, with its own header; Escape closes it
 *  to the page. */
export function GateReview({ item, gate, files, threads, isViewed, approve, onReviewChanges, onRequestChanges, onClose }: {
  item: Pick<WorkItem, "id" | "pending_gate">;
  gate: string;
  files: CompareFile[];
  threads: ReviewThread[];
  isViewed: (path: string) => boolean;
  /** Approve through the review route; resolves to a refusal or null. */
  approve: () => Promise<string | null>;
  /** To the review page, on one file or the first. */
  onReviewChanges: (file?: string) => void;
  onRequestChanges: () => void;
  /** Back to the item. */
  onClose: () => void;
}) {
  const ref = useModal<HTMLDivElement>(() => onReviewChanges());
  const [doc, setDoc] = useState<WorkItemArtifact | { error: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    request<WorkItemArtifact>(`/work-items/${encodeURIComponent(item.id)}/artifact`).then(({ status, body }) => live && setDoc(status === 200 ? body : { error: detailOf(body) }));
    return () => void (live = false);
  }, [item.id]);
  const block = approveBlock(item, gate, threads);
  const pending = drafts(threads).length;
  const add = files.reduce((n, f) => n + f.insertions, 0);
  const del = files.reduce((n, f) => n + f.deletions, 0);
  const onApprove = async () => {
    // A chain revision's approval carries the digest of the render read here
    // (Kraft-ec66w); the review route cannot pass one yet (Kraft-0ybgb), so
    // that one goes through the gate's own approve, as the shipped page does.
    if (doc && "digest" in doc && doc.digest) {
      const { status, body } = await request(`/work-items/${encodeURIComponent(item.id)}/gates/${encodeURIComponent(gate)}/approve`, jsonBody("POST", { digest: doc.digest }));
      if (status >= 200 && status < 300) return onClose();
      return setError(detailOf(body));
    }
    setError(await approve());
  };
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
          {!doc && <p className="rv-muted">Loading the document…</p>}
          {doc && "error" in doc && <p className="rv-error" role="alert">{doc.error}</p>}
          {doc && "content" in doc && (
            <>
              <div className="rv-gate-doc-head">
                <span className="rv-gate-doc-title">{doc.title}</span>
                <span className="rv-mono rv-muted">{doc.path}</span>
              </div>
              <Markdown text={doc.content} code={codeBlock} />
              {doc.truncated && <p className="rv-muted">The document stops at {Math.round(doc.artifact_max_bytes / 1024)} KB. The rest is in the worktree.</p>}
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
