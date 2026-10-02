import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { acceptWrites, detail, holdFetch, stubFetch } from "../testkit";
import { Thread } from "./Thread";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/escalate");

afterEach(() => vi.unstubAllGlobals());
const msg = (seq: number, thread: number, turn: number, message: string, node_id: string, auto = false) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn, message, auto }, node_id, created_at: "2026-09-13T09:00:00Z" });

describe("Thread", () => {
  it("shows each thread's turns with who wrote them, links other nodes, and replies in the thread or a new one", async () => {
    const calls = stubFetch({ ...WRITES, "GET /work-items/w1/events": [200, [msg(1, 1, 1, "Which eviction policy?", "implementation", true), msg(2, 1, 2, "Allow the change?", "verification"), { seq: 3, work_item_id: "w1", type: "node_started", payload: {}, node_id: "x", created_at: "t" }]] });
    const onNode = vi.fn();
    const reload = vi.fn();
    render(<Thread item={detail()} version="1" node="verification" reload={reload} onNode={onNode} />);
    expect(await screen.findByText("Which eviction policy?")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Thread 1" })).toHaveTextContent("thread 1 · 2 turns");
    expect(screen.getAllByText("kraft")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "open node →" }));
    expect(onNode).toHaveBeenCalledWith("implementation");
    await userEvent.type(screen.getByLabelText("Reply to the escalation"), "Accept it.");
    await userEvent.click(screen.getByRole("button", { name: "Send in a new thread" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/escalate", body: { message: "Accept it.", new_thread: true } }]);
  });

  it("sends a reply in the thread on ⌘↵, a plain ↵ starting a new line", async () => {
    const calls = stubFetch({ ...WRITES, "GET /work-items/w1/events": [200, [msg(1, 1, 1, "Allow the change?", "verification")]] });
    const reload = vi.fn();
    render(<Thread item={detail()} version="1" node="verification" reload={reload} onNode={() => {}} />);
    await userEvent.type(await screen.findByLabelText("Reply to the escalation"), "Accept it.{Enter}Then go on.");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/escalate", body: { message: "Accept it.\nThen go on.", new_thread: false } }]);
  });

  it("keeps the newest read of the thread when an older one answers late", async () => {
    const reads = holdFetch(/\/work-items\/w1\/events\?after_seq=/);
    const props = { item: detail(), node: "verification", reload: () => {}, onNode: () => {} };
    const { rerender } = render(<Thread {...props} version="1" />);
    rerender(<Thread {...props} version="2" />);
    await waitFor(() => expect(reads).toHaveLength(2));
    await act(async () => reads[1]([msg(1, 1, 1, "Why did lint fail?", "verification"), msg(2, 2, 1, "sdas", "verification")]));
    expect(await screen.findByText("sdas")).toBeInTheDocument();
    await act(async () => reads[0]([msg(1, 1, 1, "Why did lint fail?", "verification")]));
    expect(screen.getByText("sdas")).toBeInTheDocument();
  });
});
