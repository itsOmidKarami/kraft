import { until } from "../../format";
import type { ChainNode, ItemAction, TaskProgress, WorkItem } from "../../types";
import type { GlyphKind, GlyphState } from "../graph/types";
import { admits } from "../item/status";
import { gateOf, groupOf, stopOf } from "./model";

/** What a board row says and offers (W6 brief B.4, B.5, B.7), from the
 *  server's `display_status` and `stop` and the raw row, one table each. */

type Row = Pick<WorkItem, "display_status" | "stop" | "current_node_id" | "progress" | "step" | "mr_ref" | "fallback" | "pending_gate" | "chain_definition" | "retry_at" | "queued"> & { status?: WorkItem["status"] };

const nodeOf = (i: Row) => i.stop?.node ?? i.current_node_id ?? "";

/** A node id as words for a row's tail: `merge_request` or `merge-request` reads "merge request". */
export const nodeWords = (id: string) => id.replace(/[_-]+/g, " ");

type Named = { id: string; kind?: string; artifact?: string | null };

/** What a gate decides, in words: its id without a trailing "approval" ("spec_approval" reads "spec", "local_review"
 *  "local review"), never its document, so a surface that knows only the id (VS Code) names it the same. */
export const gateWords = (g: Named | string) => {
  const n = typeof g === "string" ? { id: g } : g;
  return nodeWords(n.id.replace(/[_-]approval$/, ""));
};

/** What a reader calls a node: a gate by what it decides ("approve spec"), any other node in words. The id stays
 *  for places where the id is the point (config, the chain editor, CLI hints). */
export const displayName = (n: Named) => (n.kind === "gate" ? `approve ${gateWords(n)}` : nodeWords(n.id));

/** `displayName` for a node known by id: found in the chain for its kind and artifact, else its id in words. */
export const nameOf = (nodes: Named[] | undefined, id: string) => displayName(nodes?.find((n) => n.id === id) ?? { id });

/** `nameOf` for an id known to be a gate (`pending_gate`), whether or not the chain lists it as one. */
export const gateName = (nodes: Named[] | undefined, id: string) => displayName({ ...(nodes?.find((n) => n.id === id) ?? { id }), kind: "gate" });

export const capitalise = (s: string) => s.replace(/^./, (c) => c.toUpperCase());

/** Every sub-task of the plan done: a list with nothing left on it, not an empty or a board-only one. */
export const allDone = (p: Pick<TaskProgress, "tasks">) => !!p.tasks?.length && p.tasks.every((t) => t.state === "done");

export const WAITING_FOR_CHECKOUT = "waiting for the checkout";
/** The Queued card's words for a checkout wait, desktop and phone: the holder is its `IN_USE_BY` fact. */
export const CHECKOUT_FREES = "It starts when the checkout frees.";
export const IN_USE_BY = "in use by";
/** Why a queued item waits, when it is another in-place item's use of its checkout rather than a slot.
 *  The holder's title is quoted: in a line, where a long one ends is otherwise unclear. */
export const waitingForCheckout = (q?: { why?: unknown; held_by_title?: unknown } | null) =>
  q?.why === "checkout" ? `${WAITING_FOR_CHECKOUT}: ${q.held_by_title != null ? `“${String(q.held_by_title)}”` : "another item"} is using it` : null;

/** The start a queued request makes, in words: `self_retry` is an escalation agent's own retry. */
const QUEUED_VERB: Record<string, string> = { resume: "resume", start: "start", retry: "retry", continue: "continue past its gate", self_retry: "retry, as its escalation asked", escalate: "escalate", reply: "answer its review", follow_up: "run its agents at the stop" };
export const queuedVerbWords = (verb: string) => QUEUED_VERB[verb] ?? nodeWords(verb);

/** Up to the first full stop that ends a sentence, not one inside "$0.04". */
const firstSentence = (s: string) => s.split(/\.(?:\s|$)/)[0];

