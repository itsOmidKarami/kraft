import { describe, expect, it, vi } from "vitest";
import { openMustFix } from "../../../frontend/src/ng/review/finish";
import type { ReviewThread } from "../../src/core/api";
import { approveFlow, mustFixRefusal, openMustFixThreads } from "../../src/core/mustFix";
import { C, T } from "./fixtures/thread";

const fixture = [
  T({ id: "open", label: "must_fix" }),
  T({ id: "claimed", label: "must_fix", state: "claimed" }),
  T({ id: "draft", label: "must_fix", draft: true }),
  T({ id: "resolved", label: "must_fix", state: "resolved" }),
  T({ id: "nit", label: "nit" }),
];

describe("openMustFixThreads", () => {
  it("keeps open, claimed and draft must-fix threads, not resolved ones or other labels", () =>
    expect(openMustFixThreads(fixture).map((t) => t.id)).toEqual(["open", "claimed", "draft"]));
  it("agrees with the web UI's count", () => expect(openMustFixThreads(fixture).length).toBe(openMustFix(fixture)));
});

describe("mustFixRefusal", () => {
  it("is null with none open", () => expect(mustFixRefusal([fixture[3], fixture[4]])).toBeNull());

  it("lists each thread, a draft to relabel or delete, never to resolve", () =>
    expect(mustFixRefusal([
      T({ id: "a", label: "must_fix", draft: true, file_path: "src/a.py", start_line: 3, comments: [C({ body: "fix this\nmore" })] }),
      T({ id: "b", label: "must_fix", state: "claimed", file_path: null, start_line: null, comments: [C({ body: "and this" })] }),
    ])).toEqual({
      title: "Approve refused: 2 must-fix threads are open",
      items: [
        { tid: "a", label: "src/a.py:3", description: "fix this", detail: "draft: change its label or delete it" },
        { tid: "b", label: "the whole change", description: "and this", detail: "claimed: resolve it once it is fixed" },
      ],
    }));

  it("names a file comment by its file, and says is for one", () => {
    const r = mustFixRefusal([T({ label: "must_fix", start_line: null })])!;
    expect(r.title).toBe("Approve refused: 1 must-fix thread is open");
    expect(r.items[0].label).toBe("src/a.py");
    expect(r.items[0].detail).toBe("open: resolve it once it is fixed");
  });
});

describe("approveFlow", () => {
  const run = (threads: ReviewThread[]) => {
    const order: string[] = [];
    const steps = {
      threads: vi.fn(async () => (order.push("threads"), threads)),
      refuse: vi.fn(async () => void order.push("refuse")),
      readOrSend: vi.fn(async () => (order.push("readOrSend"), "approved" as const)),
    };
    return { order, out: approveFlow(steps) };
  };

  it("refuses before any read-first fetch or send", async () => {
    const { order, out } = run([fixture[2]]);
    expect(await out).toBe("refused");
    expect(order).toEqual(["threads", "refuse"]);
  });

  it("goes on to read-first only after the threads say nothing blocks", async () => {
    const { order, out } = run([fixture[4]]);
    expect(await out).toBe("approved");
    expect(order).toEqual(["threads", "readOrSend"]);
  });
});
