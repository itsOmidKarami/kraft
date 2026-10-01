import { useEffect, useState } from "react";
import { docBody } from "../../format";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Markdown } from "../ui/Markdown";
import { showToast } from "../ui/Toast";
import { detailOf, request } from "../http";

export type DocSource = { kind: "document"; id: string; by?: string } | { kind: "artifact"; workItemId: string };
type Viewed = { title: string; path: string; content: string; truncated?: boolean };

/** The editors the server can launch (`POST /documents/{id}/open`); null is the system default. */
export const EDITORS: { id: string | null; name: string }[] = [
  { id: "code", name: "VS Code" },
  { id: "cursor", name: "Cursor" },
  { id: "zed", name: "Zed" },
  { id: "obsidian", name: "Obsidian" },
  { id: null, name: "Default app" },
];

/** A document over the page (prototype lines 278–283, GAP §2 #9): an indexed
 *  document opens in an editor and copies its path; a gate's artifact, read
 *  off the worktree with no index row or absolute path, shows only its text. */
export function DocViewer({ source, onClose }: { source: DocSource; onClose: () => void }) {
  const [doc, setDoc] = useState<Viewed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    const url = source.kind === "document" ? `/documents/${encodeURIComponent(source.id)}` : `/work-items/${encodeURIComponent(source.workItemId)}/artifact`;
    request<Viewed>(url).then((r) => (r.status === 200 ? setDoc(r.body) : setError(detailOf(r.body))));
  }, [source]);
  const open = async (editor: string | null) => {
    if (source.kind !== "document") return;
    const r = await request(`/documents/${encodeURIComponent(source.id)}/open`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ editor }) });
    setNote(r.status === 200 ? `Opened in ${EDITORS.find((e) => e.id === editor)?.name}.` : detailOf(r.body));
  };
  const indexed = source.kind === "document";
  return (
    <Dialog
      title={doc?.title ?? "Document"}
      onClose={onClose}
      footer={indexed && doc ? (
        <div className="dv-footer">
          <span className="item-muted">Open in</span>
          {EDITORS.map((e) => <Button key={e.name} onClick={() => open(e.id)}>{e.name}</Button>)}
          <Button onClick={() => navigator.clipboard?.writeText(doc.path).then(() => showToast("Copied path"), () => {})}>Copy path</Button>
        </div>
      ) : undefined}
    >
      <div className="dv">
        {doc && <p className="dv-path"><span className="is-mono">{doc.path}</span>{source.kind === "document" && source.by && <> · {source.by}</>}</p>}
        {note && <p className="item-muted" role="status">{note}</p>}
        {error ? <p className="item-error" role="alert">{error}</p> : !doc ? <p className="item-muted">Reading…</p> : (
          <div className="dv-body">
            <Markdown text={docBody(doc.content, doc.title)} />
            {doc.truncated && <p className="item-muted">The document is longer than the server serves; open it in an editor for the rest.</p>}
          </div>
        )}
      </div>
    </Dialog>
  );
}
