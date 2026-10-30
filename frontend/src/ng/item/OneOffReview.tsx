import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { detailOf, jsonBody, request } from "../http";
import { OneOffSummary } from "./OneOffParts";
import { approveBody, changedNodes, reviewSentence, type OneOffReviewData } from "./oneOff";
import { useOneOffMarks } from "./oneOffMarks";
import "./draft/draft.css";

/** Start on an item whose one-off chain holds custom content nobody approved: the
 *  chain as filed, its custom parts in full, then Approve and start. The approval
 *  names the digest this read answered, so a chain that changed since is refused.
 *  `fromLink`: opened by an address, not a click, so it never starts on its own. */
export function OneOffReview({ itemId, fromLink, onApproved, onClose, onReading }: { itemId: string; fromLink?: boolean; onApproved: () => void; onClose: () => void; onReading?: (reading: boolean) => void }) {
  const [data, setData] = useState<OneOffReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const read = useCallback(async () => {
    const a = await request<OneOffReviewData>(`/work-items/${encodeURIComponent(itemId)}/one-off`);
    if (a.status === 200) setData(a.body);
    else setError(detailOf(a.body));
  }, [itemId]);
  useEffect(() => {
    onReading?.(true);
    void read().finally(() => onReading?.(false));
  }, [read]); // eslint-disable-line react-hooks/exhaustive-deps
  const changes = changedNodes(data?.provenance ?? null);
  const setMarks = useOneOffMarks((s) => s.set);
  useEffect(() => {
    setMarks({ ...Object.fromEntries(changes.added.map((n) => [n, "add" as const])), ...Object.fromEntries(changes.overridden.map((n) => [n, "change" as const])) });
    return () => setMarks({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Nothing left to approve (a library-only chain, or someone approved since the page read the item): a Start
  // the person pressed starts at once, no dialog. One that came from an address asks first.
  const settled = useRef(false);
  const clear = !!data && !data.needs_review;
  const auto = clear && !fromLink;
  useEffect(() => {
    if (auto && !settled.current) {
      settled.current = true;
      onApproved();
    }
  }, [auto]); // eslint-disable-line react-hooks/exhaustive-deps

  const approve = async () => {
    if (!data) return;
    if (clear) return onApproved();
    setBusy(true);
    setError(null);
    const a = await request(`/work-items/${encodeURIComponent(itemId)}/one-off/approve`, jsonBody("POST", approveBody(data, "web")));
    setBusy(false);
    if (a.status === 200) return onApproved();
    setError(detailOf(a.body));
    // A chain that changed since is shown again as it is now, never approved from the old read.
    if (a.status === 409) await read();
  };

  // Nothing is shown until the read answers, so a chain with nothing to approve never flashes a dialog.
  if (auto || (!data && !error)) return null;
  // `returnTo`: opened from an address nothing had focus, so close hands it to the header's main button, as Review & apply does.
  return (
    <Dialog title={clear ? "Start this item?" : "Approve this chain, then start?"} onClose={onClose} className="idr-review" returnTo={() => document.querySelector<HTMLElement>(".item-main-action")}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" data-autofocus disabled={busy || !data} onClick={approve}>{clear ? "Start" : "Approve and start"}</Button>
      </>}>
      {data && (
        <>
          <p className="idr-sub">{reviewSentence(data.extends, data.custom.length > 0)}</p>
          <OneOffSummary changes={changes} custom={data.custom} added={data.added} />
          {data.redrafted.length > 0 && (
            <>
              <p className="idr-h">changed since filing</p>
              {data.redrafted.map((r, i) => <pre key={i} className="idr-lines">{r.diff.join("\n")}</pre>)}
            </>
          )}
        </>
      )}
      {error && <p className="idr-moved" role="alert">{error}</p>}
    </Dialog>
  );
}
