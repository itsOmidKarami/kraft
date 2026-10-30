import { useEffect, useRef, useState } from "react";
import { detailOf, jsonBody, request } from "../../http";
import { addedCheckText, approveBody, changedNodes, customRows, type OneOffReviewData } from "../../item/oneOff";
import { ChoiceSheet, type useSheet } from "../nav/Sheet";

/** Start on an item whose one-off chain holds custom content nobody approved: the
 *  custom parts in full, then Approve and start, which names the digest this read
 *  answered. A chain that changed since is read again, never approved from the old read. */
export function OneOffSheet({ itemId, sheet, start }: { itemId: string; sheet: ReturnType<typeof useSheet>; start: () => Promise<boolean> }) {
  const [data, setData] = useState<OneOffReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const read = async () => {
    const a = await request<OneOffReviewData>(`/work-items/${encodeURIComponent(itemId)}/one-off`);
    if (a.status === 200) setData(a.body);
    else setError(detailOf(a.body));
  };
  useEffect(() => { void read(); }, [itemId]); // eslint-disable-line react-hooks/exhaustive-deps
  const changes = changedNodes(data?.provenance ?? null);
  const touched = [["Added", changes.added], ["Overridden", changes.overridden], ["Removed", changes.removed]] as const;
  return (
    <ChoiceSheet
      title="Approve this chain, then start?"
      text={data ? `This item runs a chain of its own${data.extends ? `, extending ${data.extends}` : ""}. Read what is custom in it before it starts.` : "Reading the chain…"}
      options={data?.needs_review ? [{ value: "approve", label: "Approve and start" }] : []}
      onPick={async () => {
        if (!data || busy.current) return;
        busy.current = true;
        try {
          const a = await request(`/work-items/${encodeURIComponent(itemId)}/one-off/approve`, jsonBody("POST", approveBody(data, "phone")));
          if (a.status !== 200) {
            setError(detailOf(a.body));
            if (a.status === 409) await read();
            return;
          }
          if (await start()) sheet.close();
        } finally {
          busy.current = false;
        }
      }}
      onClose={sheet.close}
    >
      {data && (
        <div className="ph-oneoff">
          {touched.map(([label, ids]) => ids.length > 0 && <p key={label} className="ph-help"><b>{label}</b> {ids.join(", ")}</p>)}
          {customRows(data.custom).map((r) => (
            <section key={r.key} className="ph-oneoff-part">
              <p className="ph-help">{r.what}</p>
              <p className="ph-target">{r.where}</p>
              <pre className="ph-start-lines">{r.text}</pre>
            </section>
          ))}
          {data.added.map((a) => <p key={a.node} className="ph-target">✓ {addedCheckText(a)}</p>)}
        </div>
      )}
      {error && <p className="ph-error" role="alert">{error}</p>}
    </ChoiceSheet>
  );
}
