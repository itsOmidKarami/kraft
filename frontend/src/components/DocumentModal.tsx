import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowSquareOut,
  CaretDown,
  Copy,
  Eye,
  FileText,
  ListChecks,
  Notebook,
  X,
} from "@phosphor-icons/react";
import * as api from "../api";
import { ago } from "../format";
import type { DocumentDetail } from "../types";
import { backdropProps, useModal } from "../useModal";

/**
 * A document, read-only, over the work item (design 6a). Not a route: the
 * reader is looking at the item, and closing must put them back where they
 * were, not in history.
 */

const KIND_ICONS: Record<string, typeof FileText> = {
  plans: ListChecks,
  specs: FileText,
  reviews: Eye,
  sessions: Notebook,
};

/** The editors the server knows how to launch, plus the OS default. */
const EDITORS: { id: string | null; name: string }[] = [
  { id: "code", name: "VS Code" },
  { id: "cursor", name: "Cursor" },
  { id: "zed", name: "Zed" },
  { id: "obsidian", name: "Obsidian" },
  { id: null, name: "System default" },
];

const PREF_KEY = "kraft.preferred_editor";

/**
 * Which editor the "Open in" button offers first. Read from this browser until
 * `preferred_editor` lands in config with Settings → General (Kraft-7z6.14).
 */
function readPreferred(): string | null {
  try {
    return localStorage.getItem(PREF_KEY);
  } catch {
    return null; // private mode, blocked storage — the default is fine
  }
}

export function DocumentModal({
  id,
  onClose,
  onNavigate,
}: {
  id: string;
  onClose: () => void;
  /** Fired before a breadcrumb routes away, so a parent overlay can dismiss itself. */
  onNavigate?: () => void;
}) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [preferred, setPreferred] = useState<string | null>(readPreferred);
  const ref = useModal<HTMLDivElement>(onClose);
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

  /**
   * Ask the server to launch the editor. A headless server answers 501, so fall
   * back to the URL scheme, which the *viewer's* machine can honour even when
   * the server cannot.
   */
  const openIn = async (editor: string | null) => {
    setMenu(false);
    setNote(null);
    try {
      localStorage.setItem(PREF_KEY, editor ?? "");
    } catch {
      /* storage blocked; the choice just does not stick */
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

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="document" {...backdropProps(onClose)}>
      <div className="dialog doc-modal" ref={ref}>
        <header className="doc-modal-head">
          <Icon size={20} className="doc-modal-icon" />
          <div className="doc-modal-title">
            <span className="doc-modal-name">{doc?.title ?? "…"}</span>
            {doc && (
              <div className="doc-modal-meta">
                <span className="tag tag-neutral doc-kind">{doc.kind ?? doc.source_kind}</span>
                {doc.links.find((l) => l.node_id) && (
                  <span>{doc.links.find((l) => l.node_id)!.node_id}</span>
                )}
                <span>written {ago(doc.source_updated_at)}</span>
                <span className="doc-modal-path">{doc.path}</span>
                <span>indexed {ago(doc.indexed_at)}</span>
                {[...new Set(doc.links.map((l) => l.work_item_id).filter(Boolean))].map((wid) => (
                  <Link key={wid} to={`/work-items/${wid}`} onClick={onNavigate}>
                    {wid}
                  </Link>
                ))}
              </div>
            )}
          </div>
          <div className="doc-modal-actions" ref={menuRef}>
            {/* Launching an editor needs a window on one machine or the other.
                A phone has neither the server's desktop nor a vscode:// handler,
                so the whole launch affordance goes; Copy path is the fallback
                that works from anywhere. */}
            <div className="desktop-only">
              <button
                className="btn btn-primary doc-open"
                disabled={!doc}
                onClick={() => openIn(current.id)}
              >
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
            <button className="btn btn-icon btn-ghost" title="Copy path" onClick={copyPath}>
              <Copy size={14} />
            </button>
            <button className="btn btn-icon btn-ghost" title="Close · Esc" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="doc-modal-body">
          {error && <p className="form-error">{error}</p>}
          {!doc && !error && <p className="empty">loading…</p>}
          {doc && <Markdown remarkPlugins={[remarkGfm]}>{doc.content}</Markdown>}
          {doc && (
            <p className="doc-modal-foot">
              Read-only here. Edits happen in your editor; the index picks them up on the next
              scan.
            </p>
          )}
        </div>
        {note && <p className="doc-modal-note">{note}</p>}
      </div>
    </div>
  );
}
