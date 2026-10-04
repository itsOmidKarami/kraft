import { useState } from "react";
import type { CompareFile, ReviewThread, WorkItem, WorkItemArtifact } from "../../types";
import { docBody } from "../../format";
import { useModal } from "../../useModal";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Markdown } from "../ui/Markdown";
import { X } from "../icons";
import { detailOf, request } from "../http";
import { OpenInEditor } from "../item/DocViewer";
import { editorName, useEditors } from "../item/editors";
import { showToast } from "../ui/Toast";
import { FileTree } from "./FileTree";
import { approveBlock, drafts } from "./finish";
import { codeBlock } from "./Thread";
import type { Fetched } from "./useReview";

/** The gate review overlay (prototype 292–321, `openGateReview`): the gate's
 *  document beside the list of what changed, with the gate's decision. A
 *  full-page layer over the review page, with its own header; Escape closes it
 *  to the page. */
export function GateReview({ item, gate, doc, by, files, threads, isViewed, approve, onReviewChanges, onRequestChanges, onClose }: {
  item: Pick<WorkItem, "id" | "pending_gate">;
  gate: string;
  /** GET /artifact, read by the page (its digest also rides on the page's Approve). */
  doc: Fetched<WorkItemArtifact> | null;
  /** The task that wrote the document, by path, when the chain says. */
  by?: string | null;
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
  const editors = useEditors(doc?.state === "ready");
  const [note, setNote] = useState<string | null>(null);
  const open = async (editor: string) => {
    const r = await request(`/work-items/${encodeURIComponent(item.id)}/artifact/open`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ editor }) });
    setNote(r.status === 200 ? `Opened in ${editorName(editor)}.` : detailOf(r.body));
  };
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
                {by && <span className="rv-muted">written by <span className="rv-mono">{by}</span></span>}
                <div className="rv-gate-doc-actions">
                  <OpenInEditor editors={editors} open={open} />
                  <Button onClick={() => navigator.clipboard?.writeText(doc.data.absolute_path ?? doc.data.path).then(() => showToast("Copied path"), () => {})}>Copy path</Button>
                </div>
                {note && <span className="rv-muted" role="status">{note}</span>}
              </div>
              {/* Its own H1 repeats the title above, as the document viewer drops it. */}
              <Markdown text={docBody(doc.data.content, doc.data.title)} code={codeBlock} />
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
            <Button variant="primary" onClick={() => onReviewChanges()}>Review changes</Button>
          </div>
        </aside>
      </div>
    </div>
  );
}
