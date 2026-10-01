import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DraftNotes } from "./DraftNotes";
import { add, answer, mountDraft, ov, sent } from "./testkit";
import { V1 } from "../testkit";

afterEach(() => vi.unstubAllGlobals());
const withScan = [...V1.slice(0, 3), { id: "scan", kind: "exec" as const, gate_after: null, tasks: ["scan.run.run"], steps: [["scan.run.run"]] }, V1[3]];

describe("DraftNotes", () => {
  it("shows nothing when no op has a problem or has been passed", async () => {
    const { calls } = mountDraft(<DraftNotes />, { "GET /work-items/w1/draft": answer([ov("merge_request", undefined, { time_cap_minutes: 5 })]) });
    await waitFor(() => expect(calls.some((c) => c.path === "/work-items/w1/draft")).toBe(true));
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("offers Move after <current> and Remove on an added node the run has passed", async () => {
    mountDraft(<DraftNotes />, { "GET /work-items/w1/draft": answer([add("scan", "plan", true)]) });
    expect(await screen.findByText("The run has passed this point.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move after verification" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("moves only that op's `after` to the current node, and sends nothing before the click", async () => {
    const ops = [ov("merge_request", undefined, { time_cap_minutes: 5 }), add("scan", "plan", true)];
    const { calls } = mountDraft(<DraftNotes />, { "GET /work-items/w1/draft": answer(ops), "PUT /work-items/w1/draft": answer([ops[0], add("scan", "verification")], [], withScan) });
    await screen.findByRole("button", { name: "Move after verification" });
    expect(sent(calls, "PUT")).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Move after verification" }));
    await waitFor(() => expect(sent(calls, "PUT").map((c) => c.body)).toEqual([{ ops: [{ op: "override", path: "merge_request", policy: { time_cap_minutes: 5 } }, { op: "add_node", after: "verification", node: { id: "scan", extends: "security" } }] }]));
  });

  it("removes only the op it names", async () => {
    const ops = [ov("merge_request", undefined, { time_cap_minutes: 5 }), add("scan", "plan", true)];
    const { calls } = mountDraft(<DraftNotes />, { "GET /work-items/w1/draft": answer(ops), "PUT /work-items/w1/draft": answer([ops[0]]) });
    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(sent(calls, "PUT").map((c) => c.body)).toEqual([{ ops: [{ op: "override", path: "merge_request", policy: { time_cap_minutes: 5 } }] }]));
  });

  it("gives a passed override Remove only", async () => {
    mountDraft(<DraftNotes />, { "GET /work-items/w1/draft": answer([ov("verification.checks.lint", { model: "x" }, undefined, true)]) });
    await screen.findByRole("button", { name: "Remove" });
    expect(screen.queryByRole("button", { name: /^Move after/ })).toBeNull();
  });

  it("shows a server problem's own message with Remove", async () => {
    mountDraft(<DraftNotes />, { "GET /work-items/w1/draft": answer([ov("merge_request.open.open_draft", { command: "make" })], [{ op: 0, message: "task_config.command: not a field" }]) });
    expect(await screen.findByText("task_config.command: not a field")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("scopes a node's pane to that node's own issues", async () => {
    mountDraft(<DraftNotes node="plan" />, { "GET /work-items/w1/draft": answer([ov("plan.write.plan", { model: "x" }, undefined, true), ov("verification.checks.lint", { model: "x" }, undefined, true)]) });
    expect(await screen.findAllByRole("group")).toHaveLength(1);
    expect(screen.getByRole("group").getAttribute("aria-label")).toContain("plan.write.plan");
  });

  it("says where an added node was added, and Remove takes it and the ops inside it out", async () => {
    const ops = [add("scan", "verification"), ov("scan.run.run", { model: "x" }), ov("merge_request", undefined, { time_cap_minutes: 5 })];
    const { calls } = mountDraft(<DraftNotes node="scan" />, { "GET /work-items/w1/draft": answer(ops, [], withScan), "PUT /work-items/w1/draft": answer([ops[2]]) });
    expect(await screen.findByText(/Added in this item's draft, after/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove from the draft" }));
    await waitFor(() => expect(sent(calls, "PUT").map((c) => c.body)).toEqual([{ ops: [{ op: "override", path: "merge_request", policy: { time_cap_minutes: 5 } }] }]));
  });
});
