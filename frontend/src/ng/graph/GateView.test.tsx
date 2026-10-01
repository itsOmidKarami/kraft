import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GateView } from "./GateView";

describe("GateView", () => {
  it("labels the diamond 'you' after a reviewer, and every part is a button", async () => {
    const user = userEvent.setup();
    const cb = { onReviewer: vi.fn(), onGate: vi.fn(), doc: vi.fn(), reject: vi.fn() };
    render(
      <GateView
        gate={{ id: "review_gate", state: "current" }}
        reviewer={{ id: "auto_review", state: "done", chip: "approve", chipTone: "green" }}
        doc={{ label: "review.md", onClick: cb.doc }}
        reject={{ id: "implement", onClick: cb.reject }}
        onReviewer={cb.onReviewer}
        onGate={cb.onGate}
      />,
    );
    expect(screen.getByText("decides after the agent")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "you, gate, running" }));
    await user.click(screen.getByRole("button", { name: "auto_review, reviewer task, done" }));
    await user.click(screen.getByRole("button", { name: /review\.md/ }));
    await user.click(screen.getByRole("button", { name: "reject to implement" }));
    expect([cb.onGate, cb.onReviewer, cb.doc, cb.reject].map((f) => f.mock.calls.length)).toEqual([1, 1, 1, 1]);
  });

  it("names the diamond by the gate without a reviewer or a slot", () => {
    render(<GateView gate={{ id: "approve_plan" }} />);
    expect(screen.getByRole("button", { name: "approve_plan, gate" })).toBeInTheDocument();
    expect(screen.getByText("No message yet.")).toBeInTheDocument();
    expect(screen.queryByText("add a reviewer")).toBeNull();
  });

  it("offers the add-a-reviewer slot in the editor", async () => {
    const onAdd = vi.fn();
    render(<GateView gate={{ id: "approve_plan" }} onAdd={onAdd} />);
    expect(screen.getByRole("button", { name: /you, gate/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /add a reviewer/ }));
    expect(onAdd).toHaveBeenCalled();
  });
});
