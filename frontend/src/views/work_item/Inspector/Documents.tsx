import { useEffect, useState } from "react";
import { Eye, FileText, ListChecks, Notebook } from "@phosphor-icons/react";
import * as api from "../../../api";
import type { WorkItemDocument } from "../../../types";

/**
 * Inspector · Documents (UI v2 · 05, 14): `LinkedDocuments`'s fetch, as a
 * selectable list instead of rows that each open `DocumentModal`. Selecting
 * a row is what `RightPane/Doc.tsx` shows.
 */

const KIND_ICONS: Record<string, typeof FileText> = {
  plans: ListChecks,
  specs: FileText,
  reviews: Eye,
  sessions: Notebook,
};

export function Documents({
  workItemId,
  eventCount,
  selected,
  onSelect,
}: {
  workItemId: string;
  eventCount: number;
  selected: string | null;
  onSelect: (documentId: string) => void;
}) {
  const [docs, setDocs] = useState<WorkItemDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getWorkItemDocuments(workItemId)
      .then((r) => {
        if (!live) return;
        setDocs(r.documents);
        setError(null);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [workItemId, eventCount]);

  // Default the selection to the first document once the list lands, so the
  // right pane never sits empty for a tab that has content.
  useEffect(() => {
    if (!selected && docs && docs.length > 0) onSelect(docs[0].document_id);
  }, [docs, selected, onSelect]);

  return (
    <div className="inspector-list linked-docs" data-testid="inspector-documents">
      {error && <p className="form-error" role="alert">{error}</p>}
      {!error && docs?.length === 0 && <p className="empty">no linked documents yet</p>}
      {docs?.map((d) => {
        const Icon = KIND_ICONS[d.kind ?? ""] ?? FileText;
        return (
          <button
            key={d.document_id}
            className="doc-row"
            data-selected={d.document_id === selected}
            onClick={() => onSelect(d.document_id)}
          >
            <Icon size={16} className="doc-icon" />
            <span className="doc-text">
              <span className="doc-title">{d.title}</span>
              <span className="doc-path">{d.path}</span>
            </span>
            <span className="tag tag-neutral doc-kind">{d.kind ?? d.source_kind}</span>
            {d.attachment_kind && <span className="tag tag-outline doc-attached">attached at intake</span>}
          </button>
        );
      })}
      {!!docs?.length && (
        <p className="inspector-foot">
          Written by agents into <code>.engineering/</code>; the index lags a scan behind git.
        </p>
      )}
    </div>
  );
}
