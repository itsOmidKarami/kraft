import { reasonTail } from "../../../frontend/src/ng/board/rowText";
import type { WorkItem } from "./api";

export type Announcement =
  | { kind: "gate"; id: string; gate: string }
  | { kind: "needs"; id: string; text: string };

/** What a toast is about, or none: a gate by name, else a `needs_you` or
 *  `failed` row by its stop kind. Paused, escalated and queued items wait on
 *  nobody new, so they never toast. */
function keyOf(item: WorkItem): string | undefined {
  if (item.archived_at) return undefined;
  const d = item.display_status;
  if (d === "needs_you" && item.pending_gate) return `${item.id}:gate:${item.pending_gate}`;
  if (d === "needs_you" || d === "failed") return `${item.id}:${d}:${item.stop?.kind ?? "none"}`;
  return undefined;
}

export class Announcer {
  announced = new Set<string>();
  private current = new Map<string, string>();

  constructor(private level: () => "all" | "gates" | "off") {}

  consider(item: WorkItem): Announcement | undefined {
    // An escalation working on the stop leaves it as it was: if the item is still stuck after, that is no news.
    if (item.display_status === "escalated" && !item.archived_at) return undefined;
    const key = keyOf(item);
    const previous = this.current.get(item.id);
    if (key) this.current.set(item.id, key);
    else this.current.delete(item.id);
    if (!key || this.level() === "off") return undefined;
    if (key === `${item.id}:gate:${item.pending_gate}`) {
      if (this.announced.has(key)) return undefined;
      this.announced.add(key);
      return { kind: "gate", id: item.id, gate: item.pending_gate! };
    }
    if (this.level() === "gates" || previous === key) return undefined;
    return { kind: "needs", id: item.id, text: reasonTail(item) };
  }

  /** What to toast for `items`, already scoped to the window. `quiet` only
   *  records them: turning on Show all repos widens to stops that were
   *  waiting before, which are not news. */
  considerAll(items: WorkItem[], quiet = false): [WorkItem, Announcement][] {
    const out: [WorkItem, Announcement][] = [];
    for (const item of items) {
      const a = this.consider(item);
      if (a && !quiet) out.push([item, a]);
    }
    return out;
  }
}
