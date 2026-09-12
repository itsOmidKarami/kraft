import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowSquareOut, ArrowsOutSimple, CaretDown, Check, Copy, Eye, FileText, ListChecks, Notebook } from "@phosphor-icons/react";
import * as api from "../../../api";
import { ago } from "../../../format";
import type { WorkItem } from "../../../types";
import { Composer } from "../ActionBar/Composer";
import { rejectTarget } from "../ActionBar/GateCard";
import { useActionBar } from "../ActionBar/useActionBar";

/**
 * Right pane · Documents (UI v2 · 05, 14): `DocumentModal`'s fetch and
 * open-in-editor logic, without the dialog framing. When the open document
 * is the pending gate's own artifact, the header also carries that gate's
 * Approve/Reject — the reader ends where the decision is (G5-04), reusing
 * `useActionBar` rather than a second approve path.
 */

const KIND_ICONS: Record<string, typeof FileText> = {
  plans: ListChecks,
  specs: FileText,
  reviews: Eye,
  sessions: Notebook,
};

const EDITORS: { id: string | null; name: string }[] = [
  { id: "code", name: "VS Code" },
  { id: "cursor", name: "Cursor" },
  { id: "zed", name: "Zed" },
  { id: "obsidian", name: "Obsidian" },
  { id: null, name: "System default" },
];

const PREF_KEY = "kraft.preferred_editor";

function readPreferred(): string | null {
  try {
    return localStorage.getItem(PREF_KEY);
  } catch {
    return null;
  }
}

export type DocSource = { kind: "document"; id: string } | { kind: "artifact"; workItemId: string };

/** What the body renders, from either source. `repo`, `kind` and
 *  `indexed_at` are document-only: an un-indexed artifact has no index row
 *  and no absolute path, which is why artifact mode hides Open-in-editor
 *  and Copy-path rather than showing them broken. */
type Viewed = {
  title: string;
  path: string;
  content: string;
  kind?: string | null;
  repo?: string;
  indexed_at?: string;
  truncated?: boolean;
  source_updated_at?: string | null;
  origin?: string;
};

