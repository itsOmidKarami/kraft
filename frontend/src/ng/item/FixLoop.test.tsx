import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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
const run = () => [
  sess("verification.checks.lint", { round: 0, status: "failed" }), sess("verification.review.code_review", { round: 0 }),
  sess("verification.fix_loop.main.repair", { round: 1, wall_ms: 310_000 }),
  sess("verification.checks.lint", { round: 1, status: "failed", attempt: 2 }), sess("verification.review.code_review", { round: 1, attempt: 2, wall_ms: 90_000 }),
  sess("verification.fix_loop.judge", { round: 1, wall_ms: 31_000 }),
  sess("verification.fix_loop.main.repair", { round: 2, status: "running", wall_ms: null, attempt: 2, started_at: new Date(Date.now() - 41_000).toISOString() }),
];
const item = (sessions = run()) => detail({ chain_definition: { template_id: "default", nodes }, materialized_chain: LOOPED, worker_sessions: sessions });
const mount = (path: string, it = item()) => {
  stubFetch({ "GET /work-items/w1/events": [200, [{ seq: 1, work_item_id: "w1", type: "judge_verdict", node_id: "verification", payload: { node_id: "verification", cycle: 1, verdict: "continue" }, created_at: "2026-09-13T09:30:00Z" }]], "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [] }] });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/work-items/:id/nodes/:node" element={<><Workspace item={it} version="1" reload={() => {}} /><Where /></>} /></Routes>
    </MemoryRouter>,
  );
};
function Where() { const l = useLocation(); return <output data-testid="where">{l.pathname + l.search}</output>; }
const where = () => screen.getByTestId("where").textContent;
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
    // The chain ran the node once: no pass to tell apart, so none is named.
    expect(screen.queryByRole("button", { name: /^pass \d/ })).toBeNull();
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

  it("gives a loop step of several tasks its own pane: selected from the canvas, it opens there and lists its tasks", async () => {
    // One loop step, `mend`, whose two tasks run together: both repaired after round 1, and one is at it again after round 2.
    const frozen = JSON.parse(LOOPED);
    frozen.chain.nodes[2].fix_loop = { max_attempts: 2, steps: [{ id: "mend", tasks: [{ id: "fmt", kind: "agent" }, { id: "deps", kind: "agent" }] }], judge: { id: "judge", kind: "agent" } };
    const sessions = [
      sess("verification.checks.lint", { status: "failed" }), sess("verification.review.code_review"),
      sess("verification.fix_loop.mend.fmt", { round: 1 }), sess("verification.fix_loop.mend.deps", { round: 1 }),
      sess("verification.checks.lint", { round: 1, status: "failed", attempt: 2 }), sess("verification.review.code_review", { round: 1, attempt: 2 }),
      sess("verification.fix_loop.mend.fmt", { round: 2, status: "running", wall_ms: null, attempt: 2 }),
    ];
    mount("/work-items/w1/nodes/verification", detail({ chain_definition: { template_id: "default", nodes }, materialized_chain: JSON.stringify(frozen), worker_sessions: sessions }));
    const open = () => within(canvas()).queryByRole("group", { name: "mend, tasks in parallel" });
    await userEvent.click(within(canvas()).getByRole("button", { name: /^mend, fix-loop step of 2 parallel tasks/ }));
    expect(sub("mend")).toBe("fix-loop step · between rounds 2 and 3 · running");
    expect(within(pane("mend")).getByText("2, dispatched together")).toBeInTheDocument();
    // Nothing under a fix loop is configured or retried by its own path: no Config tab and no footer.
    expect(within(pane("mend")).queryByRole("tab")).toBeNull();
    expect(within(pane("mend")).queryByRole("button", { name: /^(Retry|Skip|Pause)/ })).toBeNull();
    await waitFor(() => expect(open()).not.toBeNull());
    // Folded by its own close it stays the selection; a row of the pane then opens that task, and the step with it.
    await userEvent.click(within(canvas()).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(open()).toBeNull());
    expect(sub("mend")).toMatch(/^fix-loop step/);
    await userEvent.click(within(pane("mend")).getByRole("button", { name: /deps/ }));
    expect(sub("deps")).toMatch(/^fix-loop repair · between rounds 2 and 3/);
    expect(await within(canvas()).findByRole("button", { name: /^deps/ })).toHaveAttribute("aria-pressed", "true");
    // The task's crumbs lead back to its step, whose pane is one round's, as its tasks' are: round 1's repair is over.
    await pickRound(1);
    await userEvent.click(within(pane("deps")).getByRole("button", { name: "mend" }));
    expect(sub("mend")).toBe("fix-loop step · between rounds 1 and 2 · done");
    expect(within(canvas()).getByRole("button", { name: /^mend,/, expanded: true })).toHaveAttribute("aria-pressed", "true");
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

  it.each([
    ["verification.fix_loop.judge", "judge", "fix-loop judge · after round 2 · not yet"],
    ["verification.fix_loop.main.repair", "repair", "fix-loop repair · between rounds 2 and 3 · not yet"],
  ])("says of the newest round's %s what its box on the canvas says: it may yet run", (sel, name, want) => {
    // Round 2 has measured, and neither its judge nor the repair after it has started.
    mount(`/work-items/w1/nodes/verification?sel=${sel}`, item(run().slice(0, 5)));
    expect(sub(name)).toBe(want);
    expect(within(screen.getByRole("tabpanel")).getByText("Not yet.")).toBeInTheDocument();
    expect(within(canvas()).getByRole("button", { name: new RegExp(`^${name}`) })).toHaveTextContent("not yet");
  });

  it("keeps the pick while the selection moves inside the node", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    await pickRound(1);
    await userEvent.click(within(canvas()).getByRole("button", { name: /^lint/ }));
    expect(screen.getByRole("button", { name: /^round 1 of 3$/ })).toBeInTheDocument();
    expect(sub("lint")).toBe("subprocess task · round 1 of 3 · failed");
  });

  it("shows the attempt pill only when the round itself ran the task more than once", async () => {
    // The third run of the lint belongs to round 2, so it happened before that round's repair.
    const all = run();
    all.push(sess("verification.checks.lint", { round: 1, attempt: 3, status: "failed", wall_ms: 5_000, created_at: all[3].created_at }));
    mount("/work-items/w1/nodes/verification?sel=verification.checks.lint", item(all));
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

describe("a node the chain ran again", () => {
  // Two rounds, then local_review rejected and sent the chain back: the node's second pass is on its first round.
  const again = () => item([
    ...run().slice(0, 6).map((x) => ({ ...x, pass: 1 })),
    sess("verification.checks.lint", { pass: 2, attempt: 3, wall_ms: 12_000 }), sess("verification.review.code_review", { pass: 2, attempt: 3, status: "running", wall_ms: null }),
  ]);
  const twice = () => ({ ...again(), node_passes: { verification: [{ pass: 1 }, { pass: 2, reason: "reject" as const, gate: "local_review" }] } });
  const pickPass = async (pass: number) => {
    await userEvent.click(screen.getByRole("button", { name: /^pass \d/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: new RegExp(`^Pass ${pass}`) }));
  };

  it("opens on the newest pass, which counts its rounds and its attempts on its own", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.checks.lint", twice());
    expect(screen.getByRole("button", { name: "pass 2 of 2 · latest" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "round 1 of 3 · latest" })).toBeInTheDocument();
    expect(sub("lint")).toBe("subprocess task · round 1 of 3 · done 12s");
    // The passes say what started them, newest first.
    await userEvent.click(screen.getByRole("button", { name: /^pass \d/ }));
    expect(screen.getAllByRole("menuitemradio").map((r) => r.textContent)).toEqual(["Pass 2 · nowafter a reject at local_review", "Pass 1first run"]);
  });

  it("follows the pass picked: its rounds, its tasks and their runs, and back to the newest", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review", twice());
    expect(sub("code_review")).toMatch(/^agent task · round 1 of 3 · running/);
    await pickPass(1);
    expect(screen.getByRole("button", { name: /^pass 1 of 2$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "round 2 of 3 · latest" })).toBeInTheDocument();
    expect(sub("code_review")).toBe("agent task · round 2 of 3 · done 1m");
    expect(within(canvas()).getByRole("button", { name: /^lint/ })).toHaveAccessibleName("lint, subprocess task, failed");
    await pickRound(1);
    expect(sub("code_review")).toBe("agent task · round 1 of 3 · done 4m");
    // Another pass has its own rounds: the round picked in this one does not carry over.
    await pickPass(2);
    expect(sub("code_review")).toMatch(/^agent task · round 1 of 3 · running/);
    await pickPass(1);
    expect(screen.getByRole("button", { name: "round 2 of 3 · latest" })).toBeInTheDocument();
  });

  it("says in a retry's confirm that it is not the earlier pass on screen that runs again", async () => {
    const stopped = { ...twice(), display_status: "needs_you" as const, stop: { kind: "stuck" as const, node: "verification", resume_at: null, reason: null }, worker_sessions: twice().worker_sessions.map((x) => ({ ...x, status: x.status === "running" ? ("failed" as const) : x.status })) };
    mount("/work-items/w1/nodes/verification", stopped);
    const confirm = async () => {
      await userEvent.click(within(pane("verification")).getByRole("button", { name: "Retry" }));
      return screen.getByRole("group", { name: "Retry verification" });
    };
    // On the pass the node is on there is nothing to say.
    expect(within(await confirm()).queryByText(/You are reading pass/)).toBeNull();
    await userEvent.click(within(screen.getByRole("group", { name: "Retry verification" })).getByRole("button", { name: "Cancel" }));
    await pickPass(1);
    expect(within(await confirm()).getByText("You are reading pass 1. The retry runs on the node as it stands now, and pass 1 stays as it is.")).toBeInTheDocument();
  });

  it("says nothing of a pass in the retry of another node's task, which a link can select under this node's view", async () => {
    const base = twice();
    const stopped = { ...base, display_status: "needs_you" as const, stop: { kind: "stuck" as const, node: "verification", resume_at: null, reason: null }, worker_sessions: [sess("plan.write.plan", { node_id: "plan" }), ...base.worker_sessions.map((x) => ({ ...x, status: x.status === "running" ? ("failed" as const) : x.status }))] };
    mount("/work-items/w1/nodes/verification?sel=plan.write.plan", stopped);
    await pickPass(1);
    await userEvent.click(within(pane("plan")).getByRole("button", { name: "Retry" }));
    expect(within(screen.getByRole("group", { name: "Retry plan.write.plan" })).queryByText(/You are reading pass/)).toBeNull();
  });

  it("opens on the pass and the round its link names, as a link from the phone has them", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&round=1&pass=1", twice());
    expect(screen.getByRole("button", { name: /^pass 1 of 2$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^round 1 of 3$/ })).toBeInTheDocument();
    expect(sub("code_review")).toBe("agent task · round 1 of 3 · done 4m");
  });

  it("opens on the newest when its link names a pass or a round the node does not have", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.checks.lint&round=7&pass=9", twice());
    expect(screen.getByRole("button", { name: "pass 2 of 2 · latest" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "round 1 of 3 · latest" })).toBeInTheDocument();
  });

  it("keeps the pass and the round picked in the URL: there while the selection moves, gone on the newest and on another node", async () => {
    const AT = "/work-items/w1/nodes/verification";
    mount(`${AT}?sel=verification.review.code_review`, twice());
    await pickPass(1);
    expect(where()).toBe(`${AT}?sel=verification.review.code_review&pass=1`);
    await pickRound(1);
    expect(where()).toBe(`${AT}?sel=verification.review.code_review&round=1&pass=1`);
    await userEvent.click(within(canvas()).getByRole("button", { name: /^lint/ }));
    expect(where()).toBe(`${AT}?sel=verification.checks.lint&round=1&pass=1`);
    // The newest round of the pass is no round in the URL; another pass has its own rounds.
    await userEvent.click(screen.getAllByRole("button", { name: /latest ↩/ }).at(-1)!);
    expect(where()).toBe(`${AT}?sel=verification.checks.lint&pass=1`);
    await pickRound(1);
    await pickPass(2);
    expect(where()).toBe(`${AT}?sel=verification.checks.lint`);
    // Another node's view starts on its own newest.
    await pickPass(1);
    await userEvent.click(within(screen.getByRole("group", { name: "Chain" })).getAllByRole("button")[0]);
    expect(where()).toBe("/work-items/w1/nodes/plan");
  });

  it("names the pass in the node's own pane, and offers no setting of a node that has run", async () => {
    mount("/work-items/w1/nodes/verification?tab=config", twice());
    expect(sub("verification")).toMatch(/^exec node · running/);
    await pickPass(1);
    // The node's state now is its newest pass's: an earlier pass says which it is.
    expect(sub("verification")).toBe("exec node · pass 1 of 2");
    // Overrides are set before an item starts. Reading an earlier pass does not make it one that has not.
    expect(within(pane("verification")).queryByRole("button", { name: /override|Reset/i })).toBeNull();
    expect(within(pane("verification")).queryByRole("combobox")).toBeNull();
    expect(within(pane("verification")).queryByRole("spinbutton")).toBeNull();
  });
});
