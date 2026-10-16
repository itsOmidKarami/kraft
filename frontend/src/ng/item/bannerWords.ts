import type { WorkItem } from "../../types";
import { FORGE_LOGIN_HINT, failedFix } from "./cause";
import { budgetRaise, NOT_RAISABLE, offersRaise, RAISE_REFUSED } from "./status";

/** Which frame a stop's words sit in: a gate's "Waiting for your approval at
 *  <node>.", a cap or budget's "Stopped at <node>. <text>", a question, a
 *  failure, or any other stop that needs you. */
export type BannerKind = "gate" | "limit" | "question" | "failed" | "stop";

/** A stopped item's banner, as words both front doors draw (spec §4.5 of the
 *  console's design): the node it names, the server's reason as the web
 *  shows it, and the way on the web gives in words. `""` for none. */
export type BannerWords = { kind: BannerKind; node: string; text: string; way: string };

type BannerItem = Pick<WorkItem, "display_status" | "stop" | "pending_gate" | "needs_context_question" | "actions">;

/** A stop's reason as a sentence of its own: a capital first letter and a full stop. */
export const sentence = (s: string) => `${s.charAt(0).toUpperCase()}${s.slice(1).replace(/\.*$/, "")}.`;

/** What a failed card says to do where the cause is the repo's (`failedFix`), the button's label as a sentence. */
export const REPO_FIX = "Check the repo settings.";

/** The banner's words for an item that failed or needs you, or null for any
 *  other: `Banner` draws the gate and cap stops from it, the console every one.
 *  A failure's text is its whole reason: the console has no fact rows to move
 *  the " — tests failed: …" tail into. */
export function bannerOf(item: BannerItem): BannerWords | null {
  const stop = item.stop;
  if (!stop || (item.display_status !== "needs_you" && item.display_status !== "failed")) return null;
  const node = stop.node ?? "";
  if (item.display_status === "failed") {
    const fix = failedFix(item);
    return { kind: "failed", node, text: stop.reason ?? "", way: fix === "forge_login" ? FORGE_LOGIN_HINT : fix === "repo" ? REPO_FIX : "" };
  }
  switch (stop.kind) {
    case "gate":
      return { kind: "gate", node: item.pending_gate ?? node, text: "", way: "" };
    case "question":
      return { kind: "question", node, text: item.needs_context_question ?? (stop.reason ?? "").replace(/^needs_context:\s*/, ""), way: "" };
    case "cap":
    case "budget": {
      const text = sentence(stop.reason ?? (stop.kind === "budget" ? "The budget ran out" : "A limit was reached"));
      // A budget the item cannot raise says where it is raised instead of offering a raise.
      const way = stop.kind === "budget" && !offersRaise(item) ? (budgetRaise(item) ? RAISE_REFUSED : NOT_RAISABLE) : "";
      return { kind: "limit", node, text, way };
    }
    default:
      return { kind: "stop", node, text: stop.reason ?? "", way: "" };
  }
}
