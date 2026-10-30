import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OneOffCreateReview } from "./OneOffCreateReview";

const CUSTOM = [{ path: "add[0].node", what: "adds the node k6, running an agent", value: "run k6 against staging" }];
const EXTENDING = { parent: "default", nodes: null, origins: { k6: "added", plan: "overridden", tests: "inherited" } as const, removed: [{ id: "work_brief", kind: "exec" as const, after: null }] };
const ADDED = [{ node: "k6", harnesses: ["claude"], estimate_usd: null, estimate_runs: null }];
const props = { base: "default", custom: CUSTOM, text: "id: one-off\n", extending: EXTENDING, added: ADDED, busy: false, onApprove: () => {}, onClose: () => {} };

describe("OneOffCreateReview", () => {
  it("says what the chain adds, overrides and removes, every custom part, and the checks on each added node, as the Start review does", () => {
    render(<OneOffCreateReview {...props} />);
    const d = screen.getByRole("dialog", { name: "Approve this chain, then create the item?" });
    expect(within(d).getByText("Added").parentElement).toHaveTextContent("Added k6");
    expect(within(d).getByText("Overridden").parentElement).toHaveTextContent("Overridden plan");
    expect(within(d).getByText("Removed").parentElement).toHaveTextContent("Removed work_brief");
    expect(within(d).getByText("run k6 against staging")).toBeInTheDocument();
    expect(within(d).getByText(/k6: harness claude is allowed · no estimate yet/)).toBeInTheDocument();
    expect(within(d).queryByText(/add\[0\]/)).toBeNull();
    expect(within(d).queryByRole("alert")).toBeNull();
  });

  it.each([
    { id: "what is custom changed", next: { custom: [{ ...CUSTOM[0], value: "run k6 --everywhere" }] } },
    { id: "the text changed", next: { text: "id: one-off\n# later\n" } },
  ])("says so when a re-read finds the chain moved ($id), and shows it as it is now", ({ next }) => {
    const { rerender } = render(<OneOffCreateReview {...props} />);
    rerender(<OneOffCreateReview {...props} {...next} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The chain changed while you were reviewing");
  });

  it("shows a failed re-read's error", () => {
    render(<OneOffCreateReview {...props} error="Could not read the chain again: try again." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not read the chain again");
  });
});
