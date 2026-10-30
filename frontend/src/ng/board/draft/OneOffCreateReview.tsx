import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { OneOffParts } from "../../item/OneOffParts";
import type { CustomPart } from "../../item/oneOff";

/** Create with a one-off chain that holds custom content: every custom part in
 *  full, and the approval that goes with the digest the editor answered. */
export function OneOffCreateReview({ base, custom, busy, onApprove, onClose }: { base: string | null; custom: CustomPart[]; busy: boolean; onApprove: () => void; onClose: () => void }) {
  return (
    <Dialog title="Approve this chain, then create the item?" onClose={onClose} className="idr-review"
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" data-autofocus disabled={busy} onClick={onApprove}>Approve and create</Button>
      </>}>
      <p className="idr-sub">This item runs a chain of its own{base ? `, extending ${base}` : ""}. Its agents do what the parts below say. Read them before you approve.</p>
      <OneOffParts custom={custom} />
    </Dialog>
  );
}
