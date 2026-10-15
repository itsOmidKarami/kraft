import type { WorkItem } from "../../../types";
import { admittedRowAction } from "../../board/rowText";

/** What a card's buttons are (W17 brief B.4): the button the prototype draws,
 *  chosen from the same `admittedRowAction` the desktop row uses, so the two cannot
 *  disagree about which items have a button. */
export type CardButton =
  | { kind: "review"; label: string; gate: string }
  | { kind: "answer"; label: string }
  | { kind: "open"; label: string }
  | { kind: "raise"; label: string }
  | { kind: "resume"; label: string };

export function cardButtons(item: WorkItem): CardButton[] {
  const a = admittedRowAction(item);
  if (!a) return [];
  // A gate is decided inside its review, as on the desktop row; the card only opens it.
  if (a.kind === "gate") return [{ kind: "review", label: a.label, gate: a.gate }];
  if (a.kind === "resume") return [{ kind: "resume", label: "Resume" }];
  if (item.display_status === "failed") return [{ kind: "open", label: "Retry…" }];
  switch (item.stop?.kind) {
    // As on the desktop row, which opens its peek on the editor: the item opens with the raise sheet over it when it can make
    // one (`budgetRaise`, the limit on a cap) and on its card, which says why, when it cannot. A list row carries neither the
    // stop's limit nor the item's spend, so the card cannot tell a daily or token cap, which the server refuses to raise, from the item's own.
    case "cap":
    case "budget": return [{ kind: "raise", label: a.label }];
    case "question": return [{ kind: "answer", label: "Answer…" }];
    default: return [{ kind: "open", label: a.label }];
  }
}

export const itemPath = (id: string, search = "") => `/work-items/${encodeURIComponent(id)}${search}`;
export const reviewPath = (id: string, gate: string) => `/work-items/${encodeURIComponent(id)}/review?gate=${encodeURIComponent(gate)}`;
