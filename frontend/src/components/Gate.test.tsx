import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import type { WorkerSession } from "../types";
import { Gate } from "./Gate";

const item = { id: "w1" } as never;

describe("Gate", () => {
  it("approve calls the API", async () => {
    const spy = vi.spyOn(api, "approveGate").mockResolvedValue();
    render(<Gate item={item} gate="spec_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(spy).toHaveBeenCalledWith("w1", "spec_approval");
  });

  it("reject submit is disabled until a note is entered", async () => {
    const spy = vi.spyOn(api, "rejectGate").mockResolvedValue();
    render(<Gate item={item} gate="spec_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /^reject/i }));
    const submit = screen.getByRole("button", { name: /reject and re-plan/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("reject note"), "redo the spec");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(spy).toHaveBeenCalledWith("w1", "spec_approval", "redo the spec");
  });

  it("surfaces an API error inline and re-enables the button", async () => {
    vi.spyOn(api, "approveGate").mockRejectedValue(
      new Error("gate 'x' is not pending"),
    );
    render(<Gate item={item} gate="spec_approval" />);
    const approve = screen.getByRole("button", { name: /approve/i });
    await userEvent.click(approve);
    expect(await screen.findByText(/not pending/)).toHaveClass("form-error");
    expect(approve).toBeEnabled();
  });

  const reviewItem = {
    id: "w1",
    chain_definition: {
      template_id: "default",
      nodes: [
        { id: "implementation", tasks: [], gate_after: null },
        {
          id: "human_review",
          tasks: [],
          gate_after: "human_review_approval",
          reject_to: "implementation",
        },
      ],
    },
  } as never;

  it("names the node a rejection sends the chain back to", async () => {
    render(<Gate item={reviewItem} gate="human_review_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /^reject/i }));
    expect(
      screen.getByRole("button", { name: /reject and send back/i }),
    ).toBeInTheDocument();
    // exact string, so this matches the <code> and not every ancestor of it
    expect(screen.getByText("implementation").tagName).toBe("CODE");
  });

  it("keeps 'Reject and re-plan' for a gate that re-runs its own node", async () => {
    render(<Gate item={item} gate="spec_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /^reject/i }));
    expect(screen.getByRole("button", { name: /reject and re-plan/i })).toBeInTheDocument();
  });

  it("the inline variant names the gate and offers the same two actions", async () => {
    render(<Gate item={item} gate="plan_approval" variant="inline" />);
    // the row reads as a phrase; the raw gate name is the tooltip
    expect(screen.getByText("approve the plan")).toBeInTheDocument();
    expect(screen.getByTitle("plan_approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^reject/i }));
    expect(screen.getByLabelText("reject note")).toBeInTheDocument();
  });

  const deferred = [
    { severity: "minor", message: "naming nit", file: "a.py", line: 3, source_plugin: "fake" },
  ];

  it("lists deferred minor findings at the gate", () => {
    render(<Gate item={item} gate="human_review_approval" deferred={deferred} />);
    expect(screen.getByText(/naming nit/)).toBeInTheDocument();
    expect(screen.getByText(/a\.py:3/)).toBeInTheDocument();
  });

  it("renders no list when there are none", () => {
    const { container } = render(
      <Gate item={item} gate="human_review_approval" deferred={[]} />,
    );
    expect(container.querySelector(".gate-deferred")).toBeNull();
  });

  it("shows concerns at the review gate, in the same panel as deferred findings", () => {
    render(
      <Gate
        item={item}
        gate="human_review_approval"
        deferred={deferred}
        concerns={["the retry path is untested"]}
      />,
    );
    expect(screen.getByText(/the retry path is untested/)).toBeInTheDocument();
    expect(screen.getByText(/naming nit/)).toBeInTheDocument();
    // one panel, not two competing ones
    expect(document.querySelectorAll(".gate-deferred")).toHaveLength(1);
  });

  it("renders no list when there are no findings and no concerns", () => {
    const { container } = render(
      <Gate item={item} gate="human_review_approval" deferred={[]} concerns={[]} />,
    );
    expect(container.querySelector(".gate-deferred")).toBeNull();
  });

  it("suppresses Approve inline for human_review_approval and links to the detail view instead", () => {
    render(
      <MemoryRouter>
        <Gate item={item} gate="human_review_approval" variant="inline" />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.getByRole("link", { name: /review to approve/i })).toHaveAttribute(
      "href",
      "/work-items/w1",
    );
  });

  it("offers escalate alongside approve/reject, both variants", () => {
    render(<Gate item={item} gate="spec_approval" />);
    expect(screen.getByRole("button", { name: /^escalate/i })).toBeInTheDocument();
    render(<Gate item={item} gate="spec_approval" variant="inline" />);
    expect(screen.getAllByRole("button", { name: /^escalate/i })).toHaveLength(2);
  });

  it("offers Skip on a pending gate, card variant", () => {
    render(<Gate item={item} gate="spec_approval" />);
    expect(screen.getByRole("button", { name: /skip/i })).toBeInTheDocument();
  });

  it("does not offer Skip on the inline variant", () => {
    render(<Gate item={item} gate="spec_approval" variant="inline" />);
    expect(screen.queryByRole("button", { name: /skip/i })).not.toBeInTheDocument();
  });

  it("escalate sends through the message box, same as the standalone control", async () => {
    const spy = vi.spyOn(api, "escalateWorkItem").mockResolvedValue({ id: "w1", status: "escalating" });
    render(<Gate item={item} gate="spec_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /^escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "is this right?");
    await userEvent.click(screen.getByRole("button", { name: /send to agent/i }));
    expect(spy).toHaveBeenCalledWith("w1", "is this right?");
  });
});

const session = (over: Partial<WorkerSession> = {}) =>
  ({
    id: "s1",
    work_item_id: "w1",
    node_id: "verify",
    hook_point: "on.test.run",
    status: "done",
    attempt: 1,
    round: 0,
    created_at: "",
    started_at: null,
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
    head_sha: "abc1234",
    ...over,
  }) as WorkerSession;

describe("Gate test-evidence freshness", () => {
  it("says tests passed on the current commit", () => {
    const freshItem = { id: "w1", head_sha: "abc1234" } as never;
    render(<Gate item={freshItem} gate="human_review_approval" sessions={[session()]} />);
    expect(screen.getByText(/tests passed on abc1234/i)).toBeInTheDocument();
  });

  it("calls the evidence stale when HEAD has moved since", () => {
    const movedItem = { id: "w1", head_sha: "def5678" } as never;
    render(<Gate item={movedItem} gate="human_review_approval" sessions={[session()]} />);
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
    expect(screen.getByText(/abc1234/)).toBeInTheDocument();
  });

  it("says nothing when there is no test session to report on", () => {
    const freshItem = { id: "w1", head_sha: "abc1234" } as never;
    render(<Gate item={freshItem} gate="human_review_approval" sessions={[]} />);
    expect(screen.queryByText(/tests (passed|last ran)/i)).not.toBeInTheDocument();
  });
});
