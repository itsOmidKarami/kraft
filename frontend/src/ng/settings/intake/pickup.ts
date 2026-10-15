import { plural } from "../../../format";
import type { IntakeResolved } from "./types";
import { toMinutes } from "./units";

/** What a pickup sentence says (AreaIntake's `pickupSentence`). */
export function pickup(i: IntakeResolved): string {
  return i.enabled
    ? `Every ${plural(toMinutes(i.interval_s), "minute")}, starts ready beads at P${i.priority_ceiling} and below from ${i.repos.length ? i.repos.join(", ") : "every enabled repo"}. Each runs to its first gate and waits for you there.`
    : "Nothing is picked up automatically. Ready beads stay in the queue until someone files them.";
}
