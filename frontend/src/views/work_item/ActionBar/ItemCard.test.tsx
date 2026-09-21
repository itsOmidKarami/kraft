import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import { dismissTurn } from "./EscalationCard";
import { ItemCard } from "./ItemCard";
import { NEEDS_HUMAN_EVENT, escMessage, escSession, item, session } from "../../../testFixtures";

type Href = (node: string, tab: string, id: string) => string;

function renderCard(
  item_: WorkItem,
  sessions: WorkerSession[] = [],
  events: KraftEvent[] = [],
  reviewHref: Href = (node, tab, id) => `#node=${node}&tab=${tab}&tnode=${id}`,
) {
  return render(
    <MemoryRouter>
      <ItemCard
        item={item_}
        sessions={sessions}
        events={events}
        onReviewChanges={() => {}}
        onEditChain={() => {}}
        reviewHref={reviewHref}
      />
    </MemoryRouter>,
  );
}

/** The button row's own controls, More actions left out. */
const rowNames = () =>
  [...document.querySelectorAll<HTMLElement>(".item-card-actions > button, .item-card-actions > a")].map((b) =>
    b.textContent?.trim(),
  );

const gateItem = (over: Partial<WorkItem> = {}) =>
  item({ status: "needs_human", pending_gate: "spec_approval", gate_artifact: "docs/spec.md", current_node_id: "spec", ...over });
const capped: Partial<WorkItem> = { status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } };
const openMore = () => userEvent.click(screen.getByRole("button", { name: "More actions" }));
const menuLabels = () => screen.getAllByRole("menuitem").map((m) => m.textContent);

