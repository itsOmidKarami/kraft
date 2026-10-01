import { useState } from "react";
import { act } from "../../item/actions";
import type { ItemDetail } from "../../item/useItem";
import { ConfirmSheet, type useSheet } from "../nav/Sheet";
import { useDo } from "./useDo";

/** "Pause this item?" (the prototype's pause sheet): it asks first, says what is lost, and keeps a refusal inside the sheet. */
export function PauseSheet({ item, node, sheet, reload }: { item: ItemDetail; node: string | null; sheet: ReturnType<typeof useSheet>; reload: () => void }) {
  const { busy, run } = useDo(reload);
  const [error, setError] = useState<string | null>(null);
  return (
    <ConfirmSheet
      title="Pause this item?"
      text={`The running attempt${node ? ` on ${node}` : ""} stops now and its work is lost. Nothing runs until you resume.`}
      busy={busy}
      error={error}
      confirm={{
        label: "Pause now",
        run: async () => {
          const r = await run(act.pause(item.id), node ? `Paused at ${node}.` : "Paused.");
          if (r.ok) sheet.close();
          else setError(r.error);
        },
      }}
      onClose={sheet.close}
    />
  );
}
