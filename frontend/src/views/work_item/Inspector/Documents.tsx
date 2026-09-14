import { useEffect, useRef, useState } from "react";
import { Eye, FileText, ListChecks, Notebook, Paperclip } from "@phosphor-icons/react";
import * as api from "../../../api";
import { shortIds } from "../../../format";
import type { WorkItemDocument } from "../../../types";
import { GATE_DOC_ID } from "../selection";

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
  gatePending,
  gateArtifactPending,
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
  /** Whether a gate is pending at all. `item.gate_artifact` is null both when
   *  there is no gate and when the gate's artifact is not yet written to disk
   *  (board.py's `gate_artifact`), so the "Gate document" section can't guard
   *  on `preselectPath` being present — it must guard on the gate itself. */
  gatePending: boolean;
  /** Whether a gate is pending and its artifact exists on disk. NOT derivable
   *  from the document list: the index does not ingest a gate's artifact until
   *  approval (`artifacts._ingest_approved_gate_artifact`), so "absent from
   *  `docs`" is the normal state of a written artifact, and the old
   *  `preselectMissing` read it as "not written yet" (G5-05). */
  gateArtifactPending: boolean;
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
  // It is a reason to show the artifact itself: on a miss we select
  // GATE_DOC_ID, and the right pane reads it out of the worktree via
  // `api.getWorkItemArtifact` (Kraft-8ic7). The selection upgrades to the
  // real document id once the index catches up.
  const autoPickRef = useRef<string | null>(null);
  useEffect(() => {
    if (!docs) return; // still loading
    if (selected && selected !== autoPickRef.current) return; // a real row click
    if (preselectPath) {
      const match = docs.find((d) => d.path === preselectPath);
      if (!match) {
        // Not a fallback to "some document" — this IS the right document,
        // just not in the index yet (docs may still be an empty list, since
        // the index hasn't ingested it). `RightPane` reads it off disk.
        if (gateArtifactPending && selected !== GATE_DOC_ID) {
          autoPickRef.current = GATE_DOC_ID;
          onSelect(GATE_DOC_ID);
        }
        return;
      }
      if (match.document_id === selected) return;
      autoPickRef.current = match.document_id;
      onSelect(match.document_id);
      return;
    }
    if (docs.length === 0) return;
    const pick = docs[0].document_id;
    if (pick === selected) return;
    autoPickRef.current = pick;
    onSelect(pick);
  }, [docs, selected, onSelect, preselectPath, gateArtifactPending]);

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
        {/* W10.D: stacked lines, not columns -- the title (2-line clamp) with its
            chips, then the path on one line cut from the left so the filename
            survives (the whole path is its title). The chips drop under the
            path when the row is too narrow to keep them beside the title. */}
        <span className="doc-text">
          <span className="doc-title" title={d.title}>{shortIds(d.title)}</span>
          <span className="doc-chips">
            <span className="tag tag-neutral doc-kind">{d.kind ?? d.source_kind}</span>
            {d.attachment_kind && (
              <span className="tag tag-outline doc-attached" title="attached at intake">
                <Paperclip size={11} aria-hidden />
                <span className="doc-attached-text">attached at intake</span>
              </span>
            )}
          </span>
          <span className="doc-path path" title={d.path} data-allow-ellipsis>
            <span dir="ltr">{d.path}</span>
          </span>
        </span>
      </button>
    );
  };

  return (
    <div className="inspector-list linked-docs" data-testid="inspector-documents">
      {error && <p className="form-error" role="alert">{error}</p>}
      {!error && docs?.length === 0 && <p className="empty">no linked documents yet</p>}

      {gatePending && (
        <>
          <p className="section-label">Gate document</p>
          {gateDoc ? (
            row(gateDoc)
          ) : (
            <button
              className="doc-row"
              data-selected={selected === GATE_DOC_ID}
              disabled={!gateArtifactPending}
              onClick={() => onSelect(GATE_DOC_ID)}
            >
              <FileText size={16} className="doc-icon" />
              <span className="doc-text">
                <span className="doc-title path" title={preselectPath ?? undefined}>{preselectPath}</span>
                <span className="doc-chips">
                  <span className="row-sub">{gateArtifactPending ? "not indexed yet" : "not written yet"}</span>
                </span>
              </span>
            </button>
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
