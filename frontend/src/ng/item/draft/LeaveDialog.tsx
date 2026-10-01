import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { showToast } from "../../ui/Toast";
import { useApply } from "./apply";
import { useDraft } from "./context";

/** The click guard (Decided 16): with a draft, a click on an in-app link that
 *  leaves this item opens the dialog first; a hard unload gets the browser's own
 *  prompt. The back button and a typed URL are not guarded: the draft is on the
 *  server and the header shows it when the person returns. */
export function LeaveGuard() {
  const d = useDraft();
  const navigate = useNavigate();
  const [to, setTo] = useState<string | null>(null);
  const has = !!d && d.ops.length > 0;
  const id = d?.raw.id;
  useEffect(() => {
    if (!has || !id) return;
    const own = `/ng/work-items/${encodeURIComponent(id)}`;
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || (a.target && a.target !== "_self") || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin || !url.pathname.startsWith("/ng/")) return;
      if (url.pathname === own || url.pathname.startsWith(`${own}/`)) return;
      e.preventDefault();
      e.stopPropagation();
      setTo(url.pathname.slice("/ng".length) + url.search);
    };
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    document.addEventListener("click", onClick, true);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      document.removeEventListener("click", onClick, true);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, [has, id]);
  if (!d || !has || to === null) return null;
  return <Leave onStay={() => setTo(null)} go={() => { setTo(null); navigate(to); }} />;
}

function Leave({ onStay, go }: { onStay: () => void; go: () => void }) {
  const d = useDraft()!;
  const apply = useApply();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const blocked = d.issues.length > 0;
  const n = d.changes;
  const review = () => { onStay(); d.setReviewing(true); };
  const run = async () => {
    if (blocked) return review();
    setBusy(true);
    const o = await apply();
    setBusy(false);
    if (o.kind === "applied" || o.kind === "gone") go();
    else review();
  };
  const discard = async () => {
    setBusy(true);
    const a = await d.draft.discard();
    setBusy(false);
    if (a.status === 204 || a.status === 404) {
      showToast("Draft discarded");
      go();
    }
  };
  return (
    <Dialog title="You have unapplied changes to this item" onClose={onStay}
      footer={asking ? (
        <>
          <span className="idr-ask">Discard this draft and continue? It cannot be brought back.</span>
          <Button onClick={() => setAsking(false)}>Keep</Button>
          <Button className="idr-danger" disabled={busy} onClick={discard}>Discard & continue</Button>
        </>
      ) : (
        <>
          <Button className="idr-danger idr-left" onClick={() => setAsking(true)}>Discard &amp; continue</Button>
          <Button onClick={onStay}>Stay</Button>
          <Button variant="primary" disabled={busy} onClick={run}>{blocked ? "Review problems" : "Apply & continue"}</Button>
        </>
      )}>
      <p className="idr-sub">{n} {n === 1 ? "change is" : "changes are"} saved as a draft on this item. The run does not see {n === 1 ? "it" : "them"} until you apply.</p>
    </Dialog>
  );
}
