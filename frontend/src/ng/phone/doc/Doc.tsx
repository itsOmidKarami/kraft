import { useEffect, useState } from "react";
import { copyablePath, docBody } from "../../../format";
import { detailOf, request } from "../../http";
import { Markdown } from "../../ui/Markdown";
import { showToast } from "../../ui/Toast";
import { ScreenHeader } from "../nav/ScreenHeader";
import "./doc.css";

type Viewed = { title: string; path: string; content: string; repo?: string; origin?: string };

/** A document (W17 brief F.5), over whichever screen opened it with `?doc=<id>`: title, path (wraps, copyable), the text.
 *  `url` reads one that is not indexed instead, such as a spec attached at intake (`?attached=<kind>`). */
export function Doc({ id, url }: { id?: string; url?: string }) {
  const [doc, setDoc] = useState<Viewed | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(null);
    request<Viewed>(url ?? `/documents/${encodeURIComponent(id ?? "")}`).then((r) => {
      if (!live) return;
      if (r.status === 200) setDoc(r.body);
      else setError(r.status === 404 ? "This document is gone." : detailOf(r.body));
    });
    return () => void (live = false);
  }, [id, url]);
  return (
    <>
      <ScreenHeader />
      <div className="ph-content">
        {error && <><h1 className="ph-title">Document</h1><p className="ph-empty" role="alert">{error}</p></>}
        {!doc && !error && <div className="ph-skeleton" aria-busy="true"><span /><span /></div>}
        {doc && (
          <>
            <div className="ph-doc-head">
              <h1 className="ph-doc-title">{doc.title}</h1>
              <p className="ph-doc-path">{doc.path}</p>
              {/* A session summary or gate artifact lives only in Kraft's index: there is no file path to copy (R13b-02). */}
              {doc.origin !== "event_ingest" && <button type="button" className="ph-linkbtn" onClick={() => navigator.clipboard?.writeText(copyablePath(doc)).then(() => showToast("Copied path"), () => showToast("Could not copy the path."))}>Copy path</button>}
            </div>
            {doc.content.trim() ? <div className="ph-doc-body"><Markdown text={docBody(doc.content, doc.title)} /></div> : <p className="ph-empty">This document is empty.</p>}
          </>
        )}
      </div>
    </>
  );
}
