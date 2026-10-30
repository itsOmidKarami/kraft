import { useEffect, useRef, useState } from "react";
import { detailOf, jsonBody, request } from "../../http";
import { addedCheckText, approveBody, changedNodes, customRows, reviewSentence, type OneOffReviewData } from "../../item/oneOff";
import { ConfirmSheet, type useSheet } from "../nav/Sheet";

/** Start on an item whose one-off chain holds custom content nobody approved: the
 *  custom parts in full, then Approve and start, which names the digest this read
 *  answered. A chain that changed since is read again, never approved from the old read. */
export function OneOffSheet({ itemId, sheet, start }: { itemId: string; sheet: ReturnType<typeof useSheet>; start: (approved: boolean) => Promise<boolean> }) {
  const [data, setData] = useState<OneOffReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const read = async () => {
    const a = await request<OneOffReviewData>(`/work-items/${encodeURIComponent(itemId)}/one-off`);
    if (a.status === 200) setData(a.body);
    else setError(detailOf(a.body));
  };
  useEffect(() => { void read(); }, [itemId]); // eslint-disable-line react-hooks/exhaustive-deps
  // Nothing left to approve (a library-only chain, or someone approved since the screen read the item): start at once.
  const clear = !!data && !data.needs_review;
  const settled = useRef(false);
  useEffect(() => {
    if (clear && !settled.current) {
      settled.current = true;
      void start(false).then(() => sheet.close());
    }
  }, [clear]); // eslint-disable-line react-hooks/exhaustive-deps
  // Nothing until the read answers: a chain with nothing to approve never flashes a sheet.
  if (clear || (!data && !error)) return null;
  const changes = changedNodes(data?.provenance ?? null);
  const touched = [["Added", changes.added], ["Overridden", changes.overridden], ["Removed", changes.removed]] as const;
  const approve = async () => {
    if (!data || busy) return;
    setBusy(true);
    setError(null);
    try {
      const a = await request(`/work-items/${encodeURIComponent(itemId)}/one-off/approve`, jsonBody("POST", approveBody(data, "phone")));
      if (a.status !== 200) {
        setError(detailOf(a.body));
        if (a.status === 409) await read();
        return;
      }
      if (await start(true)) sheet.close();
    } finally {
      setBusy(false);
    }
  };
  return (
    <ConfirmSheet
      title="Approve this chain, then start?"
      text={data ? reviewSentence(data.extends, data.custom.length > 0) : undefined}
      confirm={{ label: "Approve and start", disabled: !data, run: () => void approve() }}
      busy={busy}
      error={error}
      onClose={sheet.close}
    >
      {data && (
        <div className="ph-oneoff">
          {touched.map(([label, ids]) => ids.length > 0 && <p key={label} className="ph-help"><b>{label}</b> {ids.join(", ")}</p>)}
          {customRows(data.custom).map((r) => (
            <section key={r.key} className="ph-oneoff-part">
              <p className="ph-help">{r.what}</p>
              <pre className="ph-start-lines">{r.text}</pre>
            </section>
          ))}
          {data.added.map((a) => <p key={a.node} className="ph-target">✓ {addedCheckText(a)}</p>)}
        </div>
      )}
    </ConfirmSheet>
  );
}
