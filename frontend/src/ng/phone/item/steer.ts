import type { ItemDetail } from "../../item/useItem";

/** Which door a steer goes through, React-free so the item model and its tests
 *  read it without loading the composer. */

export const running = (item: ItemDetail) => item.display_status === "running";
/** A stop no resume takes (a cap, a stuck loop): `/steer` and `/resume` answer it 409, and `/retry` carries the note instead. */
export const stopped = (item: ItemDetail) => item.display_status === "failed" || (item.display_status === "needs_you" && item.stop?.kind !== "question");

/** The door a steer goes through: a running item's pause then resume, a stopped one's retry, else resume (a pause, a question). */
export const steerDoor = (item: ItemDetail): "pause" | "retry" | "resume" => (stopped(item) ? "retry" : running(item) ? "pause" : "resume");
