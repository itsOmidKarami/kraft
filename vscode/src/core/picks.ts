import type { ItemAction, WorkItem } from "./api";
import { describe } from "./board";
import { linkTokens, type LinkToken } from "./links";
import { primaryAction } from "./primary";

/** What a row command does, named as its command id less `kraft.`: the
 *  palette asks for an item only among those it would apply to. */
export type PickVerb =
  | "pause" | "resume" | "answer" | "retry" | "cancel" | "skip" | "escalate" | "archive"
  | "raiseBudget" | "unblock" | "complete" | "restore"
  | "openArtifact" | "openWorktree" | "openInWebUi" | "showWorkerLog"
  | "openMergeRequest" | "openSpec" | "openPlan";

/** The verb's words in `Pick an item to …` and `Nothing here can … now.` */
export const PICK_TITLE: Record<PickVerb, string> = {
  pause: "pause", resume: "resume", answer: "answer", retry: "retry", cancel: "cancel", skip: "skip",
  escalate: "escalate", archive: "archive", raiseBudget: "raise a budget", unblock: "unblock",
  complete: "complete", restore: "restore", openArtifact: "open a gate document",
  openWorktree: "open a worktree", openInWebUi: "open in the web UI", showWorkerLog: "show a worker log",
  openMergeRequest: "open a merge request", openSpec: "open a spec", openPlan: "open a plan",
};

/** One quick-pick entry: the row's title, why it is where it is, and its id on the detail line. */
export const itemPick = (item: WorkItem) => ({ label: item.title, description: describe(item), detail: item.id, id: item.id });

const LINKS: Partial<Record<PickVerb, LinkToken>> = { openMergeRequest: "mr", openSpec: "attSpec", openPlan: "attPlan" };
const DOOR: Partial<Record<PickVerb, ItemAction>> = { raiseBudget: "raise_budget" };

/** The items `verb` applies to now, as their rows would offer it: the
 *  server's door for an action, the row's own fields for an open. */
export function pickableFor(verb: PickVerb, items: WorkItem[]): WorkItem[] {
  const link = LINKS[verb];
  const applies = (i: WorkItem): boolean => {
    if (link) return linkTokens(i).includes(link);
    switch (verb) {
      case "openArtifact": return !!i.pending_gate;
      case "openWorktree": return !!i.worktree_exists;
      case "answer": return primaryAction(i) === "answer";
      case "openInWebUi":
      case "showWorkerLog": return true;
      default: return !!i.actions?.includes(DOOR[verb] ?? (verb as ItemAction));
    }
  };
  return items.filter(applies);
}

/** The items waiting at a gate, the longest-waiting first. */
export const gatePicks = (items: WorkItem[]): WorkItem[] =>
  items.filter((i) => i.pending_gate).sort((a, b) => Date.parse(a.updated_at) - Date.parse(b.updated_at));

/** What Next Gate does with the gates waiting: the only one opens at once,
 *  several are asked about (longest-waiting first, as nothing is newer than
 *  what has waited longest), none is told so. */
export function nextGate(items: WorkItem[]): { open?: WorkItem; ask: WorkItem[] } {
  const ask = gatePicks(items);
  return ask.length === 1 ? { open: ask[0], ask } : { ask };
}
