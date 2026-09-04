import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
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

  it("labels the reject action 'Reject and stop' when there is nothing to loop back to", async () => {
    render(<Gate item={item} gate="human_review_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /^reject/i }));
    expect(screen.getByRole("button", { name: /reject and stop/i })).toBeInTheDocument();
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
});
