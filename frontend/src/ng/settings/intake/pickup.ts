import { plural } from "../../../format";
import type { IntakeResolved } from "./types";
import { toMinutes } from "./units";

/** Where "beads" is explained, linked once from the desktop's pickup card. */
export const BEADS_DOCS = "https://kraft-sdlc.dev/concepts/vocabulary#beads";
/** What the area does, under its title on both trees. */
export const INTAKE_SUB = "Starts unblocked issues from your bd tracker on a timer, and files scheduled items.";

/** What a pickup sentence says (AreaIntake's `pickupSentence`). */
export function pickup(i: IntakeResolved): string {
  return i.enabled
    ? `Every ${plural(toMinutes(i.interval_s), "minute")}, starts the open issues in your bd tracker that nothing blocks, at P${i.priority_ceiling} and below, from ${i.repos.length ? i.repos.join(", ") : "every enabled repo"}. Each runs to its first gate and waits for you there.`
    : "Nothing is picked up automatically. Open issues in your bd tracker stay there until someone files them.";
}

/** What removing a schedule does, on the confirmation both screens ask. */
export const REMOVE_SCHEDULE = "It stops filing items once you publish. Items it already filed are left as they are.";