/** The reason tail after the meta: one short line per status and stop kind. */
export function reasonTail(i: Row, now = Date.now()): string {
  const node = nameOf(i.chain_definition?.nodes, nodeOf(i));
  // An item with no node to name reads without "at …" and without a leading separator.
  const where = node ? ` at ${node}` : "";
  const tail = (() => {
    switch (i.display_status) {
      case "needs_you":
        switch (i.stop?.kind) {
          case "gate": return gateName(i.chain_definition?.nodes, i.pending_gate ?? nodeOf(i));
          case "question": return `agent asks: ${(i.stop.reason ?? "").replace(/^needs_context:\s*/, "")}`.trim();
          // A cap or budget stop's reason goes on to what was not interrupted: the row keeps its first sentence.
          case "cap":
          case "budget":
          // A checkout stop goes on to what the person should do; the item page says it whole.
          case "checkout": return i.stop.reason ? firstSentence(i.stop.reason) : `waiting for you${where}`;
          default: return i.stop?.reason ?? `waiting for you${where}`;
        }
      case "failed": return `failed${where}`;
      case "paused": return i.current_node_id ? `paused${where}` : "created paused";
      case "running":
        if (i.step) return i.step.name && i.step.task ? `${i.step.index} of ${i.step.count} · ${i.step.name} › ${i.step.task}` : `${node} · step ${i.step.index} of ${i.step.count}`;
        // A finished plan (the detail keeps it past the node) has no task left to count.
        return i.progress && !allDone(i.progress) ? `${node} · task ${i.progress.current} of ${i.progress.total}` : node;
      case "waiting": {
        const at = i.stop?.resume_at ?? i.retry_at;
        // A CI wait checks again; only a rate limit retries (R11b-05).
        return at ? `${node ? `${node} · ` : ""}${i.stop?.kind === "wait" ? "next check" : "retry"} ${until(at, now)}` : `waiting${where}`;
      }
      case "queued": return waitingForCheckout(i.queued) ?? "waiting to start";
      case "blocked": return "waiting on another item";
      case "escalated": return "escalation running";
      case "done": return i.mr_ref ? `merged !${i.mr_ref.number}` : "completed";
      case "cancelled": return "cancelled";
      default: return "";
    }
  })();
  const on = fallbackName(i.fallback?.to);
  return on ? `${tail} · on ${on}` : tail;
}

/** What a fallback switched to: the server sends `{harness, model, effort}`, its model when it names one. */
function fallbackName(to: unknown): string {
  if (typeof to === "string") return to;
  if (!to || typeof to !== "object") return "";
  const { model, harness } = to as { model?: unknown; harness?: unknown };
  return typeof model === "string" && model ? model : typeof harness === "string" ? harness : "";
}

/** A stop kind as a filter and its Analytics row say it: the kind in words, `mr_closed` as "MR closed". */
export const stopWords = (kind: string) => (kind === "mr_closed" ? "MR closed" : nodeWords(kind));

/** What the Gate and Stop reason filters say, on the desktop's menus and the phone's sheets alike. */
export const ALL_GATES = "All gates";
export const ALL_STOPS = "All stop reasons";
export const gateChip = (label: string) => `Gate · ${label}`;
export const stopChip = (label: string) => `Stop · ${label}`;

export interface FilterOption { value: string; label: string; n: number }

/** The gates items wait at and the kinds they are stopped on, as the Gate and Stop filters list them: named by what
 *  a reader calls them (`gateName`, `stopWords`), most items first. Desktop and phone list the same. */
export function stopFilterOptions(items: WorkItem[]): { gates: FilterOption[]; stops: FilterOption[] } {
  const tally = (key: (i: WorkItem) => string, label: (id: string, i: WorkItem) => string) => {
    const m = new Map<string, FilterOption>();
    for (const i of items) {
      const v = key(i);
      if (!v) continue;
      const o = m.get(v) ?? { value: v, label: label(v, i), n: 0 };
      o.n += 1;
      m.set(v, o);
    }
    return [...m.values()].sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
  };
  return {
    gates: tally(gateOf, (id, i) => gateName(i.chain_definition?.nodes, id)),
    stops: tally(stopOf, stopWords),
  };
}

export type StopCue = "failed" | "paused" | "gate" | "question" | "limit" | "other";

/** Which kind of stop a Needs you row is, for a cue that is not colour: an icon and a weight beside the reason. */
export function stopCueOf(i: Pick<Row, "display_status" | "stop">): StopCue {
  if (i.display_status === "failed") return "failed";
  if (i.display_status === "paused") return "paused";
  switch (i.stop?.kind) {
    case "gate": return "gate";
    case "question": return "question";
    case "cap": case "budget": return "limit";
    default: return "other";
  }
}

/** A row's action button: a gate is decided in its review, so it stays outlined; retry, resume and the rest are the filled one. */
export const actionVariant = (a: Pick<RowAction, "kind">): "primary" | "secondary" => (a.kind === "gate" ? "secondary" : "primary");

export type RowAction =
  | { label: string; kind: "gate"; gate: string }
  | { label: string; kind: "peek"; tab: "overview" | "config"; budget?: boolean }
  | { label: string; kind: "resume" };

