import { useCallback, useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { detailOf, jsonBody, request } from "../http";
import { OneOffParts } from "./OneOffParts";
import { addedCheckText, approveBody, changedNodes, type OneOffReviewData } from "./oneOff";
import { useOneOffMarks } from "./oneOffMarks";
import "./draft/draft.css";

const List = ({ label, ids }: { label: string; ids: string[] }) => (ids.length ? <p className="idr-sub"><b>{label}</b> <span className="is-mono">{ids.join(", ")}</span></p> : null);

/** Start on an item whose one-off chain holds custom content nobody approved: the
 *  chain as filed, its custom parts in full, then Approve and start. The approval
 *  names the digest this read answered, so a chain that changed since is refused. */
export function OneOffReview({ itemId, onApproved, onClose }: { itemId: string; onApproved: () => void; onClose: () => void }) {
  const [data, setData] = useState<OneOffReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const read = useCallback(async () => {
    const a = await request<OneOffReviewData>(`/work-items/${encodeURIComponent(itemId)}/one-off`);
    if (a.status === 200) setData(a.body);
    else setError(detailOf(a.body));
  }, [itemId]);
  useEffect(() => { void read(); }, [read]);
  const changes = changedNodes(data?.provenance ?? null);
  const setMarks = useOneOffMarks((s) => s.set);
  useEffect(() => {
    setMarks({ ...Object.fromEntries(changes.added.map((n) => [n, "add" as const])), ...Object.fromEntries(changes.overridden.map((n) => [n, "change" as const])) });
    return () => setMarks({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const approve = async () => {
    if (!data) return;
    setBusy(true);
    setError(null);
    const a = await request(`/work-items/${encodeURIComponent(itemId)}/one-off/approve`, jsonBody("POST", approveBody(data, "web")));
    setBusy(false);
    if (a.status === 200) return onApproved();
    setError(detailOf(a.body));
    // A chain that changed since is shown again as it is now, never approved from the old read.
    if (a.status === 409) await read();
  };

  return (
    <Dialog title="Approve this chain, then start?" onClose={onClose} className="idr-review"
      footer={<>
        <Button onClick={onClose}>Close</Button>
        <Button variant="primary" data-autofocus disabled={busy || !data?.needs_review} onClick={approve}>Approve and start</Button>
      </>}>
      {data ? (
        <>
          <p className="idr-sub">This item runs a chain of its own{data.extends ? <>, extending <span className="is-mono">{data.extends}</span></> : ""}. A person reads what is custom in it before it starts.</p>
          <List label="Added" ids={changes.added} />
          <List label="Overridden" ids={changes.overridden} />
          <List label="Removed" ids={changes.removed} />
          <p className="idr-h">Custom: read these</p>
          <OneOffParts custom={data.custom} />
          {data.redrafted.length > 0 && (
            <>
              <p className="idr-h">Changed since filing</p>
              {data.redrafted.map((r, i) => <pre key={i} className="idr-lines">{typeof r.diff === "string" ? r.diff : JSON.stringify(r.diff ?? r.changes, null, 2)}</pre>)}
            </>
          )}
          {data.added.length > 0 && <ul className="idr-checks">{data.added.map((a) => <li key={a.node} className="is-ok">✓ {addedCheckText(a)}</li>)}</ul>}
        </>
      ) : !error && <p className="idr-sub">Reading the chain…</p>}
      {error && <p className="idr-moved" role="alert">{error}</p>}
    </Dialog>
  );
}
