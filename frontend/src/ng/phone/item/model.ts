import { ago, elapsed, plural, until, usd } from "../../../format";
import type { KraftEvent, StopLimit } from "../../../types";
import { budgetRaise, headerState, archivable, NOT_RAISABLE, RELEASED } from "../../item/status";
import { gateMessage, materialized } from "../../item/chainValues";
import { taskName } from "../../item/paths";
import { FORGE_LOGIN_HINT, failedFix, keptLine } from "../../item/cause";
import { escalationsOf, ESCALATION } from "../../item/nodeGraph";
import { limitPolicy } from "../../item/limitPolicy";
import type { ItemDetail } from "../../item/useItem";
import type { ChainNode } from "../../graph/layout";

/** The item screen's decisions, pure (W17 brief C): what the state card says,
 *  which pair of buttons the bottom bar offers, what the ⋮ sheet lists, and the
 *  sub line of a chain row. Read from the server's `display_status` and `stop`
 *  only; nothing here derives a state (R16). */

export type Tone = "warn" | "bad" | "info" | "ok" | "muted";
export interface Card {
  tone: Tone;
  /** A speech bubble before the title: the card asks you something. */
  icon?: "message-square";
  title: string;
  where?: string;
  text?: string;
  /** What to do about it, when it is not a button: the forge CLI to sign in (`FORGE_LOGIN_HINT`; `backticks` mark commands). */
  hint?: string;
  /** Label, value, and for a fact that is another work item, its id: the value opens it. */
  facts: [string, string, string?][];
}

const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
const pathOf = (task?: string | null) => (task ? task.split(".").join(" › ") : "");

const spent = (item: ItemDetail): [string, string][] =>
  item.budget_cap ? [["spent", `${usd(item.budget_cap.spent_usd)}${item.budget_cap.cap_usd != null ? ` of ${usd(item.budget_cap.cap_usd)}` : ""}`]] : [];

/** "running 8h 2m of 8h": the time the item has run against its time cap, when it has one. */
const runClock = (item: ItemDetail): [string, string][] => {
  const t = item.running_time;
  return t?.cap_minutes != null ? [["running", `${elapsed(t.running_s * 1000)} of ${elapsed(t.cap_minutes * 60_000)}`]] : [];
};

/** " · thread 1, turn 2": which escalation conversation a question came from, and how far into it. */
function conversation(item: ItemDetail): string {
  const stop = item.stop;
  if (!stop?.node || taskName(stop.task ?? "") !== ESCALATION) return "";
  const turns = escalationsOf(item, stop.node);
  const last = turns.at(-1);
  return last ? ` · thread ${last.thread}, turn ${turns.filter((s) => s.thread === last.thread).length}` : "";
}

/** A budget stop's spend, against the cap that stopped it when the item can
 *  raise that cap, and to the cent, as the stop's reason prints it
 *  (`executor.stops.budget_reason`): the two never show $0.04 and $0.035. A
 *  daily or token cap's figures are in the reason itself. */
const budgetSpent = (item: ItemDetail): [string, string][] => {
  if (!item.budget_cap) return [];
  const cents = (n: number) => `$${n.toFixed(2)}`;
  const how = budgetRaise(item);
  const cap = how === "limit" ? stopLimitOf(item)!.value : how === "item" ? item.budget_cap.cap_usd : null;
  return [["spent", `${cents(item.budget_cap.spent_usd)}${cap != null ? ` of ${cents(cap)}` : ""}`]];
};

