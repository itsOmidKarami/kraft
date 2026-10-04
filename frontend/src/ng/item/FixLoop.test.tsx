import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChainNode, WorkerSession } from "../../types";
import { detail, LOOPED, stubFetch, V1 } from "./testkit";
import { usePaneMemory, Workspace } from "./Workspace";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));

const nodes: ChainNode[] = V1.map((n) => (n.id === "verification" ? { ...n, fix_loop: "verification.fix_loop" } : n));
let n = 0;
const sess = (hook_point: string, over: Partial<WorkerSession> = {}) =>
  ({ id: `s${n++}`, node_id: "verification", hook_point, status: "done", attempt: 1, round: 0, thread: 1, created_at: `2026-09-13T09:${String(n).padStart(2, "0")}:00Z`, started_at: "2026-09-13T10:07:00Z", exited_at: null, wall_ms: 240_000, model: "sonnet", tokens_in: 1, tokens_out: 1, cost_usd: 0.1, head_sha: "abc", ...over }) as WorkerSession;
/** Round 1 failed, a repair (310s), round 2 failed, the judge after it, and the second repair running. */
const run = (extra: WorkerSession[] = []) => [
  sess("verification.checks.lint", { round: 0, status: "failed" }), sess("verification.review.code_review", { round: 0 }),
  sess("verification.fix_loop.main.repair", { round: 1, wall_ms: 310_000 }),
  sess("verification.checks.lint", { round: 1, status: "failed", attempt: 2 }), sess("verification.review.code_review", { round: 1, attempt: 2, wall_ms: 90_000 }),
  sess("verification.fix_loop.judge", { round: 1, wall_ms: 31_000 }),
  sess("verification.fix_loop.main.repair", { round: 2, status: "running", wall_ms: null, attempt: 2, started_at: new Date(Date.now() - 41_000).toISOString() }),
  ...extra,
];
const item = (sessions = run()) => detail({ chain_definition: { template_id: "default", nodes }, materialized_chain: LOOPED, worker_sessions: sessions });
const mount = (path: string, it = item()) => {
  stubFetch({ "GET /work-items/w1/events": [200, [{ seq: 1, work_item_id: "w1", type: "judge_verdict", node_id: "verification", payload: { node_id: "verification", cycle: 1, verdict: "continue" }, created_at: "2026-09-13T09:30:00Z" }]], "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [] }] });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/work-items/:id/nodes/:node" element={<Workspace item={it} version="1" reload={() => {}} />} /></Routes>
    </MemoryRouter>,
  );
};
const pane = (name: string) => screen.getByRole("complementary", { name: `${name} pane` });
const sub = (name: string) => pane(name).querySelector(".pane-sub")!.textContent;
const canvas = () => screen.getByRole("group", { name: "verification" });
const pickRound = async (round: number) => {
  await userEvent.click(screen.getByRole("button", { name: /^round \d/ }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: new RegExp(`^Round ${round}`) }));
};

describe("a fix-loop node", () => {
  it("opens on the newest round: the canvas, the picker and the pane's subtitle say round 2 of 3, with no attempt pill", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(screen.getByRole("button", { name: "round 2 of 3 · latest" })).toBeInTheDocument();
    expect(within(canvas()).getByRole("button", { name: /^code_review/ })).toHaveAccessibleName("code_review, agent task, done");
    expect(sub("code_review")).toBe("agent task · round 2 of 3 · done 1m");
    expect(within(pane("code_review")).queryByRole("button", { name: /^attempt/ })).toBeNull();
  });

  it("opens the repair and the judge from the arc, with the round they sit between or after", async () => {
    mount("/work-items/w1/nodes/verification");
    await userEvent.click(within(canvas()).getByRole("button", { name: /^repair/ }));
    expect(sub("repair")).toMatch(/^fix-loop repair · between rounds 2 and 3 · running \d+s$/);
    // The crumb names the loop, and is no link: there is no step of that name to open.
    expect(within(pane("repair")).getByText("fix_loop").closest("button")).toBeNull();
    await userEvent.click(within(canvas()).getByRole("button", { name: /^judge/ }));
    expect(sub("judge")).toBe("fix-loop judge · after round 2 · done 31s");
  });

  it("follows the round picked: round 1's tasks, its repair, and a judge that skipped it", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.fix_loop.judge");
    expect(sub("judge")).toBe("fix-loop judge · after round 2 · done 31s");
    await pickRound(1);
    expect(screen.getByRole("button", { name: /^round 1 of 3$/ })).toBeInTheDocument();
    expect(sub("judge")).toBe("fix-loop judge · after round 1 · skipped · the first repair runs without the judge");
    expect(within(canvas()).getByRole("button", { name: /^repair/ })).toHaveAccessibleName("repair, fix-loop repair agent task, done");
    expect(within(canvas()).getByRole("button", { name: /^judge/ })).toHaveAccessibleName("judge, fix-loop judge agent task, not started");
    // `latest ↩` is back to the newest round.
    await userEvent.click(screen.getByRole("button", { name: /latest ↩/ }));
    expect(sub("judge")).toBe("fix-loop judge · after round 2 · done 31s");
  });

  it("says a repair or judge a round did not run was not run in it, and offers it no Skip", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.fix_loop.judge");
    await pickRound(1);
    expect(within(screen.getByRole("tabpanel")).getByText("Not run in this round.")).toBeInTheDocument();
    // `/skip` takes no path under a fix loop: the node is the one that is skipped, not its repair.
    expect(within(pane("judge")).queryByRole("button", { name: /^Skip/ })).toBeNull();
  });

  it("keeps the pick while the selection moves inside the node", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    await pickRound(1);
    await userEvent.click(within(canvas()).getByRole("button", { name: /^lint/ }));
    expect(screen.getByRole("button", { name: /^round 1 of 3$/ })).toBeInTheDocument();
    expect(sub("lint")).toBe("subprocess task · round 1 of 3 · failed");
  });

  it("shows the attempt pill only when the round itself ran the task more than once", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.checks.lint", item(run([sess("verification.checks.lint", { round: 1, attempt: 3, status: "failed", wall_ms: 5_000 })])));
    const pill = within(pane("lint")).getByRole("button", { name: "attempt 2 of 2" });
    expect(pill.closest(".pane-sub")).toHaveTextContent(/^subprocess task · round 2 of 3 · attempt 2 of 2▾ · failed$/);
    await userEvent.click(pill);
    // Just this round's two runs, counted from 1.
    expect(screen.getAllByRole("menuitemradio").map((r) => r.textContent)).toEqual(["✓Attempt 2failed · 5s", "Attempt 1failed · 4m"]);
  });

  it("says 'round 2 of 3' in the node's own fix loop row", () => {
    mount("/work-items/w1/nodes/verification");
    expect(within(pane("verification")).getByText("fix loop").nextElementSibling).toHaveTextContent("round 2 of 3");
  });
});
