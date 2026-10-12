import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ITEM_ACTIONS } from "../../../frontend/src/types/vocab.generated";
import { MUTATING } from "../../src/core/guard";
import { PRIMARY_COMMAND, primaryToken, type Primary } from "../../src/core/primary";

const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));
type Entry = { command: string; when?: string };
const board: Entry[] = pkg.contributes.menus["view/item/context"];
const palette: Entry[] = pkg.contributes.menus.commandPalette;
/** The token a board entry's `when` keys off `contextValueOf`, if any. */
const token = (e: Entry) => e.when?.match(/viewItem =~ \/\\b(\w+)\\b\//)?.[1];
const keyed = board.filter((e) => token(e) !== undefined);

describe("the board's item menu", () => {
  it("keys every entry off `gate`, `worktree`, `drafts`, a link token, a primary token or one of the server's actions", () => {
    const primary = (Object.keys(PRIMARY_COMMAND) as Primary[]).map(primaryToken);
    const known = new Set<string>(["gate", "worktree", "drafts", "mr", "attSpec", "attPlan", ...primary, ...ITEM_ACTIONS]);
    expect(keyed.map(token).filter((t) => !known.has(t!))).toEqual([]);
  });

  it.each(["pause", "resume", "retry", "skip", "escalate", "cancel", "archive", "approve", "reject", "raise_budget", "unblock", "complete", "restore"])(
    "offers %s from exactly one entry", (action) => {
      expect(keyed.filter((e) => token(e) === action)).toHaveLength(1);
    });

  it("offers each row command in the palette while connected, a mutating one only while writable", () => {
    // Approve and Reject act on the gate document showing; a review's drafts and its diff only from their own places.
    const gateDoc = new Set(["kraft.approve", "kraft.reject"]);
    const ownPlace = new Set(["kraft.review.submit", "kraft.review.discard", "kraft.reviewChanges"]);
    const rows = new Set(board.filter((e) => e.when?.startsWith("view == kraft.board")).map((e) => e.command));
    const want = (command: string) =>
      gateDoc.has(command) ? "resourceScheme == kraft-artifact || kraft.artifactPreview"
        : ownPlace.has(command) ? "false"
          : MUTATING.has(command) ? "kraft.connected && !kraft.readOnly" : "kraft.connected";
    expect([...rows].map((command) => palette.find((e) => e.command === command)))
      .toEqual([...rows].map((command) => ({ command, when: want(command) })));
  });
});

describe("Submit Review and Discard Review Comments", () => {
  it.each(["kraft.review.submit", "kraft.review.discard"])("%s shows on a board row only with drafts, and never read-only", (command) => {
    const e = board.find((m) => m.command === command)!;
    expect(token(e)).toBe("drafts");
    expect(e.when).toContain("!kraft.readOnly");
  });

  it("Submit shows in a review document's title bar only when its item has drafts", () => {
    const e = (pkg.contributes.menus["editor/title"] as Entry[]).find((m) => m.command === "kraft.review.submit")!;
    expect(e.when).toContain("kraft.review.activeHasDrafts");
    expect(e.when).toContain("!kraft.readOnly");
  });
});
