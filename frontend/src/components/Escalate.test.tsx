import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { Escalate } from "./Escalate";
import type { WorkerSession, WorkItem } from "../types";

const item = { id: "w1" } as WorkItem;

const session = (over: Partial<WorkerSession>): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "escalation",
    status: "running", attempt: 1, round: 0, created_at: "t", started_at: null,
    exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null,
    model: null, ...over,
  }) as WorkerSession;

beforeEach(() => vi.restoreAllMocks());

describe("Escalate", () => {
  it("sends the typed message and collapses back to the button", async () => {
    const spy = vi.spyOn(api, "escalateWorkItem").mockResolvedValue({ id: "w1", status: "escalating" });
    render(<Escalate item={item} sessions={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "double check this diff");
    await userEvent.click(screen.getByRole("button", { name: /send to agent/i }));
    expect(spy).toHaveBeenCalledWith("w1", "double check this diff");
    expect(await screen.findByRole("button", { name: /escalate/i })).toBeInTheDocument();
  });

  it("disables send until a message is entered", async () => {
    render(<Escalate item={item} sessions={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /escalate/i }));
    expect(screen.getByRole("button", { name: /send to agent/i })).toBeDisabled();
  });

  it("surfaces an API error inline instead of silently dropping the message", async () => {
    vi.spyOn(api, "escalateWorkItem").mockRejectedValue(
      new Error("an escalation turn (s1) is already running"),
    );
    render(<Escalate item={item} sessions={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "hello");
    await userEvent.click(screen.getByRole("button", { name: /send to agent/i }));
    expect(await screen.findByText(/already running/)).toHaveClass("form-error");
  });

  it("shows the running state instead of the button when an escalation turn is in flight", () => {
    render(<Escalate item={item} sessions={[session({ status: "running" })]} />);
    expect(screen.getByText(/agent is on it/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^escalate/i })).toBeNull();
  });

  it("treats a pending (not yet started) escalation session the same as running", () => {
    render(<Escalate item={item} sessions={[session({ status: "pending" })]} />);
    expect(screen.getByText(/agent is on it/i)).toBeInTheDocument();
  });

  it("ignores a finished escalation session and offers the button again", () => {
    render(<Escalate item={item} sessions={[session({ status: "done" })]} />);
    expect(screen.getByRole("button", { name: /^escalate/i })).toBeInTheDocument();
  });

  it("opens the session's log from the running state", async () => {
    const lines = vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "s1",
      status: "running",
      lines: [],
    });
    render(<Escalate item={item} sessions={[session({ id: "esc1", status: "running" })]} />);
    await userEvent.click(screen.getByRole("button", { name: /view log/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(lines).toHaveBeenCalledWith("esc1");
  });

  it("keeps a message being composed even if a running session appears mid-draft", async () => {
    // A session poll landing a matching escalation session while the box is
    // open (e.g. sent from another tab) must not silently eat what's typed
    // here -- a stale send instead surfaces the server's own 409 (Kraft-xhft).
    const { rerender } = render(<Escalate item={item} sessions={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "still typing this");
    rerender(<Escalate item={item} sessions={[session({ status: "running" })]} />);
    expect(screen.getByLabelText("escalate message")).toHaveValue("still typing this");
  });

  it("works with no sessions prop at all, for the board's inline row", async () => {
    const spy = vi.spyOn(api, "escalateWorkItem").mockResolvedValue({ id: "w1", status: "escalating" });
    render(<Escalate item={item} />);
    await userEvent.click(screen.getByRole("button", { name: /escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "hi");
    await userEvent.click(screen.getByRole("button", { name: /send to agent/i }));
    expect(spy).toHaveBeenCalledWith("w1", "hi");
  });
});