/** The words come from the desktop's StateCard, Banner and QuestionCard, so the two shapes say the same thing about the same stop. */
export function cardOf(item: ItemDetail, events: KraftEvent[] = [], fileCount: number | null = null): Card | null {
  const stop = item.stop;
  const facts = (stop?.facts ?? {}) as Record<string, unknown>;
  const where = stop ? [pathOf(stop.task) || stop.node, stop.attempt ? `attempt ${stop.attempt}` : ""].filter(Boolean).join(" · ") : undefined;
  switch (item.display_status) {
    case "failed": {
      if (!stop) return null;
      // The desktop's card, fact for fact: what the run kept, then the stop's own facts, and never `cause`, which picks the hint.
      const fs = Object.entries(facts).flatMap(([k, v]) => (k !== "cause" && str(v) ? [[k, str(v)!] as [string, string]] : []));
      const kept = keptLine(item, fileCount);
      const keptFact: [string, string][] = kept ? [["work kept", kept]] : [];
      return {
        tone: "bad", title: "Failed", where, text: stop.reason ?? undefined,
        ...(failedFix(item) === "forge_login" && { hint: FORGE_LOGIN_HINT }),
        facts: [...keptFact, ...fs.slice(0, 3 - keptFact.length), ...spent(item)],
      };
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
      return null;
    }
    case "needs_you": {
      if (!stop) return null;
      switch (stop.kind) {
        case "gate": return { tone: "warn", title: "Waiting for your approval", where: `at ${item.pending_gate ?? stop.node ?? ""}`, text: gateMessage(materialized(item), item.pending_gate ?? stop.node ?? ""), facts: spent(item) };
        case "budget": return {
          tone: "bad", title: (stop.reason ?? "The budget ran out").replace(/\.$/, ""), where: stop.node ? `at ${stop.node}` : undefined,
          text: budgetRaise(item) ? "Raising the budget resumes the item at once." : NOT_RAISABLE,
          facts: budgetSpent(item),
        };
        case "cap": return { tone: "bad", title: (stop.reason ?? "A limit was reached").replace(/\.$/, ""), where: stop.node ? `at ${stop.node}` : undefined, text: stopLimitOf(item) ? "Raise the cap to carry on, or Steer first if it should finish sooner." : "Retry runs the node again. Steer first if it should finish sooner.", facts: [...runClock(item), ...spent(item)] };
        case "question": {
          const q = item.needs_context_question;
          return { tone: "warn", icon: "message-square", title: "Needs you", where: `asked by ${stop.task ? taskName(stop.task) : "the agent"}${conversation(item)}${stop.node ? ` · on ${stop.node}` : ""}`, text: q ? `“${q}”` : (stop.reason ?? undefined), facts: [] };
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
        default: return { tone: "warn", title: stop.kind === "stuck" ? "Stuck" : "Needs you", where, text: stop.reason ?? undefined, facts: spent(item) };
      }
    }
    case "blocked": {
      const deps = item.dependencies ?? [];
      const waits = deps.filter((d) => !d.met).length;
      return {
        tone: "info", title: "Blocked", where: waits ? `waiting on ${plural(waits, "item")}` : RELEASED.where,
        text: waits ? "Kraft starts it when they complete. Unblock starts it without them." : RELEASED.text,
        facts: deps.map((d) => [d.status.replace("_", " "), d.title, d.id]),
      };
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
  | "pause" | "unblock" | "steer" | "resume" | "start" | "reject" | "review" | "raise" | "retry" | "escalate" | "answer"
  | "cancel" | "reopen-mr" | "conflicts" | "board" | "restore"
  | "repo" | "settings" | "open-mr" | "duplicate" | "archive" | "complete";
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
    case "running": return { secondary: a("pause", "Pause"), primary: a("steer", "Steer") };
    // An escalation turn runs: /pause, /steer and /escalate refuse it; a human's Retry outranks the turn (R11b-01).
    case "escalated": return { secondary: null, primary: a("retry", "Retry") };
    case "paused": return item.current_node_id ? { secondary: a("steer", "Steer"), primary: a("resume", "Resume") } : { secondary: null, primary: a("start", "Start") };
    // Kraft retries a waiting item by itself; /retry would answer it 409 (R10b-01).
    case "waiting": return { secondary: a("pause", "Pause"), primary: null };
    // Queued for a slot: Kraft starts it; Pause takes it out of the queue.
    case "queued": return { secondary: null, primary: a("pause", "Pause") };
    // Blocked behind another item: Kraft starts it; Pause takes it out, Unblock starts it without them.
    case "blocked": return { secondary: item.dependencies?.some((d) => !d.met) ? a("unblock", "Unblock") : null, primary: a("pause", "Pause") };
    case "failed": return { secondary: a("escalate", "Escalate"), primary: a("retry", "Retry") };
    case "needs_you":
      switch (stop?.kind) {
        case "gate": return { secondary: a("reject", "Reject…"), primary: a("review", "Review and decide") };
        // No Steer: a retry with one would only stop at the same cap again.
        case "budget": return { secondary: null, primary: budgetRaise(item) ? a("raise", "Raise budget") : a("retry", "Retry") };
        // Retry with no raise would only stop at the same cap again, so with the stop's limit the way on is the raise.
        case "cap": return { secondary: a("steer", "Steer"), primary: stopLimitOf(item) ? a("raise", "Raise cap") : a("retry", "Retry") };
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
  // The desktop's "Check the repo settings": a failed card whose cause the repo's settings can answer (`failedFix`).
  if (status === "failed" && failedFix(item) === "repo") out.push(a("repo", "Check the repo settings"));
  if (item.current_node_id) out.push(a("settings", "Item settings"));
  if (item.mr_ref) out.push(a("open-mr", `Open MR !${item.mr_ref.number}`));
  out.push(a("duplicate", "Duplicate"));
  if (hs.panel.includes("archive") && archivable(status)) out.push(a("archive", "Archive"));
  if (hs.panel.includes("complete")) out.push(a("complete", "Mark complete…"));
  if (hs.panel.includes("cancel") && !inBar.has("cancel")) out.push(a("cancel", "Cancel item…", true));
  return out;
}

/** A chain row's words under its id (the prototype's `sub`). `skipped`: a gate that was skipped, not decided (`gateSkipped`), which no one approved. */
export function nodeSub(n: ChainNode, skipped = false): { text: string; tone: "warn" | "info" | "bad" | "muted" } {
  const gate = n.kind === "gate";
  switch (n.state) {
    case "done": return { text: gate ? (skipped ? "skipped" : n.meta === "auto" ? "approved by an agent" : "approved") : (n.meta ?? "done"), tone: "muted" };
    case "failed": return { text: n.capped ? "stopped at the cap" : "failed", tone: "bad" };
    case "current":
      if (n.capped) return { text: "stopped at the cap", tone: "bad" };
      if (n.paused) return { text: "paused", tone: "warn" };
      if (n.sub === "needs you") return { text: gate ? "waiting for you" : "needs you", tone: "warn" };
      // A waiting node says what it waits on, as the badge does, not "running" (R11b-04).
      return { text: [n.wait ?? "running", n.sub].filter(Boolean).join(" · "), tone: "info" };
    case "plain": return { text: n.meta ?? "", tone: "muted" };
    default: return { text: gate ? "you" : "not started", tone: "muted" };
  }
}

const LIMIT_WORDS: Record<StopLimit["key"], { noun: string; unit: string; one: string; money?: true }> = {
  budget_usd: { noun: "budget cap", unit: "dollars", one: "dollar", money: true },
  time_cap_minutes: { noun: "running time cap", unit: "minutes of running time", one: "minute of running time" },
  total_time_cap_minutes: { noun: "wall-clock cap", unit: "minutes of wall-clock time", one: "minute of wall-clock time" },
  max_attempts: { noun: "attempts cap", unit: "attempts", one: "attempt" },
  timeout_minutes: { noun: "timeout", unit: "minutes per attempt", one: "minute per attempt" },
};
export const limitWords = (l: StopLimit) => LIMIT_WORDS[l.key];

/** The `stop.limit` of a cap or budget stop, or null: without it a cap stop keeps Steer and Retry, and a budget stop raises the item's own cap if that is what stopped it (`item/status`'s `budgetRaise`). */
export const stopLimitOf = (item: ItemDetail): StopLimit | null => (item.stop?.kind === "cap" || item.stop?.kind === "budget" ? (item.stop.limit ?? null) : null);

/** The PATCH body that sets one limit, keeping the rest of the item's own override (`limitPolicy`). */
export const limitPatch = (item: ItemDetail, l: StopLimit, value: number) => limitPolicy(item.policy_override, l, value);
