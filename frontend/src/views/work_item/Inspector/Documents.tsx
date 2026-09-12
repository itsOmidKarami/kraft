import { useEffect, useRef, useState } from "react";
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
  preselectPath,
}: {
  workItemId: string;
  eventCount: number;
  selected: string | null;
  onSelect: (documentId: string) => void;
  /** The gate card's "Read <doc>" (06) names a specific artifact by repo
   *  path (`item.gate_artifact`) — preferred over "just pick the first
   *  document" once the list lands with a matching row, and sections the
   *  list (G5-05): a "Gate document" row above everything else, "not
   *  written yet" in its own right column when the gate has none. */
  preselectPath?: string | null;
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

  // Default the selection once the list lands, so the right pane never sits
  // empty for a tab that has content: the gate's own artifact by path when
  // one was asked for, the first document otherwise. `autoPickRef` marks a
  // selection this effect made itself (not a row click) -- a gate's artifact
  // can land in the index a scan behind the gate appearing, so an earlier
  // auto-pick (the wrong document, all that existed yet) has to be free to
  // upgrade once the real match shows up; a person's own click never should.
  //
  // When a preselectPath is given, a miss must not fall back to docs[0]: a
  // gate's artifact isn't ingested until approval
  // (artifacts._ingest_approved_gate_artifact), so "no match yet" is the
  // normal pre-approval state, not a reason to show some unrelated document.
  const autoPickRef = useRef<string | null>(null);
  const preselectMissing = !!preselectPath && !!docs && !docs.some((d) => d.path === preselectPath);
  useEffect(() => {
    if (!docs || docs.length === 0) return;
    if (selected && selected !== autoPickRef.current) return; // a real row click
    if (preselectPath) {
      const match = docs.find((d) => d.path === preselectPath);
      if (!match) return; // don't auto-pick an unrelated document
      if (match.document_id === selected) return;
      autoPickRef.current = match.document_id;
      onSelect(match.document_id);
      return;
    }
    const pick = docs[0].document_id;
    if (pick === selected) return;
    autoPickRef.current = pick;
    onSelect(pick);
  }, [docs, selected, onSelect, preselectPath]);

  const gateDoc = preselectPath ? (docs?.find((d) => d.path === preselectPath) ?? null) : null;
  const rest = docs?.filter((d) => d !== gateDoc) ?? [];

  const row = (d: WorkItemDocument) => {
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
  };

  return (
    <div className="inspector-list linked-docs" data-testid="inspector-documents">
      {error && <p className="form-error" role="alert">{error}</p>}
      {!error && docs?.length === 0 && <p className="empty">no linked documents yet</p>}

      {!!preselectPath && (
        <>
          <p className="section-label">Gate document</p>
          {gateDoc ? (
            row(gateDoc)
          ) : (
            <div className="doc-row" data-kind="placeholder">
              <FileText size={16} className="doc-icon" />
              <span className="doc-text">
                <span className="doc-title">{preselectPath}</span>
              </span>
              <span className="row-sub">{preselectMissing ? "not written yet" : ""}</span>
            </div>
          )}
        </>
      )}

      {!!rest.length && <p className="section-label">Written by this node</p>}
      {rest.map(row)}

      {!!docs?.length && (
        <p className="inspector-foot">
          Written by agents into <code>.engineering/</code>; the index lags a scan behind git.
        </p>
      )}
    </div>
  );
}