/** The one action a row offers, or none. */
export function rowAction(i: Row): RowAction | null {
  if (i.display_status === "failed") return { label: "Retry…", kind: "peek", tab: "overview" };
  if (i.display_status === "paused") return i.current_node_id ? { label: "Resume", kind: "resume" } : null;
  if (i.display_status !== "needs_you") return null;
  switch (i.stop?.kind) {
    case "gate": return { label: "Review", kind: "gate", gate: i.pending_gate ?? nodeOf(i) };
    case "question": return { label: "Answer", kind: "peek", tab: "overview" };
    // Only the item's own cap names a limit (`raisable`); a node's, a gate's or the idle watchdog's has nothing to raise.
    case "cap": return i.stop.raisable || i.stop.limit ? { label: "Raise cap", kind: "peek", tab: "config" } : { label: "Retry…", kind: "peek", tab: "overview" };
    // A budget row carries neither the stop's limit nor its scope: the peek it opens picks the editor that raises this
    // cap (`budgetRaise`), or stays on the banner that says why the item can't, with Retry.
    case "budget": return { label: "Raise budget", kind: "peek", tab: "config", budget: true };
    // A stuck loop's and a checkout stop's way on is Retry, on the peek's card (R11a-01): the row said Open.
    case "stuck":
    case "checkout": return { label: "Retry…", kind: "peek", tab: "overview" };
    default: return { label: "Open", kind: "peek", tab: "overview" };
  }
}

/** `rowAction`, or null when the server's `actions` refuse its door: Resume and
 *  Answer go through `resume`; Retry… (failed, stuck, checkout) and a cap's Raise (its
 *  limit editor patches, then retries) through `retry`; a budget's Raise
 *  through `raise_budget` or `retry`, since the peek offers Retry when it
 *  can't raise; a gate's review through `approve` or `reject`. Open goes to
 *  the peek, which decides its own doors. On a server that sends no
 *  `actions`, `rowAction` as it is. `rowAction` stays as it is for the
 *  VS Code extension, which applies `actions` itself. */
export function admittedRowAction(i: Row & Pick<WorkItem, "actions">): RowAction | null {
  const a = rowAction(i);
  if (!a) return null;
  const any = (...verbs: ItemAction[]) => verbs.some((v) => admits(i, v, true));
  if (a.kind === "resume") return any("resume") ? a : null;
  if (a.kind === "gate") return any("approve", "reject") ? a : null;
  if (i.display_status === "failed" || i.stop?.kind === "stuck" || i.stop?.kind === "checkout" || i.stop?.kind === "cap") return any("retry") ? a : null;
  if (i.stop?.kind === "question") return any("resume") ? a : null;
  if (i.stop?.kind === "budget") return any("raise_budget", "retry") ? a : null;
  return a;
}

/** The row's NodeGlyph: the current node's kind and icon, its state from the status. */
export function glyphOf(i: Row): { kind: GlyphKind; icon?: string; state: GlyphState } {
  const nodes: ChainNode[] = i.chain_definition?.nodes ?? [];
  const n = nodes.find((x) => x.id === i.current_node_id) ?? nodes[nodes.length - 1];
  const kind: GlyphKind = n?.kind === "gate" ? "gate" : "exec";
  const dropped = i.display_status === "cancelled" || (i.display_status === "archived" && i.status === "abandoned");
  const finished = i.display_status === "done" || (i.display_status === "archived" && !dropped);
  const icon = dropped ? "ban" : finished ? "check" : n && (n.steps?.length ?? 0) > 1 ? "layers" : undefined;
  const state: GlyphState = (() => {
    switch (i.display_status) {
      case "failed": return "failed";
      case "escalated": return "esc";
      case "done": return "done";
      case "cancelled": return "ghost";
      case "archived": return i.status === "abandoned" ? "ghost" : "done";
      case "running": case "waiting": return "current";
      // Amber is "needs a human"; a paused item waits on nobody, so it is neutral.
      case "paused": return i.current_node_id ? "paused" : "todo";
      default: {
        const g = groupOf(i);
        return g === "needs" ? "amber" : g === "not_started" ? "todo" : "current";
      }
    }
  })();
  return { kind, icon, state };
}

/** The tick strip: one per node, gates as diamonds (Ticks.tsx draws them).
 *  `run` is the composer's preview: a node that will run. */
export type Tick = { gate: boolean; state: "done" | "current" | "hot" | "todo" | "run" };

/** A chain's nodes as the board marks them: done, the one it stands on (`hot`
 *  when it needs you), or still to run. The tick strip draws them, and so does
 *  the console's chain line (`tests/console/wording_cases.json`). */
export type ChainMark = { node: string; gate: boolean; state: "done" | "current" | "hot" | "todo" };

export function chainMarks(i: Row): ChainMark[] {
  const nodes = i.chain_definition?.nodes ?? [];
  const ended = i.display_status === "done" || i.display_status === "cancelled" || i.display_status === "archived";
  const at = nodes.findIndex((n) => n.id === i.current_node_id);
  const hot = (groupOf(i) === "needs" && i.display_status !== "paused") || i.display_status === "escalated";
  return nodes.map((n, k) => ({
    node: n.id,
    gate: n.kind === "gate",
    state: ended || (at >= 0 && k < at) ? "done" : at >= 0 && k === at ? (hot ? "hot" : "current") : "todo",
  }));
}

export function ticksOf(i: Row): Tick[] {
  return chainMarks(i).map(({ gate, state }) => ({ gate, state }));
}
