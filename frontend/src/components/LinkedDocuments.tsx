import { useEffect, useState } from "react";
import * as api from "../api";
import type { WorkItemDocument } from "../types";
import { DocumentModal } from "./DocumentModal";

/**
 * Documents linked to this work item (04 §9, 05 §4.2). Fetched here rather than
 * reduced into the store: this is a per-view read, not event-stream state. It
 * refetches when the item's event count moves, which is how a session summary
 * written mid-chain shows up without a second polling loop.
 */
export function LinkedDocuments({
  workItemId,
  eventCount,
}: {
  workItemId: string;
  eventCount: number;
}) {
  const [docs, setDocs] = useState<WorkItemDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getWorkItemDocuments(workItemId)
      .then((r) => {
        if (!live) return;
        setDocs(r.documents);
        setError(null);
      })
      .catch((e) => {
        if (!live) return;
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [workItemId, eventCount]);

  return (
    <section className="linked-docs">
      <h3>documents</h3>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {!error && docs?.length === 0 && <p className="empty">no linked documents yet</p>}
      {!!docs?.length && (
        <ul>
          {docs.map((d) => (
            <li key={d.document_id}>
              <button className="linked-doc" onClick={() => setOpenDoc(d.document_id)}>
                <span className="linked-doc-title">{d.title}</span>
                <span className="chip" data-kind={d.kind ?? ""}>
                  {d.kind ?? d.source_kind}
                </span>
                {d.node_id && <span className="hook">{d.node_id}</span>}
                <span className="repo-tag">{d.path}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {openDoc && <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} />}
    </section>
  );
}
