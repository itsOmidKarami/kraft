import { useEffect, useState } from "react";
import { Eye, FileText, ListChecks, Notebook } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItemDocument } from "../types";
import { DocumentModal } from "./DocumentModal";

/**
 * The Documents tab (design 4e). Fetched here rather than reduced into the
 * store: this is a per-view read, not event-stream state. It refetches when the
 * item's event count moves, which is how a session summary written mid-chain
 * shows up without a second polling loop.
 */

/** Kind → glyph, per spec §6 row 4e. Anything unrecognised reads as a file. */
const KIND_ICONS: Record<string, typeof FileText> = {
  plans: ListChecks,
  specs: FileText,
  reviews: Eye,
  sessions: Notebook,
};

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
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {!error && docs?.length === 0 && <p className="empty">no linked documents yet</p>}
      {docs?.map((d) => {
        const Icon = KIND_ICONS[d.kind ?? ""] ?? FileText;
        return (
          <button key={d.document_id} className="doc-row" onClick={() => setOpenDoc(d.document_id)}>
            <Icon size={16} className="doc-icon" />
            <span className="doc-text">
              <span className="doc-title">{d.title}</span>
              <span className="doc-path">{d.path}</span>
            </span>
            <span className="tag tag-neutral doc-kind">{d.kind ?? d.source_kind}</span>
            <span className="doc-node">{d.node_id}</span>
          </button>
        );
      })}
      {!!docs?.length && (
        <p className="doc-foot">
          Read-only. Written by agents into <code>.engineering/</code>; the index lags a scan
          behind git.
        </p>
      )}
      {openDoc && <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} />}
    </section>
  );
}
