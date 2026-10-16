import { useState } from "react";
import { nameOf } from "../../board/rowText";
import { act } from "../../item/actions";
import type { ItemDetail } from "../../item/useItem";
import { ConfirmSheet, type useSheet } from "../nav/Sheet";
import { useDo } from "./useDo";
import { PAUSE_THIS_ITEM } from "../../board/sharedWords";
import { REMOVE_FROM_QUEUE, REMOVE_FROM_QUEUE_TEXT, REMOVE_FROM_QUEUE_TITLE } from "../../item/sharedWords";

/** PAUSE_THIS_ITEM (the prototype's pause sheet): it asks first, says what is lost, and keeps a refusal inside the sheet. */
export function PauseSheet({ item, node, sheet, reload }: { item: ItemDetail; node: string | null; sheet: ReturnType<typeof useSheet>; reload: () => void }) {
  const { busy, run } = useDo(reload);
  const at = node ? nameOf(item.chain_definition.nodes, node) : null;
  const [error, setError] = useState<string | null>(null);
  const queued = item.display_status === "queued";
  return (
    <ConfirmSheet
      title={queued ? REMOVE_FROM_QUEUE_TITLE : PAUSE_THIS_ITEM}
      text={queued ? REMOVE_FROM_QUEUE_TEXT : `The running attempt${node ? ` on ${at}` : ""} stops now and its work is lost. Nothing runs until you resume.`}
      busy={busy}
      error={error}
      confirm={{
        label: queued ? REMOVE_FROM_QUEUE : "Pause now",
        run: async () => {
          const r = await run(act.pause(item.id), queued ? "Removed from the queue." : node ? `Paused at ${at}.` : "Paused.");
          if (r.ok) sheet.close();
          else setError(r.error);
        },
      }}
      onClose={sheet.close}
    />
  );
}
