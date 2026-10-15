import { gateWords } from "../../../frontend/src/ng/board/rowText";

/** User-facing lines name an item by its title and a gate in the board's words ("spec"), never by id. */

/** A click on a stale or unread render: the document opens instead of acting. */
export const readFirstMessage = (name: string, gate: string): string => `Read the ${gateWords(gate)} on ${name} first: the document is open.`;

export const notAtGate = (name: string): string => `${name} is not waiting at a gate.`;

export const gateHasNoDocument = (name: string): string => `${name}'s gate has no document to show.`;

export const skipQuestion = (name: string): string => `Skip ${name}'s current step?`;

export const cancelPrompt = (name: string): string => `Why cancel ${name}? (recorded on the item)`;

export const completePrompt = (name: string): string => `Why mark ${name} complete? (recorded on the item)`;

export const escalatePrompt = (name: string): string => `What should Kraft-Agent help with on ${name}?`;

export const budgetPrompt = (name: string, why: string): string => `${why}New spend cap for ${name} in dollars, or none`;

export const noBase = (name: string): string => `${name} has no base to compare against yet.`;

export const noChanges = (name: string): string => `${name} has no changes.`;

export const noDrafts = (name: string): string => `${name} has no draft comments to submit.`;

export const noMergeRequest = (name: string): string => `${name} has no merge request to open.`;

export const noWorktree = (name: string): string => `${name} has no worktree on disk: it was reclaimed, or the item has not run yet.`;

export const outcomeUnavailable = (label: string, name: string): string => `${label} is not available on ${name} now.`;

export const submitPlaceholder = (name: string): string => `Submit your review of ${name}`;

export const restartTitle = (name: string): string => `Request changes on ${name}: restart at`;

export const abandonQuestion = (name: string): string => `Abandon ${name}? Kraft deletes its worktree and branch.`;

export const offFileThreads = (off: number, name: string): string =>
  `${off} review thread${off === 1 ? "" : "s"} on ${name} ${off === 1 ? "is" : "are"} not on a file in this review.`;
