import { describe, expect, it } from "vitest";
import type { ReviewThread } from "../../types";
import { inPageOrder, stepThread, threadPosition } from "./model";

const t = (id: string, file_path: string | null, end_line: number | null, state = "open") => ({ id, file_path, end_line, state }) as ReviewThread;
// As the page draws them: the whole change's, then b.py before a.py (the tree's order), a file's own above its lines, then gone.py.
const THREADS = [t("gone", "gone.py", 3), t("a5", "a.py", 5), t("b-top", "b.py", null), t("whole", null, null), t("b2", "b.py", 2, "resolved"), t("b9", "b.py", 9, "claimed")];
const ORDERED = inPageOrder(THREADS, ["b.py", "a.py"]);

describe("stepping through unresolved threads", () => {
  it("orders threads as the page draws them", () => {
    expect(ORDERED.map((x) => x.id)).toEqual(["whole", "b-top", "b2", "b9", "a5", "gone"]);
  });

  it.each([
    ["next from nowhere is the first", null, 1, "whole", false],
    ["previous from nowhere is the last", null, -1, "gone", false],
    ["next skips a resolved thread", "b-top", 1, "b9", false],
    ["from a thread since resolved, goes on from where it sits", "b2", -1, "b-top", false],
    ["past the last goes round to the first", "gone", 1, "whole", true],
    ["before the first goes round to the last", "whole", -1, "gone", true],
  ])("%s", (_, at, dir, to, wrapped) => {
    expect(stepThread(ORDERED, at, dir as 1 | -1)).toEqual({ thread: expect.objectContaining({ id: to }), wrapped });
  });

  it("counts only unresolved threads, and goes nowhere when there are none", () => {
    expect(threadPosition(ORDERED, "b9")).toEqual({ at: 3, total: 5 });
    expect(threadPosition(ORDERED, "b2")).toEqual({ at: null, total: 5 });
    expect(stepThread([t("x", "a.py", 1, "resolved")], null, 1)).toBeNull();
  });
});
