import { describe, expect, it } from "vitest";
import { detailOf, titleOf } from "./timelineHelpers";
import type { KraftEvent } from "../../types";

const ev = (over: Partial<KraftEvent>): KraftEvent =>
  ({ seq: 1, work_item_id: "w1", type: "node_started", payload: {}, created_at: "t", ...over }) as KraftEvent;

describe("timelineHelpers: judge_verdict", () => {
  it("titles a continue verdict as judge: continuing", () => {
    const e = ev({ type: "judge_verdict", payload: { verdict: "continue", reasoning: "shrinking" } });
    expect(titleOf(e, new Map())).toBe("judge: continuing");
  });

  it("titles stop_needs_human and stop_downgrade both as judge: stopped early", () => {
    const stopped = ev({ type: "judge_verdict", payload: { verdict: "stop_needs_human", reasoning: "recurring" } });
    const downgraded = ev({ type: "judge_verdict", payload: { verdict: "stop_downgrade", reasoning: "not worth it" } });
    expect(titleOf(stopped, new Map())).toBe("judge: stopped early");
    expect(titleOf(downgraded, new Map())).toBe("judge: stopped early");
  });

  it("shows the judge's reasoning as the detail line", () => {
    const e = ev({ type: "judge_verdict", payload: { verdict: "continue", reasoning: "two findings cleared" } });
    expect(detailOf(e)).toBe("two findings cleared");
  });
});
