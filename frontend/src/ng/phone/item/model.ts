import { ago, until, usd } from "../../../format";
import type { KraftEvent } from "../../../types";
import { headerState, archivable } from "../../item/status";
import { taskName } from "../../item/paths";
import type { ItemDetail } from "../../item/useItem";
import type { ChainNode } from "../../graph/layout";

/** The item screen's decisions, pure (W17 brief C): what the state card says,
 *  which pair of buttons the bottom bar offers, what the ⋮ sheet lists, and the
 *  sub line of a chain row. Read from the server's `display_status` and `stop`
 *  only; nothing here derives a state (R16). */

export type Tone = "warn" | "bad" | "info" | "ok" | "muted";
export interface Card {
  tone: Tone;
  title: string;
  where?: string;
  text?: string;
  facts: [string, string][];
}

const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
const pathOf = (task?: string | null) => (task ? task.split(".").join(" › ") : "");

const spent = (item: ItemDetail): [string, string][] =>
  item.budget_cap ? [["spent", `${usd(item.budget_cap.spent_usd)}${item.budget_cap.cap_usd != null ? ` of ${usd(item.budget_cap.cap_usd)}` : ""}`]] : [];

/** The words come from the desktop's StateCard, Banner and QuestionCard, so the two shapes say the same thing about the same stop. */
export function cardOf(item: ItemDetail, events: KraftEvent[] = []): Card | null {
  const stop = item.stop;
  const facts = (stop?.facts ?? {}) as Record<string, unknown>;
  const where = stop ? [pathOf(stop.task) || stop.node, stop.attempt ? `attempt ${stop.attempt}` : ""].filter(Boolean).join(" · ") : undefined;
  switch (item.display_status) {
    case "failed": {
      if (!stop) return null;
      const fs = Object.entries(facts).flatMap(([k, v]) => (str(v) ? [[k, str(v)!] as [string, string]] : []));
      return { tone: "bad", title: "Failed", where, text: stop.reason ?? undefined, facts: [...fs.slice(0, 3), ...spent(item)] };
    }
    case "waiting": {
      if (stop?.kind === "rate_limit") {
        const allowed = list(facts.fallback_allowed);
        const fs: [string, string][] = [];
        if (stop.resume_at) fs.push(["next retry", until(stop.resume_at)]);
        if (item.rate_limit) fs.push(["retries", `${item.rate_limit.count} of ${item.rate_limit.cap} used`]);
        if (allowed.length) fs.push(["fallback", `${allowed.join(", ")} allowed`]);
        return { tone: "info", title: "Waiting on the provider", where, text: `${str(facts.harness) ?? "The agent"} hit its rate limit. Kraft retries by itself, so nothing needs doing.`, facts: fs };
      }
      if (stop?.kind === "wait") return { tone: "info", title: "Waiting on CI", where, text: stop.reason ?? undefined, facts: stop.resume_at ? [["next check", until(stop.resume_at)]] : [] };
      if (stop && (stop.kind as string) === "worker_lost" && str(facts.last_seen_at) && str(facts.reassign_at)) {
        return {
          tone: "info", title: "Worker lost", where,
          text: `The worker running this item stopped answering ${ago(str(facts.last_seen_at))}. If it does not return, Kraft hands the item to another worker.`,
          facts: [["last seen", ago(str(facts.last_seen_at))], ["reassigns", until(str(facts.reassign_at))]],
        };
      }
      return null;
    }
    case "needs_you": {
      if (!stop) return null;
      switch (stop.kind) {
        case "gate": return { tone: "warn", title: "Waiting for your approval", where: `at ${item.pending_gate ?? stop.node ?? ""}`, facts: spent(item) };
        case "budget": return { tone: "bad", title: (stop.reason ?? "The budget ran out").replace(/\.$/, ""), where: stop.node ? `at ${stop.node}` : undefined, text: "Raising the budget resumes the item at once.", facts: spent(item) };
        case "cap": return { tone: "bad", title: (stop.reason ?? "A limit was reached").replace(/\.$/, ""), where: stop.node ? `at ${stop.node}` : undefined, text: "Retry runs the node again. Steer first if it should finish sooner.", facts: spent(item) };
        case "question": {
          const q = item.needs_context_question;
          return { tone: "warn", title: "Needs you", where: `asked by ${stop.task ? taskName(stop.task) : "the agent"}${stop.node ? ` · on ${stop.node}` : ""}`, text: q ? `“${q}”` : (stop.reason ?? undefined), facts: [] };
        }
        case "conflict": {
          const unresolved = list(facts.unresolved);
          const resolved = list(facts.resolved);
          return { tone: "warn", title: "Rebase needs you", where, text: stop.reason ?? undefined, facts: [...(unresolved.length ? [["unresolved", unresolved.join(" · ")] as [string, string]] : []), ...(resolved.length ? [["resolved", resolved.join(" · ")] as [string, string]] : []), ...spent(item)] };
        }
        case "mr_closed": {
          const ref = str(facts.ref) ?? (item.mr_ref ? String(item.mr_ref.number) : "");
          const closed = [...events].reverse().find((e) => e.type === "mr_closed");
          const by = str(closed?.payload.by);
          return {
            tone: "warn", title: `MR !${ref} was closed on the forge`,
            where: [stop.node, closed ? `closed ${by ? `by ${by} ` : ""}${ago(closed.created_at)}, not merged` : "not merged"].filter(Boolean).join(" · "),
            text: "Kraft stopped syncing the MR. The branch, the review and all findings are intact. Decide whether this work still goes ahead.", facts: [],
          };
        }
        default: return { tone: "warn", title: "Needs you", where, text: stop.reason ?? undefined, facts: spent(item) };
      }
    }
    case "escalated": return { tone: "warn", title: "Escalation running", where: item.current_node_id ? `at ${item.current_node_id}` : undefined, text: "An agent is looking at this. It resumes the run, or comes back with a question for you.", facts: [] };
    case "paused": return item.current_node_id ? { tone: "warn", title: "Paused", where: `at ${item.current_node_id}`, text: "Nothing runs until you resume. Steering resumes the item with your note.", facts: [] } : null;
    case "done": return { tone: "ok", title: "Done", where: item.mr_ref ? `MR !${item.mr_ref.number} merged` : undefined, text: "All nodes passed.", facts: spent(item) };
    case "cancelled": {
      const ev = [...events].reverse().find((e) => e.type === "work_item_cancelled");
      const at = str(ev?.payload.node_id) ?? item.current_node_id;
      return { tone: "muted", title: "Cancelled", where: at ? `at ${at}` : undefined, text: "The run stopped. Everything it produced is kept.", facts: [["reason", str(ev?.payload.reason) || "no reason given"], ...spent(item)] };
    }
    case "archived": return { tone: "muted", title: "Archived", text: "Restore it to bring it back to the board.", facts: [] };
    default: return null;
  }
}

