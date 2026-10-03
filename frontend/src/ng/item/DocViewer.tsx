import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { docBody } from "../../format";
import type { WorkItemDocument } from "../../types";
import { backdropProps, useModal } from "../../useModal";
import { ChevronDown, X } from "../icons";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Markdown } from "../ui/Markdown";
import { Menu } from "../ui/Menu";
import { editorChoices, editorName, useEditors } from "./editors";
import { showToast } from "../ui/Toast";
import { detailOf, request } from "../http";

export type DocSource = { kind: "document"; id: string; by?: string } | { kind: "artifact"; workItemId: string } | { kind: "attachment"; workItemId: string; attachment: string };

const urlOf = (s: DocSource) =>
  s.kind === "document" ? `/documents/${encodeURIComponent(s.id)}`
  : s.kind === "artifact" ? `/work-items/${encodeURIComponent(s.workItemId)}/artifact`
  // A spec or plan attached at intake, read from Kraft's copy: before start nothing has indexed it.
  : `/work-items/${encodeURIComponent(s.workItemId)}/attachments/${encodeURIComponent(s.attachment)}`;
type Viewed = { title: string; path: string; content: string; truncated?: boolean };

/** Where in the chain a document was written: node › task › attempt. */
export const docBy = (d?: WorkItemDocument) => (d ? [d.node_id, d.hook_point?.split(".").at(-1), d.attempt ? `attempt ${d.attempt}` : ""].filter(Boolean).join(" › ") : "");

/** A document in a drawer over the page's right side (prototype lines 278–283,
 *  GAP §2 #9): an indexed document opens in an editor and copies its path; a
 *  gate's artifact, read off the worktree with no index row or absolute path,
 *  shows only its text. A press on the scrim or Escape closes it. */
export function DocViewer({ source, onClose }: { source: DocSource; onClose: () => void }) {
  const [doc, setDoc] = useState<Viewed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Not kept across openings: the viewer unmounts on close.
  const [full, setFull] = useState(false);
  const ref = useModal<HTMLDivElement>(onClose);
  const titleId = useId();
  useEffect(() => {
    request<Viewed>(urlOf(source)).then((r) => (r.status === 200 ? setDoc(r.body) : setError(detailOf(r.body))));
    // The `by` line is display only: a new label must not read the document again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlOf(source)]);
  const open = async (editor: string | null) => {
    if (source.kind !== "document") return;
    const r = await request(`/documents/${encodeURIComponent(source.id)}/open`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ editor }) });
    setNote(r.status === 200 ? `Opened in ${editorName(editor)}.` : detailOf(r.body));
  };
  const indexed = source.kind === "document";
  const editors = useEditors(indexed);
  const by = source.kind === "document" ? source.by : undefined;
  return createPortal(
    <div className="dv-scrim" {...backdropProps(onClose)}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className={`dv-drawer${full ? " is-full" : ""}`}>
        <header className="dv-head">
          <div className="dv-head-top">
            <h2 id={titleId} className="dv-title">{doc?.title ?? "Document"}</h2>
            <button type="button" className="dv-full" onClick={() => setFull(!full)}>{full ? "⤡ exit full screen" : "⤢ full screen"}</button>
            <IconButton label="Close" onClick={onClose}><X size={16} aria-hidden /></IconButton>
          </div>
          {doc && <p className="dv-path is-mono">{doc.path}</p>}
          {by && <p className="dv-by">written by {by}</p>}
          {indexed && doc && (
            <div className="dv-actions">
              <OpenInEditor editors={editors} open={open} />
              <Button onClick={() => navigator.clipboard?.writeText(doc.path).then(() => showToast("Copied path"), () => {})}>Copy path</Button>
            </div>
          )}
        </header>
        {note && <p className="dv-note item-muted" role="status">{note}</p>}
        <div className="dv-body">
          {error ? <p className="item-error" role="alert">{error}</p> : !doc ? <p className="item-muted">Reading…</p> : (
            <>
              <Markdown text={docBody(doc.content, doc.title)} />
              {doc.truncated && <p className="item-muted">The document is longer than the server serves; open it in an editor for the rest.</p>}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** One Open in editor button, on the default editor; ▾ lists the others this
 *  machine has. With none it stays, disabled, and says why. */
function OpenInEditor({ editors, open }: { editors: ReturnType<typeof useEditors>; open: (editor: string | null) => void }) {
  const choices = editors && typeof editors !== "string" ? editorChoices(editors) : [];
  if (!choices.length) {
    const why = typeof editors === "string" ? editors : editors ? "No editor found on this machine" : "Looking for editors…";
    return <span className="dv-editor" title={why}><Button disabled title={why}>Open in editor</Button></span>;
  }
  const [first, ...rest] = choices;
  return (
    <span className="dv-editor">
      <Button title={first === null ? "Open with the system's default app" : `Open in ${editorName(first)}`} onClick={() => open(first)}>Open in editor</Button>
      {rest.length > 0 && <Menu label="Other editors" trigger={<ChevronDown size={14} aria-hidden />} items={rest.map((e) => ({ label: editorName(e), onSelect: () => open(e) }))} />}
    </span>
  );
}