// Ten findings, the live item's scale that pushed the split off the viewport (Kraft-a4js).
const tenFindings = Array.from({ length: 10 }, (_, i) => ({
  severity: "minor",
  message: `finding number ${i} with a fair bit of prose explaining why it matters and where`,
  file: "a.py",
  line: i,
  source_plugin: "fake",
}));

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("ItemCard (W11 · A)", () => {
  it.each<[string, WorkItem, WorkerSession[], KraftEvent[], string[]]>([
    ["gate", gateItem(), [], [], ["Approve", "Reject", "Review spec"]],
    ["gate without a named document", gateItem({ pending_gate: "code_review" }), [], [], ["Approve", "Reject", "Read document"]],
    ["running", item({ status: "active" }), [], [], ["Pause"]],
    ["paused", item({ status: "paused" }), [], [], ["Resume", "Steer"]],
    ["paused, steerable false", item({ status: "paused", steerable: false }), [], [], ["Resume"]],
    ["capped", item(capped), [], [], ["Steer & retry", "Escalate"]],
    ["budget", item({ status: "needs_human", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } }), [], [], ["Raise budget", "Escalate"]],
    ["question", item({ status: "needs_human", needs_context_question: "which?" }), [], [], ["Answer"]],
    ["escalating", item(capped), [escSession()], [NEEDS_HUMAN_EVENT, escMessage()], []],
    ["escalated", item(capped), [escSession({ status: "done", exited_at: "t" })], [NEEDS_HUMAN_EVENT, escMessage()], ["Reply", "Dismiss"]],
    ["done", item({ status: "completed", mr_ref: { number: 42, url: "https://x" } }), [], [], ["Open MR"]],
    ["not started", item({ status: "paused", current_node_id: null }), [], [], ["Start", "Edit chain"]],
  ])("%s: the button row is its state's set", (_, it_, sessions, events, want) => {
    renderCard(it_, sessions, events);
    expect(rowNames()).toEqual(want);
    expect(screen.getByRole("button", { name: "More actions" })).toBeInTheDocument();
  });

  it("gate: Reject says where it walks back to as its tooltip and description, and the card has no hint line", () => {
    const chain = {
      template_id: "d",
      nodes: [
        { id: "spec", tasks: [], gate_after: null },
        { id: "plan", tasks: [], gate_after: "plan_approval", reject_to: "spec" },
      ],
    };
    renderCard(gateItem({ pending_gate: "plan_approval", current_node_id: "plan", chain_definition: chain }));
    const reject = screen.getByRole("button", { name: /^Reject$/ });
    expect(reject).toHaveAttribute("title", "reject walks back to spec");
    expect(reject).toHaveAccessibleDescription("reject walks back to spec");
    expect(document.querySelector(".control-hint")).toBeNull();
  });

  it("gate: More actions lists Skip, Escalate, then the item's own actions, one per row (rule 8)", async () => {
    renderCard(gateItem());
    await openMore();
    expect(menuLabels()).toEqual([
      "Skip",
      "Escalate",
      "Review changes",
      "Open MR",
      "Open worktree",
      "Copy id",
      "Copy link",
      "Archive",
      "Cancel work item",
    ]);
    expect(screen.getByRole("menuitem", { name: "Skip" })).toHaveAttribute("title", "continue without approving");
    expect(screen.getByRole("menuitem", { name: "Escalate" })).toHaveAttribute("title", "ask an agent to decide");
  });

  it("Open MR is dimmed with a reason until an MR exists", async () => {
    const { unmount } = renderCard(gateItem());
    await openMore();
    const mr = screen.getByRole("menuitem", { name: "Open MR" });
    expect(mr).toHaveAttribute("aria-disabled", "true");
    expect(mr).toHaveAttribute("title", "not opened yet");
    unmount();
    renderCard(gateItem({ mr_ref: { number: 7, url: "https://x" } }));
    await openMore();
    expect(screen.getByRole("menuitem", { name: "Open MR" })).not.toHaveAttribute("aria-disabled");
  });

  it("More actions is a menu button: arrows move between rows, Escape closes and returns focus (rule 11)", async () => {
    renderCard(gateItem());
    const more = screen.getByRole("button", { name: "More actions" });
    expect(more).toHaveAttribute("aria-haspopup", "menu");
    await userEvent.click(more);
    const rows = screen.getAllByRole("menuitem");
    expect(rows[0]).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(rows[1]).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}{ArrowUp}");
    expect(rows.at(-1)).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(more).toHaveFocus();
  });

  it("puts the stats top-right: this node's tasks, the item's tokens and spend", () => {
    renderCard(
      item({
        usage: {
          total: { tokens_in: 90_000, tokens_out: 5_400, cost_usd: 5.01, cost_complete: true, wall_ms: 0, sessions: 1, rounds: 1, capped_out: 0 },
          by_node: [],
        },
      }),
      [session()],
    );
    expect(document.querySelector(".item-card-stats")?.textContent).toBe("1 task · 95.4k tokens · $5.01");
  });

  it("gate: Approve calls the API, and a failure says so", async () => {
    const spy = vi.spyOn(api, "approveGate").mockRejectedValue(new Error("409 gate already resolved"));
    renderCard(gateItem({ pending_gate: "plan_approval", current_node_id: "plan" }));
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(spy).toHaveBeenCalledWith("w1", "plan_approval");
    expect(await screen.findByText(/409 gate already resolved/)).toBeInTheDocument();
  });

  it("gate: the Reject composer opens under the buttons and stays disabled until a note is entered", async () => {
    renderCard(gateItem());
    await userEvent.click(screen.getByRole("button", { name: /^Reject$/ }));
    expect(document.querySelector(".item-card-actions + .composer, .item-card-actions ~ .composer")).toBeTruthy();
    const submit = screen.getByRole("button", { name: /reject and/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/composer message/i), "fix the error path");
    expect(submit).toBeEnabled();
  });

  it("gate: the Reject composer names the node a rejection sends the chain back to", async () => {
    const chain = {
      template_id: "d",
      nodes: [
        { id: "spec", tasks: [], gate_after: null },
        { id: "plan", tasks: [], gate_after: "plan_approval", reject_to: "spec" },
      ],
    };
    renderCard(gateItem({ pending_gate: "plan_approval", current_node_id: "plan", chain_definition: chain }));
    await userEvent.click(screen.getByRole("button", { name: /^Reject$/ }));
    expect(screen.getByText(/re-enters at/)).toHaveTextContent("spec");
    expect(screen.getByRole("button", { name: /reject and send back/i })).toBeInTheDocument();
  });

  it("gate: Skip in More opens its composer and skips with the note", async () => {
    const spy = vi.spyOn(api, "skipWorkItem").mockResolvedValue({} as never);
    renderCard(gateItem());
    await openMore();
    await userEvent.click(screen.getByRole("menuitem", { name: "Skip" }));
    await userEvent.type(screen.getByLabelText(/composer message/i), "not needed");
    await userEvent.click(screen.getByRole("button", { name: "Skip step" }));
    expect(spy).toHaveBeenCalledWith("w1", "not needed");
  });

  it("gate: counts deferred findings on the header's second line and links them to the Timeline", () => {
    renderCard(gateItem({ pending_gate: "code_review", deferred_findings: tenFindings, concerns: ["the retry path is untested"] }));
    const sub = document.querySelector(".item-card-sub") as HTMLElement;
    expect(sub.textContent).toMatch(/^code_review · 10 findings deferred · 1 concern · see Timeline$/);
    expect(screen.queryByText(/finding number 0/)).toBeNull();
    expect(screen.getByRole("link", { name: /see timeline/i })).toHaveAttribute("href", expect.stringContaining("tab=timeline"));
  });

  it("gate: the findings link targets the node whose findings_measured carries them, skipping a later empty one", () => {
    const events: KraftEvent[] = [
      { seq: 1, work_item_id: "w1", type: "findings_measured", payload: { node_id: "verify", findings: tenFindings }, created_at: "t1" },
      { seq: 2, work_item_id: "w1", type: "findings_measured", payload: { node_id: "mr_checks", findings: [] }, created_at: "t2" },
    ];
    renderCard(
      gateItem({ pending_gate: "human_review_approval", deferred_findings: tenFindings, current_node_id: "human_review" }),
      [],
      events,
    );
    expect(screen.getByRole("link", { name: /see timeline/i })).toHaveAttribute("href", expect.stringContaining("tnode=verify"));
  });

  it("gate: a judge-stop note stays in the card, counted rather than listed", () => {
    const judge = [
      {
        node_id: "verify",
        reasoning: "real but not worth chasing further",
        findings: [{ severity: "important", message: "still broken", file: "a.py", line: 4, source_plugin: "on.check" }],
      },
    ];
    renderCard(gateItem({ judge_stop_note: judge }));
    expect(screen.getByText(/real but not worth chasing further/)).toBeInTheDocument();
    expect(screen.getByText(/1 finding not chased/)).toBeInTheDocument();
    expect(screen.queryByText(/still broken/)).toBeNull();
    expect(document.querySelectorAll(".gate-judge-note")).toHaveLength(1);
  });

  it("gate: the document link names the gate's document and lands on the Documents tab", () => {
    renderCard(gateItem({ pending_gate: "plan_approval", gate_artifact: "docs/plan.md", current_node_id: "plan" }));
    expect(screen.getByRole("link", { name: /review plan/i })).toHaveAttribute("href", expect.stringContaining("tab=documents"));
  });

  it("gate: an unwritten document is a disabled label saying so", () => {
    renderCard(gateItem({ gate_artifact: null }));
    const doc = screen.getByText(/Review spec/);
    expect(doc).toHaveAttribute("aria-disabled", "true");
    expect(doc.getAttribute("title")).toMatch(/not written yet/);
  });

  it("running: Pause enabled, Steer is a disabled More row saying when it unlocks, no Escalate", async () => {
    renderCard(item({ status: "active" }));
    expect(screen.getByRole("button", { name: /^pause$/i })).toBeEnabled();
    await openMore();
    const steer = screen.getByRole("menuitem", { name: "Steer" });
    expect(steer).toHaveAttribute("aria-disabled", "true");
    expect(steer).toHaveAttribute("title", "steer unlocks once paused");
    expect(screen.queryByRole("menuitem", { name: /escalate/i })).toBeNull();
  });

  it("paused: Steer opens a composer that resumes with the note", async () => {
    const spy = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({ id: "w1", node_id: "verify", steer: "go" });
    renderCard(item({ status: "paused" }));
    await userEvent.click(screen.getByRole("button", { name: /^steer$/i }));
    await userEvent.type(screen.getByLabelText(/composer message/i), "go");
    await userEvent.click(screen.getByRole("button", { name: /resume with this steer/i }));
    expect(spy).toHaveBeenCalledWith("w1", "go");
  });

  it("composer: Cmd-Enter submits on the item it was opened on and closes the composer (W6.3, W6.4)", async () => {
    const resume = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({ id: "w1", node_id: null, steer: "go" });
    renderCard(item({ status: "paused" }));
    await userEvent.click(screen.getByRole("button", { name: /^steer$/i }));
    await userEvent.type(screen.getByLabelText(/composer message/i), "go");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(resume).toHaveBeenCalledWith("w1", "go");
    await waitFor(() => expect(screen.queryByLabelText(/composer message/i)).toBeNull());
  });

  it("composer: Escape cancels and puts focus back on the trigger (W6.4)", async () => {
    renderCard(item({ status: "paused" }));
    await userEvent.click(screen.getByRole("button", { name: /^steer$/i }));
    expect(screen.getByLabelText(/composer message/i)).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByLabelText(/composer message/i)).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: /^steer$/i })).toHaveFocus());
  });

  it("paused: Escalate is offered from More actions and posts through (Kraft-k5ol)", async () => {
    const spy = vi.spyOn(api, "escalateWorkItem").mockResolvedValue({ id: "w1", status: "escalating" });
    renderCard(item({ status: "paused" }));
    await openMore();
    expect(menuLabels()).toContain("Escalate");
    await userEvent.click(screen.getByRole("menuitem", { name: "Escalate" }));
    await userEvent.type(screen.getByLabelText(/composer message/i), "please look at this");
    await userEvent.click(screen.getByRole("button", { name: /^escalate$/i }));
    expect(spy).toHaveBeenCalledWith("w1", "please look at this");
  });

  it("shows a failed Cancel work item", async () => {
    vi.spyOn(api, "abandonWorkItem").mockRejectedValue(new Error("409 busy"));
    renderCard(item({ status: "paused" }));
    await openMore();
    await userEvent.click(screen.getByRole("menuitem", { name: /cancel work item/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /cancel work item/i }));
    expect(await screen.findByText(/409 busy/)).toBeInTheDocument();
  });

  it("offers Restore instead of Archive once archived (W0.9)", async () => {
    renderCard(item({ status: "completed", archived_at: "2026-09-13T08:00:00Z" }));
    await openMore();
    expect(screen.getByRole("menuitem", { name: "Restore" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Archive" })).toBeNull();
  });

  it("not started: Start resumes the never-run item", async () => {
    const spy = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({ id: "w1", node_id: null, steer: null });
    renderCard(item({ status: "paused", current_node_id: null }));
    await userEvent.click(screen.getByRole("button", { name: /^start$/i }));
    expect(spy).toHaveBeenCalledWith("w1");
  });

  it("done: Open MR links the merge request the header names", () => {
    renderCard(item({ status: "completed", mr_ref: { number: 42, url: "https://x" } }));
    expect(screen.getByRole("link", { name: /open mr/i })).toHaveAttribute("href", "https://x");
    expect(screen.getByText(/merged !42/)).toBeInTheDocument();
  });

  it("capped: steerable false retries immediately, no composer", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    renderCard(item({ ...capped, steerable: false }));
    await userEvent.click(screen.getByRole("button", { name: /steer & retry/i }));
    expect(spy).toHaveBeenCalledWith("w1");
    expect(screen.queryByLabelText(/composer message/i)).toBeNull();
  });

  it("capped by a judge: the header gives the judge's reasoning, not the cap copy", () => {
    renderCard(item({ status: "needs_human", cappedOut: undefined, stop_reason: "judge: recurring findings" }), [], [NEEDS_HUMAN_EVENT]);
    expect(screen.getByText("stopped early (judge)")).toBeInTheDocument();
    expect(document.querySelector(".item-card-sub")?.textContent).toMatch(/recurring findings$/);
    expect(screen.queryByText(/hit its cap/)).toBeNull();
  });

  it("budget: Raise budget reaches the item through its composer", async () => {
    const raise = vi.spyOn(api, "raiseBudget").mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    renderCard(item({ status: "needs_human", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } }));
    await userEvent.click(screen.getByRole("button", { name: /raise budget/i }));
    await userEvent.click(screen.getByRole("button", { name: /\+\$5/i }));
    await userEvent.click(screen.getByRole("button", { name: /raise to \$10 and resume/i }));
    expect(raise).toHaveBeenCalledWith("w1", 10);
  });

  it("question: the question shows in full; Answer quotes it and stays disabled until text is entered", async () => {
    const spy = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({ id: "w1", node_id: "v", steer: "42" });
    renderCard(item({ status: "needs_human", needs_context_question: "which backoff?" }));
    expect(screen.getByTestId("stop-question")).toHaveTextContent("which backoff?");
    await userEvent.click(screen.getByRole("button", { name: /^answer$/i }));
    expect(screen.getAllByText("which backoff?")).toHaveLength(1);
    const submit = screen.getByRole("button", { name: /answer and resume/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/composer message/i), "42");
    await userEvent.click(submit);
    expect(spy).toHaveBeenCalledWith("w1", "42");
  });

  it("escalating: the pill and More, not the underlying state's buttons", () => {
    renderCard(item(capped), [escSession()], [NEEDS_HUMAN_EVENT, escMessage()]);
    expect(screen.getByTestId("escalating-pill")).toHaveTextContent(/agent is on it · turn 1/i);
    expect(screen.getByText(/one escalation turn at a time/)).toBeInTheDocument();
  });

  it("escalating, auto: the pill and the header say it fired on its own", () => {
    const autoMsg = escMessage({ payload: { session_id: "e1", message: "diagnose and fix", auto: true } });
    renderCard(item(capped), [escSession()], [NEEDS_HUMAN_EVENT, autoMsg]);
    expect(screen.getByText(/auto-escalated · turn 1/i)).toBeInTheDocument();
    expect(screen.getByText(/fired automatically/i)).toBeInTheDocument();
  });

  it("escalating: a failed Stop escalation shows its error", async () => {
    vi.spyOn(api, "stopEscalation").mockRejectedValue(new Error("stop failed"));
    renderCard(item(capped), [escSession()], [NEEDS_HUMAN_EVENT, escMessage()]);
    await openMore();
    await userEvent.click(screen.getByRole("menuitem", { name: "Stop escalation" }));
    expect(await screen.findByText("stop failed")).toBeInTheDocument();
  });

  it("escalated: the report shows in the card and Apply as steer & retry is a More row", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    const done = escSession({ status: "done_with_concerns", exited_at: "t" });
    const exit: KraftEvent = {
      seq: 3,
      work_item_id: "w1",
      type: "worker_session_exited",
      payload: { session_id: "e1", status: "done_with_concerns", concerns: "renamed the field" },
      created_at: "t",
    };
    renderCard(item(capped), [done], [NEEDS_HUMAN_EVENT, escMessage(), exit]);
    expect(screen.getByTestId("escalated-card")).toHaveTextContent("renamed the field");
    await openMore();
    await userEvent.click(screen.getByRole("menuitem", { name: "Apply as steer & retry" }));
    expect(spy).toHaveBeenCalledWith("w1", "From escalation: renamed the field");
  });

  it("a dismissed escalation falls back to the underlying capped reason", () => {
    dismissTurn("w1", "e1");
    renderCard(item(capped), [escSession({ id: "e1", status: "done", attempt: 1 })], [NEEDS_HUMAN_EVENT]);
    expect(screen.getByRole("button", { name: /steer & retry/i })).toBeInTheDocument();
    expect(screen.queryByTestId("escalated-card")).toBeNull();
  });

  it("clicking Dismiss takes the escalated state off at once", async () => {
    const done = escSession({ id: "e9", status: "done", attempt: 1 });
    renderCard(item(capped), [done], [NEEDS_HUMAN_EVENT, escMessage({ payload: { session_id: "e9", message: "go" } })]);
    expect(screen.getByTestId("escalated-card")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByTestId("escalated-card")).toBeNull();
    expect(screen.getByRole("button", { name: /steer & retry/i })).toBeInTheDocument();
  });

  it("the escalate composer shows the prior thread on turn 2+, marking an auto turn", async () => {
    const manual = { seq: 1, work_item_id: "w1", type: "escalation_message", payload: { session_id: "e2", message: "look at the widget", auto: false }, created_at: "2026-01-01T00:00:00Z" } as KraftEvent;
    const auto = { seq: 2, work_item_id: "w1", type: "escalation_message", payload: { session_id: "e3", message: "diagnose and fix", auto: true }, created_at: "2026-01-01T00:05:00Z" } as KraftEvent;
    const laterStop: KraftEvent = { ...NEEDS_HUMAN_EVENT, seq: 3, created_at: "2026-01-01T00:10:00Z" };
    renderCard(
      item(capped),
      [
        escSession({ id: "e2", status: "done", attempt: 1, created_at: "2026-01-01T00:00:00Z" }),
        escSession({ id: "e3", status: "done", attempt: 2, created_at: "2026-01-01T00:05:00Z" }),
      ],
      [manual, auto, laterStop],
    );
    await userEvent.click(screen.getByRole("button", { name: /^escalate/i }));
    const thread = screen.getByTestId("escalation-thread");
    expect(thread).toHaveTextContent("turn 1: look at the widget");
    expect(thread).toHaveTextContent("turn 2 (auto-escalated): diagnose and fix");
  });

  it("escalated: renders the report as markdown, not literal characters", () => {
    const exit = {
      seq: 3,
      work_item_id: "w1",
      type: "worker_session_exited",
      payload: { session_id: "e1", status: "done_with_concerns", concerns: "## Fixed\n\n- **one** thing\n" },
      created_at: "t",
    } as KraftEvent;
    renderCard(item(capped), [escSession({ status: "done_with_concerns", exited_at: "t" })], [NEEDS_HUMAN_EVENT, escMessage(), exit]);
    expect(screen.getByRole("heading", { name: "Fixed" })).toBeInTheDocument();
    expect(screen.queryByText(/## Fixed/)).toBeNull();
  });

  it("escalated: a clean turn with no concerns reads its session summary document, and offers Apply", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [
        {
          document_id: "d1", repo: "/r", title: "Escalation report", kind: "sessions", source_kind: "session_summary",
          path: ".engineering/sessions/e1.md", node_id: "verify", hook_point: "escalation", worker_session_id: "e1",
          attachment_kind: null, indexed_at: "t",
        },
      ],
    });
    vi.spyOn(api, "getDocument").mockResolvedValue({
      id: "d1", repo: "/r", source_kind: "session_summary", kind: "sessions", title: "Escalation report",
      path: ".engineering/sessions/e1.md", content: "Proposed renaming the field to fix the mismatch.", metadata: {},
      source_created_at: null, source_updated_at: null, indexed_at: "t", links: [],
    });
    const retry = vi.spyOn(api, "retryWorkItem").mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    const done = escSession({ status: "done", exited_at: "t", session_summary_ref: ".engineering/sessions/e1.md" });
    renderCard(item(capped), [done], [NEEDS_HUMAN_EVENT, escMessage()]);
    expect(await screen.findByText("Proposed renaming the field to fix the mismatch.")).toBeInTheDocument();
    await openMore();
    await userEvent.click(screen.getByRole("menuitem", { name: "Apply as steer & retry" }));
    expect(retry).toHaveBeenCalledWith("w1", "From escalation: Proposed renaming the field to fix the mismatch.");
  });

  it("escalated: a turn stopped by Stop agent says so, and offers no Apply", async () => {
    renderCard(item(capped), [escSession({ status: "paused", exited_at: "t" })], [NEEDS_HUMAN_EVENT, escMessage()]);
    expect(screen.getByText(/stopped by stop agent/i)).toBeInTheDocument();
    await openMore();
    expect(screen.queryByRole("menuitem", { name: "Apply as steer & retry" })).toBeNull();
  });

  it("escalated: quotes the turn's own message when it reported no summary (W8.6)", () => {
    renderCard(
      item(capped),
      [escSession({ status: "done", exited_at: "t" })],
      [NEEDS_HUMAN_EVENT, escMessage({ payload: { session_id: "e1", message: "what about submodules?" } })],
    );
    expect(screen.getByTestId("escalated-card")).toHaveTextContent("what about submodules?");
    expect(screen.queryByText(/no summary reported/i)).toBeNull();
  });

  it("escalated with two threads: composer shows a folded row for thread 1 and thread 2's turns above the split button (Kraft-dkb6g)", async () => {
    const t1 = escSession({ id: "s1", thread: 1, status: "done", exited_at: "2026-09-13T10:00:00Z" });
    const t2a = escSession({ id: "s2", thread: 2, status: "done", exited_at: "2026-09-13T10:30:00Z" });
    renderCard(
      item(capped),
      [t1, t2a],
      [
        NEEDS_HUMAN_EVENT,
        escMessage({ seq: 2, payload: { session_id: "s1", message: "first turn", thread: 1, turn: 1 } }),
        escMessage({ seq: 3, payload: { session_id: "s2", message: "second thread", thread: 2, turn: 1 } }),
      ],
    );
    await userEvent.click(screen.getByRole("button", { name: /reply/i }));
    expect(screen.getByTestId("escalation-thread-fold-1")).toHaveTextContent(/thread 1 · 1 turn/);
    expect(screen.getByTestId("escalation-thread-divider")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reply options" })).toBeInTheDocument();
  });

  it("escalate composer: picking 'in new thread' from the split menu posts new_thread true (Kraft-dkb6g)", async () => {
    const spy = vi.spyOn(api, "escalateWorkItem").mockResolvedValue({ id: "w1", status: "escalating" });
    renderCard(
      item(capped),
      [escSession({ thread: 1, status: "done", exited_at: "t" })],
      [NEEDS_HUMAN_EVENT, escMessage({ payload: { session_id: "e1", message: "go", thread: 1, turn: 1 } })],
    );
    await userEvent.click(screen.getByRole("button", { name: /reply/i }));
    await userEvent.type(screen.getByLabelText(/composer message/i), "follow-up");
    await userEvent.click(screen.getByRole("button", { name: /reply options/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /reply in new thread/i }));
    expect(spy).toHaveBeenCalledWith("w1", "follow-up", true);
  });

  it("first-ever escalation: no split menu, no fold rows, no divider (Kraft-dkb6g)", async () => {
    renderCard(item(capped));
    await userEvent.click(screen.getByRole("button", { name: /^escalate$/i }));
    expect(screen.queryByRole("button", { name: /escalate options/i })).toBeNull();
    expect(screen.queryByTestId("escalation-thread-divider")).toBeNull();
  });
});
