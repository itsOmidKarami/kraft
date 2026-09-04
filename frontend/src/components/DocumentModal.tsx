import { useEffect, useState } from "react";
import * as api from "../api";
import type { DocumentDetail } from "../types";

export function DocumentModal({ id, onClose }: { id: string; onClose: () => void }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    <div className="modal-backdrop" role="dialog" aria-label="document">
      <div className="modal doc-modal">
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
