import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { KraftEvent, WorkerSession } from "../../../types";
import {
  EscalatedCard,
  EscalatingPill,
  dismissTurn,
  dismissedTurnId,
} from "./EscalationCard";
import { item } from "./testFixtures";

describe("EscalationCard", () => {
  it("shows the turn number and a Stop agent button while running", () => {
    render(<EscalatingPill turn={2} onStop={() => {}} busy={false} />);
    expect(screen.getByText(/turn 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop agent/i })).toBeEnabled();
  });

  it("stop agent calls the stop endpoint", async () => {
    const spy = vi
      .spyOn(api, "stopEscalation")
      .mockResolvedValue({ id: "w1", session_id: "e1", status: "paused" });
    const onStop = () => api.stopEscalation("w1");
    render(<EscalatingPill turn={1} onStop={onStop} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: /stop agent/i }));
    expect(spy).toHaveBeenCalledWith("w1");
  });

  it("auto: true renders the Auto-escalated pill and its hint", () => {
    render(<EscalatingPill turn={1} auto onStop={() => {}} busy={false} />);
    expect(screen.getByText(/auto-escalated · turn 1/i)).toBeInTheDocument();
    expect(screen.getByText(/fired automatically/i)).toBeInTheDocument();
  });

  it("auto: false renders today's 'Agent is on it' copy unchanged", () => {
    render(<EscalatingPill turn={1} auto={false} onStop={() => {}} busy={false} />);
    expect(screen.getByText(/agent is on it · turn 1/i)).toBeInTheDocument();
    expect(screen.getByText(/one escalation turn at a time/i)).toBeInTheDocument();
  });

  it("escalated card shows the exit event's concerns text and applies it as a steer on retry", async () => {
    const spy = vi
      .spyOn(api, "retryWorkItem")
      .mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    const sess = {
      id: "e1",
      work_item_id: "w1",
      node_id: "v",
      hook_point: "escalation",
      status: "done_with_concerns",
      attempt: 1,
      round: 0,
      created_at: "t",
      started_at: "t",
      exited_at: "t",
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      wall_ms: null,
      model: null,
      head_sha: null,
    } as WorkerSession;
    const evs = [
      {
        seq: 1,
        work_item_id: "w1",
        type: "worker_session_exited",
        payload: {
          session_id: "e1",
          status: "done_with_concerns",
          concerns: "renamed the field",
        },
        created_at: "t",
      },
    ] as KraftEvent[];
    render(
      <EscalatedCard
        item={item()}
        session={sess}
        events={evs}
        onOpenReply={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("renamed the field")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /apply as steer/i }),
    );
    expect(spy).toHaveBeenCalledWith(
      "w1",
      "From escalation: renamed the field",
    );
  });

  it("renders the escalation report as markdown, not literal characters", () => {
    const sess = {
      id: "e1",
      work_item_id: "w1",
      node_id: "v",
      hook_point: "escalation",
      status: "done_with_concerns",
      attempt: 1,
      round: 0,
      created_at: "t",
      started_at: "t",
      exited_at: "t",
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      wall_ms: null,
      model: null,
      head_sha: null,
    } as WorkerSession;
    const evs = [
      {
        seq: 1,
        work_item_id: "w1",
        type: "worker_session_exited",
        payload: {
          session_id: "e1",
          status: "done_with_concerns",
          concerns: "## Fixed\n\n- **one** thing\n",
        },
        created_at: "t",
      },
    ] as KraftEvent[];
    render(
      <EscalatedCard
        item={item()}
        session={sess}
        events={evs}
        onOpenReply={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { name: "Fixed" })).toBeInTheDocument();
    expect(screen.queryByText(/## Fixed/)).toBeNull();
  });

  it("falls back to the session_summary_ref document when a clean 'done' turn carries no concerns/question, and offers Apply", async () => {
    const retrySpy = vi
      .spyOn(api, "retryWorkItem")
      .mockResolvedValue({ id: "w1", node_id: "v", loop: "l", steer: null });
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [
        {
          document_id: "d1",
          repo: "/r",
          title: "Escalation report",
          kind: "sessions",
          source_kind: "session_summary",
          path: ".engineering/sessions/e1.md",
          node_id: "v",
          hook_point: "escalation",
          worker_session_id: "e1",
          attachment_kind: null,
          indexed_at: "t",
        },
      ],
    });
    vi.spyOn(api, "getDocument").mockResolvedValue({
      id: "d1",
      repo: "/r",
      source_kind: "session_summary",
      kind: "sessions",
      title: "Escalation report",
      path: ".engineering/sessions/e1.md",
      content: "Proposed renaming the field to fix the mismatch.",
      metadata: {},
      source_created_at: null,
      source_updated_at: null,
      indexed_at: "t",
      links: [],
    });
    const sess = {
      id: "e1",
      work_item_id: "w1",
      node_id: "v",
      hook_point: "escalation",
      status: "done",
      attempt: 1,
      round: 0,
      created_at: "t",
      started_at: "t",
      exited_at: "t",
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      wall_ms: null,
      model: null,
      head_sha: null,
      session_summary_ref: ".engineering/sessions/e1.md",
    } as WorkerSession;
    const evs = [
      {
        seq: 1,
        work_item_id: "w1",
        type: "worker_session_exited",
        payload: { session_id: "e1", status: "done" },
        created_at: "t",
      },
    ] as KraftEvent[];
    render(
      <EscalatedCard
        item={item()}
        session={sess}
        events={evs}
        onOpenReply={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(
      await screen.findByText("Proposed renaming the field to fix the mismatch."),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /apply as steer/i }),
    );
    expect(retrySpy).toHaveBeenCalledWith(
      "w1",
      "From escalation: Proposed renaming the field to fix the mismatch.",
    );
  });

  it("dismissing an escalation is remembered per item and session", () => {
    dismissTurn("w1", "e1");
    expect(dismissedTurnId("w1")).toBe("e1");
  });

  it("dismiss notifies the caller so the card can come off screen immediately", async () => {
    const sess = {
      id: "e1",
      work_item_id: "w1",
      node_id: "v",
      hook_point: "escalation",
      status: "done",
      attempt: 1,
      round: 0,
      created_at: "t",
      started_at: "t",
      exited_at: "t",
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      wall_ms: null,
      model: null,
      head_sha: null,
    } as WorkerSession;
    const onDismiss = vi.fn();
    render(
      <EscalatedCard
        item={item()}
        session={sess}
        events={[]}
        onOpenReply={() => {}}
        onDismiss={onDismiss}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalled();
    expect(dismissedTurnId("w1")).toBe("e1");
  });

  it("falls back to the stop reason, not a bare 'no summary', for a turn stopped via Stop agent -- and doesn't offer Apply for it", () => {
    const sess = {
      id: "e2",
      work_item_id: "w1",
      node_id: "v",
      hook_point: "escalation",
      status: "paused",
      attempt: 1,
      round: 0,
      created_at: "t",
      started_at: "t",
      exited_at: "t",
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      wall_ms: null,
      model: null,
      head_sha: null,
    } as WorkerSession;
    render(
      <EscalatedCard
        item={item()}
        session={sess}
        events={[]}
        onOpenReply={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText(/stopped by stop agent/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /apply as steer/i }),
    ).not.toBeInTheDocument();
  });
});
