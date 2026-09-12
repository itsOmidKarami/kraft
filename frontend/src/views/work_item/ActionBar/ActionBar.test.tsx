import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import { ActionBar } from ".";
import { dismissTurn } from "./EscalationCard";
import { NEEDS_HUMAN_EVENT, escSession, item } from "./testFixtures";

const here = dirname(fileURLToPath(import.meta.url));

function renderBar(
  item_: WorkItem,
  sessions: WorkerSession[] = [],
  events: KraftEvent[] = [],
) {
  return render(
    <MemoryRouter>
      <ActionBar
        item={item_}
        sessions={sessions}
        events={events}
        onReviewChanges={() => {}}
        onReadDoc={() => {}}
        onEditChain={() => {}}
      />
    </MemoryRouter>,
  );
}

describe("ActionBar", () => {
  it("running: Pause enabled, Steer disabled, no Escalate button", () => {
    renderBar(item({ status: "active" }));
    expect(screen.getByRole("button", { name: /^pause$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^steer$/i })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /escalate/i }),
    ).not.toBeInTheDocument();
  });

  it("paused: no Escalate button — escalate_work_item 409s on anything but needs_human", () => {
    renderBar(item({ status: "paused" }));
    expect(
      screen.queryByRole("button", { name: /escalate/i }),
    ).not.toBeInTheDocument();
  });

  it("paused: Steer expands a composer that resumes with the note", async () => {
    const spy = vi
      .spyOn(api, "resumeWorkItem")
      .mockResolvedValue({ id: "w1", node_id: "verify", steer: "go" });
    renderBar(item({ status: "paused" }));
    await userEvent.click(screen.getByRole("button", { name: /^steer$/i }));
    await userEvent.type(screen.getByLabelText(/composer message/i), "go");
    await userEvent.click(
      screen.getByRole("button", { name: /resume with this steer/i }),
    );
    expect(spy).toHaveBeenCalledWith("w1", "go");
  });

  it("shows a failed Cancel work item", async () => {
    vi.spyOn(api, "abandonWorkItem").mockRejectedValue(new Error("409 busy"));
    renderBar(item({ status: "paused" }));
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /cancel work item/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /cancel work item/i }));
    expect(await screen.findByText(/409 busy/)).toBeInTheDocument();
  });

  it("not_started: Start resumes the never-run item", async () => {
    const spy = vi
      .spyOn(api, "resumeWorkItem")
      .mockResolvedValue({ id: "w1", node_id: null, steer: null });
    renderBar(item({ status: "paused", current_node_id: null }));
    await userEvent.click(screen.getByRole("button", { name: /^start$/i }));
    expect(spy).toHaveBeenCalledWith("w1");
  });

  it("done: Archive shows the merged MR", () => {
    renderBar(
      item({ status: "completed", mr_ref: { number: 42, url: "https://x" } }),
    );
    expect(
      screen.getByRole("button", { name: /^archive$/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/merged !42/)).toBeInTheDocument();
  });

  it("archived: shows Restore, not Archive", () => {
    renderBar(
      item({ status: "completed", archived_at: "2026-01-01T00:00:00Z" }),
    );
    expect(
      screen.getByRole("button", { name: /^restore$/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^archive$/i }),
    ).not.toBeInTheDocument();
  });

  it("carries over steerable === false: no steer box on a paused item", () => {
    renderBar(item({ status: "paused", steerable: false }));
    expect(
      screen.queryByRole("button", { name: /^steer$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^resume$/i }),
    ).toBeInTheDocument();
  });

  it("capped: steerable false retries immediately, no composer", async () => {
    const spy = vi
      .spyOn(api, "retryWorkItem")
      .mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    renderBar(
      item({
        status: "needs_human",
        cappedOut: { cycles: 3, attempts: 3 },
        steerable: false,
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /steer.*retry|^retry$/i }),
    );
    expect(spy).toHaveBeenCalledWith("w1");
    expect(
      screen.queryByLabelText(/composer message/i),
    ).not.toBeInTheDocument();
  });

  it("budget: Steer and Raise budget both reach the item, via different composers", async () => {
    const raise = vi
      .spyOn(api, "raiseBudget")
      .mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    renderBar(
      item({
        status: "needs_human",
        budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 },
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /raise budget/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /\+\$5/i }));
    await userEvent.click(
      screen.getByRole("button", { name: /raise to \$10 and resume/i }),
    );
    expect(raise).toHaveBeenCalledWith("w1", 10);
  });

  it("question: Answer is disabled until text is entered and quotes the question", async () => {
    const spy = vi
      .spyOn(api, "resumeWorkItem")
      .mockResolvedValue({ id: "w1", node_id: "v", steer: "42" });
    renderBar(
      item({
        status: "needs_human",
        needs_context_question: "which backoff?",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^answer$/i }));
    expect(screen.getByText("which backoff?")).toBeInTheDocument();
    const submit = screen.getByRole("button", {
      name: /answer and resume/i,
      hidden: true,
    });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/composer message/i), "42");
    await userEvent.click(submit);
    expect(spy).toHaveBeenCalledWith("w1", "42");
  });

  it("a running escalation turn shows the pill, not the underlying state", () => {
    renderBar(
      item({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
      [escSession()],
      [NEEDS_HUMAN_EVENT],
    );
    expect(screen.getByTestId("escalating-pill")).toBeInTheDocument();
    expect(screen.getByText(/turn 1/)).toBeInTheDocument();
  });

  it("a finished escalation turn shows the escalated proposal card", () => {
    const done = escSession({ status: "done_with_concerns", exited_at: "t" });
    renderBar(
      item({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
      [done],
      [NEEDS_HUMAN_EVENT],
    );
    expect(screen.getByTestId("escalated-card")).toBeInTheDocument();
  });

  it("a dismissed escalation falls back to the underlying capped reason", () => {
    dismissTurn("w1", "e1");
    const done = escSession({ id: "e1", status: "done", attempt: 1 });
    renderBar(
      item({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
      [done],
      [NEEDS_HUMAN_EVENT],
    );
    expect(
      screen.getByRole("button", { name: /steer.*retry/i }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("escalated-card")).not.toBeInTheDocument();
  });

  it("clicking Dismiss on the escalated card takes it off screen without waiting for an unrelated re-render", async () => {
    const done = escSession({ id: "e9", status: "done", attempt: 1 });
    renderBar(
      item({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
      [done],
      [NEEDS_HUMAN_EVENT],
    );
    expect(screen.getByTestId("escalated-card")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByTestId("escalated-card")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /steer.*retry/i }),
    ).toBeInTheDocument();
  });

  it("escalate composer shows the prior thread on turn 2+", async () => {
    // The prior turn predates a later needs_human stop (a fresh
    // work_item_needs_human after it) -- deriveState treats it as an
    // already-resolved episode (falls through to capped), but the
    // composer's own thread still shows the full escalation history.
    const priorMsg = {
      seq: 1,
      work_item_id: "w1",
      type: "escalation_message",
      payload: { session_id: "e2", message: "look at the widget" },
      created_at: "t",
    } as KraftEvent;
    const priorSession = escSession({
      id: "e2",
      status: "done",
      attempt: 1,
      created_at: "2026-01-01T00:00:00Z",
    });
    const laterStop: KraftEvent = {
      ...NEEDS_HUMAN_EVENT,
      created_at: "2026-01-01T00:10:00Z",
    };
    renderBar(
      item({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
      [priorSession],
      [priorMsg, laterStop],
    );
    await userEvent.click(screen.getByRole("button", { name: /^escalate/i }));
    expect(screen.getByTestId("escalation-thread")).toHaveTextContent(
      "look at the widget",
    );
  });

  it("opens the steer composer inside the action bar, not as a sibling card", async () => {
    const user = userEvent.setup();
    renderBar(item({ status: "paused" }));
    await user.click(screen.getByRole("button", { name: /steer/i }));
    const bar = document.querySelector(".action-bar") as HTMLElement;
    expect(bar).toBeTruthy();
    expect(bar.querySelector("textarea")).toBeTruthy();
  });

  it("keeps the hint on one line and never wraps the bar", () => {
    // jsdom has no cascade to compute a layout from; pin the source instead,
    // the way styles.order.test.ts does.
    const css = readFileSync(join(here, "../../../styles.css"), "utf-8");
    expect(css).toMatch(/\.control-row\s*\{[^}]*flex-wrap:\s*nowrap/);
    expect(css).toMatch(
      /\.control-hint\s*\{[^}]*white-space:\s*nowrap;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis/,
    );
    expect(css).toMatch(/\.control-hint\s*\{[^}]*min-width:\s*0/);
  });
});
