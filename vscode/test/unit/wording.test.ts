import { expect, it } from "vitest";
import * as w from "../../src/core/wording";

// Every line names the item by its title, and a gate in the board's words.
it.each([
  ["read first", w.readFirstMessage("Add cache", "spec_approval"), "Read the spec on Add cache first: the document is open."],
  ["not at a gate", w.notAtGate("Add cache"), "Add cache is not waiting at a gate."],
  ["no gate document", w.gateHasNoDocument("Add cache"), "Add cache's gate has no document to show."],
  ["skip", w.skipQuestion("Add cache"), "Skip Add cache's current step?"],
  ["cancel", w.cancelPrompt("Add cache"), "Why cancel Add cache? (recorded on the item)"],
  ["complete", w.completePrompt("Add cache"), "Why mark Add cache complete? (recorded on the item)"],
  ["escalate", w.escalatePrompt("Add cache"), "What should Kraft-Agent help with on Add cache?"],
  ["budget", w.budgetPrompt("Add cache", "Cap hit. "), "Cap hit. New spend cap for Add cache in dollars, or none"],
  ["no base", w.noBase("Add cache"), "Add cache has no base to compare against yet."],
  ["no changes", w.noChanges("Add cache"), "Add cache has no changes."],
  ["no drafts", w.noDrafts("Add cache"), "Add cache has no draft comments to submit."],
  ["no merge request", w.noMergeRequest("Add cache"), "Add cache has no merge request to open."],
  ["no worktree", w.noWorktree("Add cache"), "Add cache has no worktree on disk: it was reclaimed, or the item has not run yet."],
  ["outcome unavailable", w.outcomeUnavailable("Approve", "Add cache"), "Approve is not available on Add cache now."],
  ["submit placeholder", w.submitPlaceholder("Add cache"), "Submit your review of Add cache"],
  ["restart title", w.restartTitle("Add cache"), "Request changes on Add cache: restart at"],
  ["abandon", w.abandonQuestion("Add cache"), "Abandon Add cache? Kraft deletes its worktree and branch."],
  ["one off-file thread", w.offFileThreads(1, "Add cache"), "1 review thread on Add cache is not on a file in this review."],
  ["off-file threads", w.offFileThreads(2, "Add cache"), "2 review threads on Add cache are not on a file in this review."],
])("%s", (_, got, want) => expect(got).toBe(want));
