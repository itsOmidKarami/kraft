import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DisplayStatus, WorkItemStop } from "../../../types";
import { acceptWrites, detail, stubFetch, type Call } from "../../item/testkit";
import { Toaster } from "../nav/Toaster";
import { Item } from "./Item";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/draft/apply", "PATCH /work-items/w1", "POST /work-items/w1/budget/raise", "POST /work-items/w1/cancel", "POST /work-items/w1/gates/plan_approval/reject", "POST /work-items/w1/pause", "POST /work-items/w1/reopen-mr", "POST /work-items/w1/resume", "POST /work-items/w1/retry", "POST /work-items/w1/unblock");

const stop = (kind: WorkItemStop["kind"], over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "verification", resume_at: null, reason: null, ...over });
const SESSION = { id: "s1", work_item_id: "w1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1, thread: 1, round: 0, created_at: "2026-09-13T09:00:00Z", started_at: "2026-09-13T09:00:00Z", exited_at: null };
const item = (status: DisplayStatus, s: WorkItemStop | null = null, over = {}) => detail({ display_status: status, stop: s, worker_sessions: [SESSION as never], budget_cap: { cap_usd: 10, source: "item", spent_usd: 10 }, ...over });

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
function mount(it: ReturnType<typeof detail>, path: string | { pathname: string; state: unknown } = "/work-items/w1", answers: Record<string, [number, unknown]> = {}) {
  const calls = stubFetch({ ...WRITES,
    "GET /work-items/w1": [200, it],
    "GET /work-items/w1/compare": [200, { files: [] }],
    "GET /work-items/w1/draft": [200, { ops: [] }],
    "GET /worker-sessions/s1/log": [200, { lines: [{ n: 1, t: "0:03", src: "agent", text: "loaded review_package", summary: "loaded review_package" }] }],
    ...answers,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/work-items/:id" element={<><Item /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
  return calls;
}
const posts = (calls: Call[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
const lastPost = (calls: Call[]) => calls.filter((c) => c.method !== "GET").at(-1);
const where = () => screen.getByLabelText("where").textContent;

afterEach(() => vi.unstubAllGlobals());

describe("the item screen (C)", () => {
  it("shows the brief, the chain with done nodes collapsed, a log preview and the pair", async () => {
    mount(item("running"));
    expect(await screen.findByRole("heading", { level: 1, name: "Design the cache" })).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2 done" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("plan_approval")).toBeNull();
    expect(screen.getByRole("button", { name: /verification/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "2 done" }));
    expect(screen.getByText("plan_approval")).toBeInTheDocument();
    expect(await screen.findByText(/loaded review_package/)).toBeInTheDocument();
    expect(screen.getByText("verification › review › code_review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Steer" })).toBeInTheDocument();
  });

  it("calls a never-started item NOT STARTED above its Start, never PAUSED", async () => {
    const calls = mount(item("paused", null, { status: "paused", current_node_id: null, worker_sessions: [] }));
    expect(await screen.findByText("NOT STARTED")).toBeInTheDocument();
    expect(screen.queryByText("PAUSED")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/resume"]));
  });

  it("before start: lists the attached spec and plan, opens Kraft's copy of one, and asks for no diff", async () => {
    const attachments = [{ kind: "spec" as const, path: "docs/specs/ws.md" }, { kind: "plan" as const, path: "docs/plans/ui.md" }];
    const calls = mount(item("paused", null, { status: "paused", current_node_id: null, worker_sessions: [], attachments }), "/work-items/w1", {
      "GET /work-items/w1/attachments/plan": [200, { title: "UI plan", path: "docs/plans/ui.md", content: "# UI plan\n\nStep **one**.", truncated: false }],
    });
    await userEvent.click(await screen.findByRole("button", { name: "plan ui.md" }));
    expect(where()).toBe("/work-items/w1?attached=plan");
    expect(await screen.findByRole("heading", { name: "UI plan" })).toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
    // A never-started item has no worktree to compare: the server would answer 409.
    expect(calls.some((c) => c.path.startsWith("/work-items/w1/compare"))).toBe(false);
  });

  // R12b-11: an archived item's worktree is gone, and the compare's 404 went to the console on every load.
  it("asks for no diff once the worktree is gone", async () => {
    const calls = mount(item("archived", null, { status: "completed", worktree_exists: false }), "/work-items/w1");
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/work-items/w1/compare"))).toBe(false);
  });

  describe("Start with a draft (R9b-01 on the phone)", () => {
    const NEVER = () => item("paused", null, { status: "paused", current_node_id: null, worker_sessions: [] });
    const DRAFT = { "GET /work-items/w1/draft": [200, { ops: [{ op: "override", path: "implementation", policy: { budget_usd: 2 }, passed: false }] }] } as Record<string, [number, unknown]>;

    it("asks first, then Apply and start applies the draft before it starts", async () => {
      const calls = mount(NEVER(), "/work-items/w1", DRAFT);
      await userEvent.click(await screen.findByRole("button", { name: "Start" }));
      const sheet = await screen.findByRole("dialog", { name: "Start with unapplied changes?" });
      expect(posts(calls)).toEqual([]);
      await userEvent.click(within(sheet).getByRole("button", { name: "Apply and start" }));
      await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/draft/apply", "POST /work-items/w1/resume"]));
    });

    it("lists the changes it would apply, as the desktop's dialog does (R10b-12)", async () => {
      mount(NEVER(), "/work-items/w1", DRAFT);
      await userEvent.click(await screen.findByRole("button", { name: "Start" }));
      const sheet = await screen.findByRole("dialog", { name: "Start with unapplied changes?" });
      expect(within(sheet).getByLabelText("Changes")).toHaveTextContent("~ implementation budget ($) → 2");
    });

    it("Start without them starts and leaves the draft", async () => {
      const calls = mount(NEVER(), "/work-items/w1", DRAFT);
      await userEvent.click(await screen.findByRole("button", { name: "Start" }));
      await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Start without them" }));
      await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/resume"]));
    });

    it("a double tap on Apply and start applies and starts once", async () => {
      const calls = mount(NEVER(), "/work-items/w1", DRAFT);
      await userEvent.click(await screen.findByRole("button", { name: "Start" }));
      const apply = within(await screen.findByRole("dialog")).getByRole("button", { name: "Apply and start" });
      // Two taps before the first apply answers.
      fireEvent.click(apply);
      fireEvent.click(apply);
      await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/draft/apply", "POST /work-items/w1/resume"]));
    });
  });

  it("a blocked item says what it waits on, and Unblock posts /unblock", async () => {
    const calls = mount(item("blocked", null, { status: "blocked", dependencies: [{ id: "a1", title: "Schema first", status: "active", met: false }] }));
    expect(await screen.findByText("Schema first")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Unblock" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/unblock"]));
    await userEvent.click(screen.getByRole("button", { name: "Schema first" }));
    expect(where()).toBe("/work-items/a1");
  });

  it("opens a node on a tap of its row", async () => {
    mount(item("running"));
    await userEvent.click(await screen.findByRole("button", { name: /verification/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification");
  });

  it("says when the item does not exist", async () => {
    mount(item("running"), "/work-items/w1", { "GET /work-items/w1": [404, { detail: "not found" }] });
    expect(await screen.findByText("This work item does not exist.")).toBeInTheDocument();
  });

  it("links to the review with the changed-file counts when the item has a diff", async () => {
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { pending_gate: "plan_approval", current_node_id: "plan_approval" }), "/work-items/w1", {
      "GET /work-items/w1/compare": [200, { files: [{ path: "a.py", insertions: 10, deletions: 2, touched_by: [], viewed: false }, { path: "b.py", insertions: 5, deletions: 1, touched_by: [], viewed: false }] }],
    });
    await userEvent.click(await screen.findByRole("button", { name: /2 files/ }));
    expect(where()).toBe("/work-items/w1/review?gate=plan_approval");
  });
});

describe("Pause (C.5)", () => {
  it("asks first, then pauses and reads the state again", async () => {
    const calls = mount(item("running"));
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    const sheet = screen.getByRole("dialog", { name: "Pause this item?" });
    expect(sheet).toHaveTextContent("The running attempt on verification stops now");
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Pause now" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/pause"]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Paused at verification.")).toBeInTheDocument();
  });

  it("a Cancel while the pause is still being answered does not let the answer close the screen under it (Kraft-9d8b2.54)", async () => {
    let answer = () => {};
    const gate = new Promise<void>((r) => (answer = r));
    stubFetch({ "GET /work-items/w1": [200, item("running")], "GET /work-items/w1/compare": [200, { files: [] }], "GET /worker-sessions/s1/log": [200, { lines: [] }] });
    const plain = (globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>);
    vi.stubGlobal("fetch", vi.fn(async (u: string, i?: RequestInit) => (String(u).endsWith("/pause") ? (await gate, new Response("{}", { status: 200 })) : plain(u, i))));
    render(
      <MemoryRouter initialEntries={["/", "/work-items/w1"]} initialIndex={1}>
        <Routes><Route path="/work-items/:id" element={<><Item /><Where /></>} /><Route path="*" element={<Where />} /></Routes>
        <Toaster />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await userEvent.click(screen.getByRole("button", { name: "Pause now" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    answer();
    expect(await screen.findByText("Paused at verification.")).toBeInTheDocument();
    expect(where()).toBe("/work-items/w1");
  });

  it("keeps a refusal inside the sheet and the sheet open", async () => {
    mount(item("running"), "/work-items/w1", { "POST /work-items/w1/pause": [409, { detail: "work item is already paused" }] });
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await userEvent.click(screen.getByRole("button", { name: "Pause now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already paused");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("Steer (C.8, R66)", () => {
  it("on a running item says it pauses, then pauses and resumes with the note, in that order", async () => {
    const calls = mount(item("running"), "/work-items/w1?compose=steer");
    expect(await screen.findByText(/Steering pauses the item\. The running attempt on verification stops now/)).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send steer" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Your note" }), "skip the flaky suite");
    await userEvent.click(send);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/work-items/w1/pause", body: {} },
      { method: "POST", path: "/work-items/w1/resume", body: { steer: "skip the flaky suite" } },
    ]));
    expect(await screen.findByText(/Steered\. verification is running again\./)).toBeInTheDocument();
  });

  it("calls nothing more when the pause is refused, and stays on the composer", async () => {
    const calls = mount(item("running"), "/work-items/w1?compose=steer", { "POST /work-items/w1/pause": [409, { detail: "work item is not running" }] });
    await userEvent.type(await screen.findByRole("textbox", { name: "Your note" }), "x");
    await userEvent.click(screen.getByRole("button", { name: "Send steer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not running");
    expect(posts(calls)).toEqual(["POST /work-items/w1/pause"]);
  });

  it("when the pause went through and the resume is refused: leaves it paused and says so", async () => {
    const calls = mount(item("running"), "/work-items/w1?compose=steer", { "POST /work-items/w1/resume": [409, { detail: "no agent task is paused" }] });
    await userEvent.type(await screen.findByRole("textbox", { name: "Your note" }), "x");
    await userEvent.click(screen.getByRole("button", { name: "Send steer" }));
    expect(await screen.findByText("Paused, but the steer was not sent: no agent task is paused")).toBeInTheDocument();
    expect(posts(calls)).toEqual(["POST /work-items/w1/pause", "POST /work-items/w1/resume"]);
  });

  it("on a paused item only resumes with the note", async () => {
    const calls = mount(item("paused"), "/work-items/w1?compose=steer");
    expect(await screen.findByText(/the item resumes with it/)).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Your note" }), "go on");
    await userEvent.click(screen.getByRole("button", { name: "Send steer" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/resume", body: { steer: "go on" } }]));
  });
});

describe("the other composers (C.7)", () => {
  const gateItem = () => item("needs_you", stop("gate", { node: "plan_approval" }), { pending_gate: "plan_approval", current_node_id: "plan_approval" });

  it("Reject names its target and needs a note", async () => {
    const calls = mount(gateItem(), "/work-items/w1?compose=reject");
    expect(await screen.findByText("Reject target: plan")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Reject with note" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "the plan skips the cache bound");
    await userEvent.click(send);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/gates/plan_approval/reject", body: { note: "the plan skips the cache bound" } }]));
  });

  it("Answer quotes the question and resumes with the answer", async () => {
    const calls = mount(item("needs_you", stop("question"), { needs_context_question: "Allow the change?" }), "/work-items/w1?compose=answer");
    expect(await screen.findByText(/Allow the change\?/)).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox"), "Allow it");
    await userEvent.click(screen.getByRole("button", { name: "Send and resume" }));
    await waitFor(() => expect(lastPost(calls)).toEqual({ method: "POST", path: "/work-items/w1/resume", body: { steer: "Allow it" } }));
  });

  // R12b-13: each opened with the focus on <body>.
  it.each(["steer", "reject", "answer", "escalate", "cancel", "complete"])("%s opens with the focus in its box", async (kind) => {
    mount(kind === "reject" ? gateItem() : item("running"), `/work-items/w1?compose=${kind}`, { "GET /work-items/w1/cancel-preview": [404, { detail: "not here" }] });
    expect(await screen.findByRole("textbox")).toHaveFocus();
  });

  it("Escalate offers a new thread only when there is an earlier one", async () => {
    mount(item("running", null, { escalation_threads: [{ thread: 1, session_id: "e", turns: 1, started_at: "x", ended_at: null, status: "done" }] }), "/work-items/w1?compose=escalate");
    expect(await screen.findByRole("switch", { name: "Start a new thread" })).toHaveAttribute("aria-checked", "false");
  });

  it("Cancel needs a reason, shows what is kept, and can close the MR", async () => {
    const calls = mount(item("running"), "/work-items/w1?compose=cancel", {
      "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "kraft/x", worktree: "/w", findings: 2, threads: 1 }, mr: { ref: 142, url: "u", state: "open" }, spend: { spent_usd: 3.72, cap_usd: 10 } }],
    });
    expect(await screen.findByText(/branch kraft\/x, the worktree until it is archived, 2 findings, 1 threads/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("switch", { name: "Also close !142 on the forge" }));
    expect(screen.getByRole("button", { name: "Cancel item" })).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Reason" }), "wrong repo");
    await userEvent.click(screen.getByRole("button", { name: "Cancel item" }));
    await waitFor(() => expect(lastPost(calls)).toEqual({ method: "POST", path: "/work-items/w1/cancel", body: { reason: "wrong repo", close_mr: true } }));
  });

  it("keeps a refusal under the button and the text in the box", async () => {
    mount(gateItem(), "/work-items/w1?compose=reject", { "POST /work-items/w1/gates/plan_approval/reject": [409, { detail: "gate 'plan_approval' is not pending" }] });
    await userEvent.type(await screen.findByRole("textbox"), "no");
    await userEvent.click(screen.getByRole("button", { name: "Reject with note" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("is not pending");
    expect(screen.getByRole("textbox")).toHaveValue("no");
  });
});

describe("Raise budget (C.6)", () => {
  const stopped = () => item("needs_you", stop("budget", { reason: "The budget ran out.", scope: "work_item" }));
  it("ends the reason with one full stop, not two (Kraft-9d8b2.55)", async () => {
    mount(stopped());
    await userEvent.click(await screen.findByRole("button", { name: "Raise budget" }));
    const text = screen.getByRole("dialog", { name: "Raise budget" }).textContent ?? "";
    expect(text).toContain("The budget ran out. Raising it applies to this item only");
    expect(text).not.toContain("..");
  });

  it("offers steps above the cap, and raising resumes through the budget route", async () => {
    const calls = mount(stopped());
    await userEvent.click(await screen.findByRole("button", { name: "Raise budget" }));
    const sheet = screen.getByRole("dialog", { name: "Raise budget" });
    expect(sheet).toHaveTextContent("$15");
    await userEvent.click(within(sheet).getByRole("button", { name: /\+\$5/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/budget/raise", body: { budget_usd: 15 } }]));
  });
  it("takes a typed amount and refuses a non-amount before calling", async () => {
    const calls = mount(stopped());
    await userEvent.click(await screen.findByRole("button", { name: "Raise budget" }));
    await userEvent.click(screen.getByRole("button", { name: "Set an amount…" }));
    const input = screen.getByLabelText("Budget in dollars", { selector: "input" });
    expect(input).toHaveAttribute("inputmode", "decimal");
    await userEvent.clear(input);
    await userEvent.type(input, "0{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("above 0");
    expect(posts(calls)).toEqual([]);
    // r12 review: Number() took "Infinity", which removed the cap.
    for (const typed of ["Infinity", "1,000"]) {
      await userEvent.clear(input);
      await userEvent.type(input, `${typed}{Enter}`);
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("like 1000 or 1.5"));
      expect(posts(calls)).toEqual([]);
    }
    await userEvent.clear(input);
    await userEvent.type(input, "25{Enter}");
    await waitFor(() => expect(lastPost(calls)).toEqual({ method: "POST", path: "/work-items/w1/budget/raise", body: { budget_usd: 25 } }));
  });
  it("does not offer a raise for a time cap: Retry is the way on", async () => {
    mount(item("needs_you", stop("cap", { reason: "Running time hit its 8h cap" })));
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Raise/ })).toBeNull();
  });
});

describe("the question card (PH-12)", () => {
  it("draws a speech bubble before its title", async () => {
    mount(item("needs_you", stop("question"), { needs_context_question: "Allow the change?" }));
    const card = await screen.findByRole("region", { name: "Needs you" });
    expect(card.querySelector(".ph-statecard-title svg.lucide-message-square")).not.toBeNull();
  });
});

describe("⋮ (C.5)", () => {
  it("lists what the bar does not and acts", async () => {
    const calls = mount(item("running", null, { mr_ref: { number: 142, url: "https://forge/142" } }), "/work-items/w1", { "POST /work-items/w1/duplicate": [200, { id: "w2", status: "paused" }], "GET /work-items/w2": [200, item("paused", null, { id: "w2" })] });
    await userEvent.click(await screen.findByRole("button", { name: "More actions" }));
    const sheet = screen.getByRole("dialog");
    const labels = within(sheet).getAllByRole("button").map((b) => b.textContent);
    // No Escalate: /escalate refuses a running item.
    expect(labels).toEqual(["Item settings", "Open MR !142", "Duplicate", "Mark complete…", "Cancel item…"]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(where()).toBe("/work-items/w2"));
    expect(posts(calls)).toEqual(["POST /work-items/w1/duplicate"]);
  });
  it("sends Cancel item… to its composer", async () => {
    mount(item("running"), "/work-items/w1", { "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "b", worktree: "/w", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } }] });
    await userEvent.click(await screen.findByRole("button", { name: "More actions" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel item…" }));
    await waitFor(() => expect(where()).toBe("/work-items/w1?compose=cancel"));
  });
});

describe("a skipped gate in the chain (R15b-03)", () => {
  it("reads skipped in its row, not approved", async () => {
    const skipped = { seq: 3, work_item_id: "w1", type: "node_skipped", node_id: "plan_approval", payload: { node_id: "plan_approval", gate: "plan_approval" }, created_at: "2026-09-13T09:00:00Z" };
    mount(item("running"), "/work-items/w1", { "GET /work-items/w1/events": [200, [skipped]] });
    await userEvent.click(await screen.findByRole("button", { name: "2 done" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /plan_approval/ })).toHaveTextContent("skipped"));
    expect(screen.getByRole("button", { name: /plan_approval/ })).not.toHaveTextContent("approved");
  });
});

describe("the failed card's cause (R15b-01)", () => {
  const green = { passed: true, scopes: [{ command: "just test", scope: "**", passed: true, exit_code: 0, session_id: "s1" }] };
  const sheetLabels = async () => {
    await userEvent.click(await screen.findByRole("button", { name: "More actions" }));
    return within(screen.getByRole("dialog")).getAllByRole("button").map((b) => b.textContent);
  };

  it("a refused forge credential says what to run and where, with the work kept, and no cause token or settings detour", async () => {
    mount(item("failed", stop("failed", { facts: { cause: "forge_auth" } }), { branch: "kraft/x-w1", test_result: green }), "/work-items/w1", { "GET /work-items/w1/compare": [200, { files: [{ path: "a.py", insertions: 1, deletions: 0, touched_by: [], viewed: false }] }] });
    const card = await screen.findByRole("region", { name: "Failed" });
    await waitFor(() => expect(card).toHaveTextContent("work keptbranch kraft/x-w1 · 1 file · tests passing"));
    expect(card).toHaveTextContent("Sign the forge CLI in on the server's machine: gh auth login, or glab auth login for GitLab. If git itself can't authenticate, check its credentials there too (gh auth setup-git, your SSH key or credential helper). Then Retry.");
    expect(within(card).getByText("gh auth login").tagName).toBe("CODE");
    expect(card).not.toHaveTextContent("forge_auth");
    expect(card).not.toHaveTextContent("cause");
    expect(await sheetLabels()).not.toContain("Check the repo settings");
  });

  it("any other infrastructure stop offers the repo settings from the ⋮ sheet, and no forge login words", async () => {
    mount(item("failed", stop("infra", { facts: { cause: "git" } })));
    const card = await screen.findByRole("region", { name: "Failed" });
    expect(card).not.toHaveTextContent("gh auth login");
    expect(card).not.toHaveTextContent("git");
    expect(await sheetLabels()).toContain("Check the repo settings");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Check the repo settings" }));
    await waitFor(() => expect(where()).toBe("/settings/repos/%2Fcode%2Fkraft-plugins"));
  });
});

describe("pair actions that call straight through", () => {
  it.each([
    ["paused mid-chain → Resume", item("paused"), "Resume", "POST /work-items/w1/resume"],
    ["failed → Retry from the failed node", item("failed", stop("failed")), "Retry", "POST /work-items/w1/retry"],
    ["MR closed → Reopen", item("needs_you", stop("mr_closed", { facts: { ref: 142 } })), "Reopen MR", "POST /work-items/w1/reopen-mr"],
  ])("%s", async (_n, it, label, call) => {
    const calls = mount(it);
    await userEvent.click(await screen.findByRole("button", { name: label }));
    await waitFor(() => expect(posts(calls)).toEqual([call]));
  });
  it("Retry sends the failed node's path", async () => {
    const calls = mount(item("failed", stop("failed", { task: "verification.review.code_review" })));
    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(lastPost(calls)?.body).toEqual({ path: expect.stringContaining("verification") }));
  });
  it("a gate stop goes to the review, and Reject… to its composer", async () => {
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { pending_gate: "plan_approval", current_node_id: "plan_approval" }));
    await userEvent.click(await screen.findByRole("button", { name: "Reject…" }));
    await waitFor(() => expect(where()).toBe("/work-items/w1?compose=reject"));
  });
});

describe("raising the cap that stopped the item (R73)", () => {
  const limit = (over = {}) => ({ path: "", key: "time_cap_minutes", value: 480, maximum: 1440, ...over });
  const capped = (l?: unknown) => item("needs_you", { ...stop("cap", { reason: "Running time hit its 8h cap" }), ...(l ? { limit: l } : {}) } as WorkItemStop);
  const sent = (calls: Call[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
  const openSheet = async () => {
    await userEvent.click(await screen.findByRole("button", { name: "Raise cap" }));
    return screen.findByRole("dialog");
  };
  const type = async (v: string) => {
    const box = screen.getByLabelText("Raise the running time cap", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, `${v}{Enter}`);
  };

  it("?raise=1, from the board's Raise cap, opens the sheet over the item, and Back lands on the item", async () => {
    mount(capped(limit()), "/work-items/w1?raise=1");
    expect(await screen.findByRole("dialog", { name: /Raise the running time cap/ })).toBeInTheDocument();
    expect(where()).toBe("/work-items/w1");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("button", { name: "Raise cap" })).toBeInTheDocument();
  });

  it("?raise=1 on a stop the item cannot raise shows its card and no sheet", async () => {
    mount(capped(), "/work-items/w1?raise=1");
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("without stop.limit a cap stop keeps Steer and Retry and offers no raise", async () => {
    mount(capped());
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Steer" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Raise/ })).toBeNull();
  });

  // R14b-08: "Now 1 minutes"; R14b-06: a phone shows the digit keyboard for an amount.
  it.each([
    ["minutes", limit(), "Now 480 minutes of running time.", "numeric"],
    ["one minute", limit({ value: 1 }), "Now 1 minute of running time.", "numeric"],
    ["one attempt", limit({ path: "verification", key: "max_attempts", value: 1 }), "Now 1 attempt on verification.", "numeric"],
  ])("with stop.limit the bar is Steer and Raise cap, and the sheet shows the value and the maximum: %s", async (_n, l, now, mode) => {
    mount(capped(l));
    expect(await screen.findByRole("button", { name: "Steer" })).toBeInTheDocument();
    const dialog = await openSheet();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(dialog).toHaveTextContent(`${now} The policy maximum is 1440.`);
    expect(within(dialog).getByRole("textbox")).toHaveAttribute("inputmode", mode);
    expect(within(dialog).getByRole("textbox")).toHaveValue(String(l.value));
    expect(within(dialog).getByRole("button", { name: "Save & retry" })).toBeInTheDocument();
  });

  it("says there is no policy maximum when the server sends none", async () => {
    mount(capped(limit({ maximum: null })));
    expect(await openSheet()).toHaveTextContent("The policy sets no maximum.");
  });

  it("Save & retry patches the item's own policy, then retries, in that order", async () => {
    const calls = mount(capped(limit()));
    await openSheet();
    await type("600");
    await waitFor(() => expect(sent(calls)).toEqual(["PATCH /work-items/w1", "POST /work-items/w1/retry"]));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ policy: { time_cap_minutes: 600 } });
    expect(calls.find((c) => c.path === "/work-items/w1/retry")!.body).toEqual({});
  });

  it("a node's own limit goes under policy.paths", async () => {
    const calls = mount(capped(limit({ path: "verification", key: "max_attempts", value: 3 })));
    await userEvent.click(await screen.findByRole("button", { name: "Raise cap" }));
    const box = screen.getByLabelText("Raise the attempts cap", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "5{Enter}");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ policy: { paths: { verification: { max_attempts: 5 } } } }));
  });

  it("a raise keeps the item's other policy overrides, item-wide and on every path", async () => {
    const policy_override = { budget_usd: 5, max_attempts: 4, paths: { verification: { timeout_minutes: 30 }, review: { max_attempts: 2 } } };
    const calls = mount({ ...capped(limit()), policy_override });
    await openSheet();
    await type("600");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ policy: { ...policy_override, time_cap_minutes: 600 } }));
  });

  it("a number that is not above the current value, or above the maximum, is refused before any call", async () => {
    const calls = mount(capped(limit()));
    await openSheet();
    await type("480");
    expect(await screen.findByRole("alert")).toHaveTextContent("above the current 480");
    await type("2000");
    expect(await screen.findByRole("alert")).toHaveTextContent("The policy maximum is 1440.");
    await type("many");
    expect(await screen.findByRole("alert")).toHaveTextContent("whole number");
    expect(sent(calls)).toEqual([]);
  });

  it.each([["hex", "0x10"], ["an exponent", "1e3"], ["a decimal", "600.5"]])("a whole-number cap refuses %s, which Number() would have read", async (_, typed) => {
    const calls = mount(capped(limit()));
    await openSheet();
    await type(typed);
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a whole number of");
    expect(sent(calls)).toEqual([]);
  });

  // The policy budget_usd stop reads dollars as the budget sheet does, not with Number() (R13b-01).
  describe("a policy budget_usd stop", () => {
    const budgeted = () => item("needs_you", { ...stop("budget", { reason: "budget_usd reached: $0.05 spent in the work item, cap $0.05" }), limit: limit({ key: "budget_usd", value: 0.05, maximum: null }) } as WorkItemStop);
    const typeCap = async (v: string) => {
      await userEvent.click(await screen.findByRole("button", { name: "Raise budget" }));
      const box = screen.getByLabelText("Raise the budget cap", { selector: "input" });
      expect(box).toHaveAttribute("inputmode", "decimal");
      await userEvent.clear(box);
      await userEvent.type(box, `${v}{Enter}`);
    };

    it("reads 0,5 as fifty cents", async () => {
      const calls = mount(budgeted());
      await typeCap("0,5");
      await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ policy: { budget_usd: 0.5 } }));
    });

    it.each([["hex", "0x10"], ["an exponent", "1e3"], ["an ambiguous 1,000", "1,000"]])("refuses %s with the hint, before any call", async (_, typed) => {
      const calls = mount(budgeted());
      await typeCap(typed);
      expect(await screen.findByRole("alert")).toHaveTextContent("Type the amount plainly, like 1000 or 1.5.");
      expect(sent(calls)).toEqual([]);
    });
  });

  it("a refused patch shows the server's words and does not retry", async () => {
    const calls = mount(capped(limit()), "/work-items/w1", { "PATCH /work-items/w1": [422, { detail: "time_cap_minutes exceeds the policy maximum 480" }] });
    await openSheet();
    await type("600");
    expect(await screen.findByRole("alert")).toHaveTextContent("exceeds the policy maximum 480");
    expect(sent(calls)).toEqual(["PATCH /work-items/w1"]);
  });

  it("a retry refused after the patch says the value was raised", async () => {
    const calls = mount(capped(limit()), "/work-items/w1", { "POST /work-items/w1/retry": [409, { detail: "already running" }] });
    await openSheet();
    const reads = () => calls.filter((c) => c.method === "GET" && c.path === "/work-items/w1").length;
    const before = reads();
    await type("600");
    expect(await screen.findByRole("alert")).toHaveTextContent("Raised to 600, but the retry was refused: already running");
    expect(reads()).toBeGreaterThan(before);
    expect(sent(calls)).toEqual(["PATCH /work-items/w1", "POST /work-items/w1/retry"]);
  });

  it("a stop.limit on a stop that is neither a cap nor a budget draws nothing", async () => {
    mount(item("needs_you", { ...stop("question"), limit: limit() } as WorkItemStop));
    await screen.findAllByText(/./);
    expect(screen.queryByRole("button", { name: /^Raise/ })).toBeNull();
  });
});

describe("a budget stop the item cannot raise", () => {
  // The daily cap: the item is under its own $10, so /budget/raise would answer 409.
  const daily = () => item("needs_you", stop("budget", { reason: "budget cap reached: $50.00 spent on today, across every work item, cap $50.00.", scope: "daily" }), { budget_cap: { cap_usd: 10, source: "policy", spent_usd: 2 } });

  it("offers Retry, not Raise budget, and says where the cap is raised", async () => {
    const calls = mount(daily());
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Raise/ })).toBeNull();
    expect(screen.getByText(/The item can't raise this cap/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/retry"]));
  });

  it("opens no raise sheet when one is asked for anyway", async () => {
    mount(daily(), { pathname: "/work-items/w1", state: { phSheet: "raise" } });
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("raising a policy budget_usd that stopped the item (Kraft-9d8b2.59)", () => {
  const policyStop = () => item("needs_you", { ...stop("budget", { reason: "budget_usd reached: $10.00 spent in the work item, cap $10.00." }), limit: { path: "", key: "budget_usd", value: 10, maximum: 25 } } as WorkItemStop);
  const sent = (calls: Call[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);

  it("Raise budget opens the dollar sheet that patches the policy and retries, not the +$5 sheet", async () => {
    const calls = mount(policyStop());
    const raise = await screen.findByRole("button", { name: "Raise budget" });
    expect(screen.queryByRole("button", { name: /^Raise the/ })).toBeNull();
    await userEvent.click(raise);
    const sheet = await screen.findByRole("dialog", { name: "Raise the budget cap" });
    expect(sheet).toHaveTextContent("Now $10. The policy maximum is $25.");
    expect(screen.queryByRole("button", { name: /\+\$5/ })).toBeNull();
    const box = screen.getByLabelText("Raise the budget cap", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "12.5{Enter}");
    await waitFor(() => expect(sent(calls)).toEqual(["PATCH /work-items/w1", "POST /work-items/w1/retry"]));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ policy: { budget_usd: 12.5 } });
  });

  it("refuses a figure above the policy maximum before calling", async () => {
    const calls = mount(policyStop());
    await userEvent.click(await screen.findByRole("button", { name: "Raise budget" }));
    const box = await screen.findByLabelText("Raise the budget cap", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "30{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("The policy maximum is $25.");
    expect(sent(calls)).toEqual([]);
  });

  it("a raise asked for on arrival lands on the same sheet", async () => {
    mount(policyStop(), { pathname: "/work-items/w1", state: { phSheet: "raise" } });
    expect(await screen.findByRole("dialog", { name: "Raise the budget cap" })).toBeInTheDocument();
  });
});

void act;
