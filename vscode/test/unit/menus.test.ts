import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ITEM_ACTIONS } from "../../../frontend/src/types/vocab.generated";

const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));
type Entry = { command: string; when?: string };
const board: Entry[] = pkg.contributes.menus["view/item/context"];
const palette: Entry[] = pkg.contributes.menus.commandPalette;
/** The token a board entry's `when` keys off `contextValueOf`, if any. */
const token = (e: Entry) => e.when?.match(/viewItem =~ \/\\b(\w+)\\b\//)?.[1];
const keyed = board.filter((e) => token(e) !== undefined);

describe("the board's item menu", () => {
  it("keys every entry off `gate`, `worktree`, a link token or one of the server's actions", () => {
    const known = new Set<string>(["gate", "worktree", "mr", "attSpec", "attPlan", ...ITEM_ACTIONS]);
    expect(keyed.map(token).filter((t) => !known.has(t!))).toEqual([]);
  });

  it.each(["pause", "resume", "retry", "skip", "escalate", "cancel", "archive", "approve", "reject", "raise_budget", "unblock", "complete", "restore"])(
    "offers %s from exactly one entry", (action) => {
      expect(keyed.filter((e) => token(e) === action)).toHaveLength(1);
    });

  it("hides its action commands from the palette, but approve and reject on a gate document", () => {
    const acts = new Set(keyed.filter((e) => token(e) !== "gate").map((e) => e.command));
    for (const command of acts) {
      const want = ["kraft.approve", "kraft.reject"].includes(command) ? "resourceScheme == kraft-artifact" : "false";
      expect(palette).toContainEqual({ command, when: want });
    }
  });
});
