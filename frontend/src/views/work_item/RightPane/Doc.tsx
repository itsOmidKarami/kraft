import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowSquareOut, CaretDown, Copy, Eye, FileText, ListChecks, Notebook } from "@phosphor-icons/react";
import * as api from "../../../api";
import { ago } from "../../../format";
import type { DocumentDetail } from "../../../types";

/**
 * Right pane · Documents (UI v2 · 05, 14): `DocumentModal`'s fetch and
 * open-in-editor logic, without the dialog framing.
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

export function Doc({ id }: { id: string }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [preferred, setPreferred] = useState<string | null>(readPreferred);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(null);
    api
      .getDocument(id)
      .then((d) => live && setDoc(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menu]);

  const absPath = doc ? `${doc.repo.replace(/\/$/, "")}/${doc.path}` : "";

  const openIn = async (editor: string | null) => {
    setMenu(false);
    setNote(null);
    try {
      localStorage.setItem(PREF_KEY, editor ?? "");
    } catch {
      /* storage blocked */
    }
    setPreferred(editor);
    try {
      await api.openDocument(id, editor ?? undefined);
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
  const hasFile = doc?.origin !== "event_ingest";

  return (
    <div className="pane doc-pane" data-testid="right-pane-doc">
      <header className="doc-modal-head">
        <Icon size={20} className="doc-modal-icon" />
        <div className="doc-modal-title">
          <span className="doc-modal-name">{doc?.title ?? "…"}</span>
          {doc && (
            <div className="doc-modal-meta">
              <span className="tag tag-neutral doc-kind">{doc.kind ?? doc.source_kind}</span>
              <span>written {ago(doc.source_updated_at)}</span>
              <span className="doc-modal-path">{doc.path}</span>
              <span>indexed {ago(doc.indexed_at)}</span>
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
        </div>
      </header>

      <div className="doc-modal-body">
        {error && <p className="form-error">{error}</p>}
        {!doc && !error && <p className="empty">loading…</p>}
        {doc && <Markdown remarkPlugins={[remarkGfm]}>{doc.content}</Markdown>}
        {doc && (
          <p className="doc-modal-foot">
            {hasFile
              ? "Read-only here. Edits happen in your editor; the index picks them up on the next scan."
              : "Read-only here. This document lives only in Kraft's index — it was never written to the connected repo."}
          </p>
        )}
      </div>
      {note && <p className="doc-modal-note">{note}</p>}
    </div>
  );
}