export type ActId =
  | "pause" | "steer" | "resume" | "start" | "reject" | "review" | "raise" | "retry" | "retry-now" | "escalate" | "answer"
  | "cancel" | "reopen-mr" | "conflicts" | "board" | "restore"
  | "settings" | "open-mr" | "duplicate" | "archive" | "complete";
export interface Act {
  id: ActId;
  label: string;
  danger?: boolean;
}
const a = (id: ActId, label: string, danger = false): Act => ({ id, label, ...(danger && { danger }) });

/** The bottom bar: `secondary · primary` (or one button), by status and stop kind. A status the table does not list has no bar. An item no agent task can read a note on (`steerable: false`) has no Steer, as on the desktop's paused card (#387). */
export function pairOf(item: ItemDetail): { secondary: Act | null; primary: Act | null } {
  const p = pairTable(item);
  if (item.steerable !== false) return p;
  const rest = [p.secondary, p.primary].filter((a): a is Act => !!a && a.id !== "steer");
  return rest.length === 2 ? { secondary: rest[0], primary: rest[1] } : { secondary: null, primary: rest[0] ?? null };
}

function pairTable(item: ItemDetail): { secondary: Act | null; primary: Act | null } {
  const stop = item.stop;
  const none = { secondary: null, primary: null };
  switch (item.display_status) {
    case "running":
    case "escalated": return { secondary: a("pause", "Pause"), primary: a("steer", "Steer") };
    case "paused": return item.current_node_id ? { secondary: a("steer", "Steer"), primary: a("resume", "Resume") } : { secondary: null, primary: a("start", "Start") };
    case "waiting": return stop?.kind === "rate_limit" || stop?.kind === "wait" ? { secondary: a("pause", "Pause"), primary: a("retry-now", "Retry now") } : { secondary: a("pause", "Pause"), primary: null };
    case "failed": return { secondary: a("escalate", "Escalate"), primary: a("retry", "Retry") };
    case "needs_you":
      switch (stop?.kind) {
        case "gate": return { secondary: a("reject", "Reject…"), primary: a("review", "Review and decide") };
        case "budget": return { secondary: a("steer", "Steer"), primary: a("raise", "Raise budget") };
        case "cap": return { secondary: a("steer", "Steer"), primary: a("retry", "Retry") };
        case "question": return { secondary: a("escalate", "Escalate"), primary: a("answer", "Answer") };
        case "conflict": return { secondary: a("cancel", "Cancel…", true), primary: a("conflicts", "Review the conflicts") };
        case "mr_closed": return { secondary: a("cancel", "Cancel…", true), primary: a("reopen-mr", "Reopen MR") };
        default: return { secondary: a("escalate", "Escalate"), primary: a("retry", "Retry") };
      }
    case "done":
    case "cancelled": return { secondary: null, primary: a("board", "Back to board") };
    case "archived": return { secondary: null, primary: a("restore", "Restore") };
    default: return none;
  }
}

