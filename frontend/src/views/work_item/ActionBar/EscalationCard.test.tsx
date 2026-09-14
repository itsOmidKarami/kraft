import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EscalatingPill, dismissTurn, dismissedTurnId } from "./EscalationCard";

// The escalated report itself (markdown, the summary document, the Stop agent
// and message fallbacks) renders in the item card: ItemCard.test.tsx.
describe("EscalationCard", () => {
  it("shows the turn number", () => {
    render(<EscalatingPill turn={2} />);
    expect(screen.getByText(/turn 2/)).toBeInTheDocument();
  });

  it("auto: true renders the Auto-escalated pill", () => {
    render(<EscalatingPill turn={1} auto />);
    expect(screen.getByText(/auto-escalated · turn 1/i)).toBeInTheDocument();
  });

  it("auto: false renders today's 'Agent is on it' copy unchanged", () => {
    render(<EscalatingPill turn={1} auto={false} />);
    expect(screen.getByText(/agent is on it · turn 1/i)).toBeInTheDocument();
  });

  it("dismissing an escalation is remembered per item and session", () => {
    dismissTurn("w1", "e1");
    expect(dismissedTurnId("w1")).toBe("e1");
  });
});
