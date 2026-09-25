import { deriveState, type ItemDisplayState } from "../../../frontend/src/deriveState";
import type { WorkItem } from "./api";

export type Announcement =
  | { kind: "gate"; id: string; gate: string }
  | { kind: "needs"; id: string; state: ItemDisplayState };

export class Announcer {
  announced = new Set<string>();
  private current = new Map<string, string>();

  constructor(private level: () => "all" | "gates" | "off") {}

  consider(item: WorkItem): Announcement | undefined {
    const { state, needsYou } = deriveState(item);
    const key = state === "gate" && item.pending_gate ? `${item.id}:gate:${item.pending_gate}` : needsYou ? `${item.id}:${state}` : undefined;
    const previous = this.current.get(item.id);
    if (key) this.current.set(item.id, key);
    else this.current.delete(item.id);
    if (!key || this.level() === "off") return undefined;
    if (state === "gate") {
      if (this.announced.has(key)) return undefined;
      this.announced.add(key);
      return { kind: "gate", id: item.id, gate: item.pending_gate! };
    }
    if (this.level() === "gates" || previous === key) return undefined;
    return { kind: "needs", id: item.id, state };
  }
}