export function Doc({
  source,
  item,
  maximized,
  onToggleMaximize,
}: {
  source: DocSource;
  /** The gate's Approve/Reject show in this pane's header when the open
   *  document is `item.pending_gate`'s own artifact — omitted where there
   *  is no item to check against (the phone page's own `Doc` usage). */
  item?: WorkItem;
  maximized?: boolean;
  onToggleMaximize?: () => void;
}) {
  const [doc, setDoc] = useState<Viewed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [preferred, setPreferred] = useState<string | null>(readPreferred);
  const [rejecting, setRejecting] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const isArtifact = source.kind === "artifact";
  const gate = item?.pending_gate ?? null;
  const isGateDoc = isArtifact ? !!gate : !!gate && !!item?.gate_artifact && doc?.path === item.gate_artifact;
  const { busy: gateBusy, err: gateErr, run: runGate } = useActionBar(item?.id ?? "");
  const target = gate && item ? rejectTarget(item, gate) : null;

  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(null);
    const p: Promise<Viewed> =
      source.kind === "document"
        ? api.getDocument(source.id).then((d): Viewed => ({ ...d, kind: d.kind ?? d.source_kind }))
        : api.getWorkItemArtifact(source.workItemId).then(
            (a): Viewed => ({
              title: a.title,
              path: a.path,
              content: a.content,
              truncated: a.truncated,
            }),
          );
    p.then((v) => live && setDoc(v)).catch(
      (e) =>
        live &&
        setError(source.kind === "artifact" ? "the gate's document could not be read" : e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.kind, source.kind === "document" ? source.id : source.workItemId]);

  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menu]);

  const absPath = doc?.repo ? `${doc.repo.replace(/\/$/, "")}/${doc.path}` : "";

  const openIn = async (editor: string | null) => {
    if (source.kind !== "document") return;
    setMenu(false);
    setNote(null);
    try {
      localStorage.setItem(PREF_KEY, editor ?? "");
    } catch {
      /* storage blocked */
    }
    setPreferred(editor);
    try {
      await api.openDocument(source.id, editor ?? undefined);
    } catch {
      window.location.href = `vscode://file/${absPath}`;
      setNote("the server could not launch an editor — handed the path to this machine instead");
    }
  };

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(absPath);
      setNote("path copied");
    } catch {
      setNote("could not copy the path");
    }
  };

  const Icon = KIND_ICONS[doc?.kind ?? ""] ?? FileText;
  const current = EDITORS.find((e) => e.id === preferred) ?? EDITORS[EDITORS.length - 1];
  const hasFile = !isArtifact && doc?.origin !== "event_ingest";

  return (
    <div className="pane doc-pane" data-testid="right-pane-doc">
      <header className="doc-modal-head">
        <Icon size={20} className="doc-modal-icon" />
        <div className="doc-modal-title">
          <span className="doc-modal-name">{doc?.title ?? "…"}</span>
          {doc && (
            <div className="doc-modal-meta">
              {doc.kind && <span className="tag tag-neutral doc-kind">{doc.kind}</span>}
              {doc.source_updated_at && <span>written {ago(doc.source_updated_at)}</span>}
              <span className="doc-modal-path">{doc.path}</span>
              {doc.indexed_at ? <span>indexed {ago(doc.indexed_at)}</span> : <span>not indexed yet — read from the worktree</span>}
            </div>
          )}
        </div>
        <div className="doc-modal-actions" ref={menuRef}>
          {hasFile && (
            <div className="desktop-only">
              <button className="btn btn-primary doc-open" disabled={!doc} onClick={() => openIn(current.id)}>
                <ArrowSquareOut size={13} />
                Open in {current.name}
              </button>
              <button
                className="btn btn-primary doc-open-more"
                aria-label="Choose editor"
                aria-expanded={menu}
                disabled={!doc}
                onClick={() => setMenu((v) => !v)}
              >
                <CaretDown size={12} />
              </button>
              {menu && (
                <div className="doc-editor-menu card elev-lg" role="menu">
                  {EDITORS.map((e) => (
                    <button key={e.name} role="menuitem" onClick={() => openIn(e.id)}>
                      {e.name}
                      {e.id === current.id && <span className="doc-editor-default">default</span>}
                    </button>
                  ))}
                  <p className="doc-editor-foot">Default editor is set in Settings → General.</p>
                </div>
              )}
            </div>
          )}
          {hasFile && (
            <button className="btn btn-icon btn-ghost" title="Copy path" onClick={copyPath}>
              <Copy size={14} />
            </button>
          )}
          {onToggleMaximize && (
            <button
              className="btn btn-icon btn-ghost"
              title={maximized ? "Collapse" : "Maximize"}
              aria-pressed={maximized}
              onClick={onToggleMaximize}
            >
              <ArrowsOutSimple size={14} />
            </button>
          )}
          {isGateDoc && item && (
            <>
              <button
                className="btn btn-primary"
                disabled={gateBusy}
                onClick={() => runGate(() => api.approveGate(item.id, gate!), "Approved — chain continues")}
              >
                <Check size={14} /> Approve
              </button>
              <button className="btn btn-secondary" disabled={gateBusy} onClick={() => setRejecting(true)}>
                Reject…
              </button>
            </>
          )}
        </div>
      </header>

      {isGateDoc && rejecting && item && (
        <Composer
          title={`Reject ${gate}`}
          value={rejectNote}
          onChange={setRejectNote}
          busy={gateBusy}
          error={gateErr}
          placeholder="What should change?"
          footnote={
            target ? (
              <>
                re-enters at <code>{target}</code> with this note as its steer
              </>
            ) : undefined
          }
          submitLabel={target ? "Reject and send back" : "Reject and re-plan"}
          disabled={rejectNote.trim() === ""}
          onSubmit={() =>
            runGate(
              () => api.rejectGate(item.id, gate!, rejectNote),
              `Rejected — re-running from ${target ?? gate}`,
            )
          }
          onCancel={() => setRejecting(false)}
        />
      )}

      <div className="doc-modal-body">
        {error && <p className="form-error">{error}</p>}
        {!doc && !error && <p className="empty">loading…</p>}
        {doc && <Markdown remarkPlugins={[remarkGfm]}>{doc.content}</Markdown>}
        {doc?.truncated && <p className="doc-modal-note">truncated — the rest is in the file</p>}
        {doc && (
          <p className="doc-modal-foot">
            {hasFile
              ? "Read-only here. Edits happen in your editor; the index picks them up on the next scan."
              : isArtifact
                ? "Read-only here. Approve to add it to Kraft's index."
                : "Read-only here. This document lives only in Kraft's index — it was never written to the connected repo."}
          </p>
        )}
      </div>
      {note && <p className="doc-modal-note">{note}</p>}
    </div>
  );
}
