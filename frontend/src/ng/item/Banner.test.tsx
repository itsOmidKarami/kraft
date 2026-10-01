import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Banner, QuestionCard } from "./Banner";
import { detail, stubFetch } from "./testkit";

afterEach(() => vi.unstubAllGlobals());
const stop = (kind: string, over = {}) => ({ kind, node: "verification", task: null, resume_at: null, reason: null, ...over }) as never;

describe("Banner", () => {
  it("shows a gate stop with Open gate, which opens that gate", async () => {
    const onOpenGate = vi.fn();
    render(<Banner item={detail({ display_status: "needs_you", stop: stop("gate", { node: "final_review" }), pending_gate: "final_review" })} onOpenGate={onOpenGate} onRaise={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for your approval at final_review.");
    await userEvent.click(screen.getByRole("button", { name: "Open gate" }));
    expect(onOpenGate).toHaveBeenCalledWith("final_review");
  });

  it.each(["cap", "budget"])("shows a %s stop with Raise cap", async (kind) => {
    const onRaise = vi.fn();
    render(<Banner item={detail({ display_status: "needs_you", stop: stop(kind, { reason: "Running time hit its 8h cap" }) })} onOpenGate={() => {}} onRaise={onRaise} />);
    expect(screen.getByRole("status")).toHaveTextContent("Running time hit its 8h cap at verification.");
    await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
    expect(onRaise).toHaveBeenCalled();
  });

  it.each([
    ["escalated", null],
    // An escalation ran on a cap stop and nobody is needed: only the badge (Decisions §4).
    ["escalated", "cap"],
    ["needs_you", "question"],
    ["needs_you", "conflict"],
    ["failed", "failed"],
    ["waiting", "rate_limit"],
  ])("shows nothing for %s (%s)", (display_status, kind) => {
    const { container } = render(<Banner item={detail({ display_status: display_status as never, stop: kind ? stop(kind) : null })} onOpenGate={() => {}} onRaise={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("QuestionCard", () => {
  const asked = detail({ display_status: "needs_you", stop: stop("question", { task: "verification.escalation.escalation" }), needs_context_question: "Allow the API change?" });

  it("answers with Send & resume, the answer as the steer", async () => {
    const calls = stubFetch();
    const reload = vi.fn();
    render(<QuestionCard item={asked} compact={false} reload={reload} onOpenThread={() => {}} />);
    expect(screen.getByText("“Allow the API change?”")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your answer"), "Accept the finding.");
    await userEvent.click(screen.getByRole("button", { name: "Send & resume" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/resume", body: { steer: "Accept the finding." } }]);
  });

  it("is one line with Open thread in a node view", async () => {
    const onOpenThread = vi.fn();
    render(<QuestionCard item={asked} compact reload={() => {}} onOpenThread={onOpenThread} />);
    expect(screen.queryByLabelText("Your answer")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("escalation is asking you on verification: “Allow the API change?”");
    await userEvent.click(screen.getByRole("button", { name: "Open thread" }));
    expect(onOpenThread).toHaveBeenCalled();
  });
});
