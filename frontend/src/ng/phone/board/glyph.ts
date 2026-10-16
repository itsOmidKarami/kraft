import { glyphOf } from "../../board/rowText";
import type { WorkItem } from "../../../types";

/** The icon a phone row's glyph draws for each state: what the item is doing, not the kind of node it is at (PH-11). */
const STOP_ICON: Record<string, string> = { question: "message-square", cap: "ban", budget: "ban", conflict: "git-compare", mr_closed: "git-pull-request", failed: "circle-alert" };
const STATUS_ICON: Record<string, string> = { running: "circle-dot", waiting: "clock", queued: "clock", blocked: "clock", paused: "pause", escalated: "siren", failed: "circle-alert", done: "check", cancelled: "ban" };

/** A phone row's glyph: the shape and colour state the desktop row has (a diamond at a gate), and an icon for the state in the rounded box. */
export function stateGlyph(i: WorkItem): { kind: ReturnType<typeof glyphOf>["kind"]; icon: string | undefined; state: ReturnType<typeof glyphOf>["state"] } {
  const g = glyphOf(i);
  const icon = (i.display_status === "needs_you" ? STOP_ICON[i.stop?.kind ?? ""] : undefined) ?? STATUS_ICON[i.display_status ?? ""] ?? g.icon;
  return { kind: g.kind, icon, state: g.state };
}
