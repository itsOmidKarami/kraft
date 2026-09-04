import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api";
import type { DocumentDetail } from "../types";
import { useModal } from "../useModal";

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
  const ref = useModal<HTMLDivElement>(onClose);

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

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="document">
      <div className="modal doc-modal" ref={ref}>
        <button className="doc-close" onClick={onClose}>
          close
        </button>
        {error && <p className="form-error">{error}</p>}
        {!doc && !error && <p>loading…</p>}
        {doc && (
          <>
            <h3>{doc.title}</h3>
            <dl className="doc-meta">
              <div>
                <dt>kind</dt>
                <dd>{doc.kind ?? "—"}</dd>
              </div>
              <div>
                <dt>source</dt>
                <dd>{doc.source_kind}</dd>
              </div>
              <div>
                <dt>repo</dt>
                <dd>{doc.repo}</dd>
              </div>
              <div>
                <dt>path</dt>
                <dd>{doc.path}</dd>
              </div>
              <div>
                <dt>updated</dt>
                <dd>{doc.source_updated_at ?? "—"}</dd>
              </div>
              {!!doc.links.length && (
                <div>
                  <dt>linked to</dt>
                  <dd className="doc-links">
                    {[...new Set(doc.links.map((l) => l.work_item_id).filter(Boolean))].map(
                      (wid) => (
                        <Link key={wid} to={`/work-items/${wid}`} onClick={onNavigate}>
                          {wid}
                        </Link>
                      ),
                    )}
                    {doc.links
                      .filter((l) => l.node_id || l.worker_session_id)
                      .map((l, i) => (
                        <span key={`s${i}`} className="hook">
                          {l.node_id ?? l.hook_point ?? l.worker_session_id}
                        </span>
                      ))}
                  </dd>
                </div>
              )}
            </dl>
            {/* ponytail: raw markdown, no renderer — spec/plan source is readable
                as-is. Add react-markdown if this proves painful. */}
            <pre className="doc-body">{doc.content}</pre>
          </>
        )}
      </div>
    </div>
  );
}
