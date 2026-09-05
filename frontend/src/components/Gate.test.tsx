import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
});