/** The ⋮ sheet: what the desktop's panel and card offer that the bar does not, cancel last. */
export function kebabOf(item: ItemDetail): Act[] {
  const status = item.display_status;
  const hs = headerState(item);
  const bar = pairOf(item);
  const inBar = new Set([bar.secondary?.id, bar.primary?.id]);
  const out: Act[] = [];
  if (hs.panel.includes("escalate") && !inBar.has("escalate") && status !== "paused") out.push(a("escalate", "Escalate"));
  if (item.current_node_id) out.push(a("settings", "Item settings"));
  if (item.mr_ref) out.push(a("open-mr", `Open MR !${item.mr_ref.number}`));
  out.push(a("duplicate", "Duplicate"));
  if (hs.panel.includes("archive") && archivable(status)) out.push(a("archive", "Archive"));
  if (hs.panel.includes("complete")) out.push(a("complete", "Mark complete…"));
  if (hs.panel.includes("cancel") && !inBar.has("cancel")) out.push(a("cancel", "Cancel item…", true));
  return out;
}

/** A chain row's words under its id (the prototype's `sub`). */
export function nodeSub(n: ChainNode): { text: string; tone: "warn" | "info" | "bad" | "muted" } {
  const gate = n.kind === "gate";
  switch (n.state) {
    case "done": return { text: gate ? (n.meta === "auto" ? "approved by an agent" : "approved") : (n.meta ?? "done"), tone: "muted" };
    case "failed": return { text: n.capped ? "stopped at the cap" : "failed", tone: "bad" };
    case "current":
      if (n.capped) return { text: "stopped at the cap", tone: "bad" };
      if (n.paused) return { text: "paused", tone: "warn" };
      if (n.sub === "needs you") return { text: gate ? "waiting for you" : "needs you", tone: "warn" };
      return { text: ["running", n.sub].filter(Boolean).join(" · "), tone: "info" };
    case "plain": return { text: n.meta ?? "", tone: "muted" };
    default: return { text: gate ? "you" : "not started", tone: "muted" };
  }
}

/** The limit a cap stop hit (R73): where it is set, how much it is, and the policy ceiling. Present only when the server says; without it a cap stop keeps Steer and Retry. */
export interface StopLimit {
  /** "" is item-wide; otherwise a node id (a fix loop). */
  path: string;
  key: "time_cap_minutes" | "total_time_cap_minutes" | "max_attempts" | "timeout_minutes";
  value: number;
  maximum: number | null;
}

const LIMIT_WORDS: Record<StopLimit["key"], { noun: string; unit: string }> = {
  time_cap_minutes: { noun: "running time cap", unit: "minutes of running time" },
  total_time_cap_minutes: { noun: "wall-clock cap", unit: "minutes of wall-clock time" },
  max_attempts: { noun: "attempts cap", unit: "attempts" },
  timeout_minutes: { noun: "timeout", unit: "minutes per attempt" },
};
export const limitWords = (l: StopLimit) => LIMIT_WORDS[l.key];

/** The `stop.limit` of a cap stop, or null: absent, or not the shape the server sends. */
export function stopLimitOf(item: ItemDetail): StopLimit | null {
  const l = (item.stop as { limit?: Partial<StopLimit> } | null)?.limit;
  if (item.stop?.kind !== "cap" || !l || typeof l.value !== "number" || typeof l.key !== "string" || !(l.key in LIMIT_WORDS)) return null;
  return { path: typeof l.path === "string" ? l.path : "", key: l.key as StopLimit["key"], value: l.value, maximum: typeof l.maximum === "number" ? l.maximum : null };
}

/** The PATCH body that sets one limit: item-wide under `policy`, a node's under `policy.paths`. */
export const limitPatch = (l: StopLimit, value: number) => ({ policy: l.path ? { paths: { [l.path]: { [l.key]: value } } } : { [l.key]: value } });
