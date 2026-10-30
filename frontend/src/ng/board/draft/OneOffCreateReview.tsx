import { useRef } from "react";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { OneOffSummary } from "../../item/OneOffParts";
import { changedFromExtending, reviewSentence, type AddedCheck, type CustomPart } from "../../item/oneOff";
import type { Extending } from "../../templates/draft/types";

/** Create with a one-off chain that holds custom content: what it adds, overrides
 *  and removes, every custom part in full and the checks on each added node, as the
 *  Start review shows them; then the approval that goes with the digest the editor
 *  answered. A re-read that finds the chain moved shows it as it is now, and says so.
 *  `text`, `extending`, `added` and `error` come from the editor's latest answer. */
export function OneOffCreateReview({ base, custom, text, extending, added = [], error, busy, onApprove, onClose }: { base: string | null; custom: CustomPart[]; text?: string; extending?: Extending | null; added?: AddedCheck[]; error?: string | null; busy: boolean; onApprove: () => void; onClose: () => void }) {
  // What was first shown: a later answer that differs is the chain having moved under the review.
  const seen = JSON.stringify([text ?? null, custom]);
  const first = useRef(seen);
  const moved = seen !== first.current;
  return (
    <Dialog title="Approve this chain, then create the item?" onClose={onClose} className="idr-review"
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" data-autofocus disabled={busy} onClick={onApprove}>Approve and create</Button>
      </>}>
      <p className="idr-sub">{reviewSentence(base, custom.length > 0)}</p>
      <OneOffSummary changes={changedFromExtending(extending)} custom={custom} added={added} />
      {moved && <p className="idr-moved" role="alert">The chain changed while you were reviewing. This is it as it is now: read it again before you approve.</p>}
      {error && <p className="idr-moved" role="alert">{error}</p>}
    </Dialog>
  );
}
