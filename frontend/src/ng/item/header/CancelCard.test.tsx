import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { acceptWrites, stubFetch } from "../testkit";
import { CancelCard } from "./CancelCard";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/cancel");

afterEach(() => vi.unstubAllGlobals());

const preview = (mr: "open" | "merged" | null) => ({
  running: { node: "verification", task: "verification.review.code_review", attempt: 2, started_at: new Date(Date.now() - 41_000).toISOString() },
  kept: { branch: "kraft/cb59", worktree: "/wt", findings: 0, threads: 1 },
  mr: mr ? { ref: 142, url: "https://forge/142", state: mr } : null,
  spend: { spent_usd: 2.41, cap_usd: 5 },
});
const open = (answers: Parameters<typeof stubFetch>[0]) => {
  const calls = stubFetch({ ...WRITES, ...answers });
  const anchor = createRef<HTMLDivElement>();
  const onDone = vi.fn();
  const onClose = vi.fn();
  render(<><div ref={anchor} /><CancelCard id="w1" anchor={anchor} onClose={onClose} onDone={onDone} /></>);
  return { calls, onDone, onClose };
};

describe("CancelCard", () => {
  it("names how long the attempt it would stop has run, in the words Mark complete uses (WI-14)", async () => {
    open({ "GET /work-items/w1/cancel-preview": [200, preview(null)] });
    expect(await screen.findByText("code_review, attempt 2 (41s). That attempt's work is lost.")).toBeInTheDocument();
  });

  it.each([true, false])("sends POST /cancel with the reason and close_mr=%s, and never the deleting route", async (close) => {
    const { calls, onDone } = open({ "GET /work-items/w1/cancel-preview": [200, preview("open")] });
    await screen.findByText(/code_review, attempt 2/);
    if (close) await userEvent.click(screen.getByLabelText(/Also close !142 on the forge/));
    await userEvent.type(screen.getByLabelText("Reason"), "superseded");
    await userEvent.click(screen.getByRole("button", { name: "Cancel item" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes).toEqual([{ method: "POST", path: "/work-items/w1/cancel", body: { reason: "superseded", close_mr: close } }]);
  });

  // R12b-05: Escape closed the card and dropped the reason; an empty card still closes on it.
  it("keeps a typed reason on Escape, and closes on one while empty", async () => {
    const { onClose } = open({ "GET /work-items/w1/cancel-preview": [200, preview(null)] });
    const reason = screen.getByLabelText("Reason");
    await userEvent.type(reason, "Not needed any more");
    fireEvent.keyDown(reason, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(reason).toHaveValue("Not needed any more");
    await userEvent.clear(reason);
    fireEvent.keyDown(reason, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("cancels on ⌘↵ from the reason once it has one", async () => {
    const { calls, onDone } = open({ "GET /work-items/w1/cancel-preview": [200, preview(null)] });
    await screen.findByText(/code_review, attempt 2/);
    await userEvent.click(screen.getByLabelText("Reason"));
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.type(screen.getByLabelText("Reason"), "superseded");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/cancel", body: { reason: "superseded", close_mr: false } }]);
  });

  it("says the worktree goes at archive, and an unpushed branch does not", async () => {
    open({ "GET /work-items/w1/cancel-preview": [200, preview(null)] });
    expect(await screen.findByText(/branch kraft\/cb59, the worktree until it is archived/)).toBeInTheDocument();
    expect(screen.getByText(/Archiving keeps the branch if it has commits that were never pushed/)).toBeInTheDocument();
  });

  it("offers closing the MR only while it is open", async () => {
    open({ "GET /work-items/w1/cancel-preview": [200, preview("merged")] });
    await screen.findByText(/code_review, attempt 2/);
    expect(screen.queryByLabelText(/Also close/)).toBeNull();
  });

  it("keeps Cancel item disabled until there is a reason, and shows the server's refusal in the card", async () => {
    const { onDone } = open({ "GET /work-items/w1/cancel-preview": [200, preview(null)], "POST /work-items/w1/cancel": [409, { detail: "work item already ended" }] });
    await screen.findByText(/code_review, attempt 2/);
    const go = screen.getByRole("button", { name: "Cancel item" });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "   ");
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "x");
    await userEvent.click(go);
    expect(await screen.findByRole("alert")).toHaveTextContent("work item already ended");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("says so when the cancel landed but the MR did not close", async () => {
    open({ "GET /work-items/w1/cancel-preview": [200, preview("open")], "POST /work-items/w1/cancel": [200, { close_mr: { ok: false, error: "403 from forge" } }] });
    await screen.findByText(/code_review, attempt 2/);
    await userEvent.click(screen.getByLabelText(/Also close/));
    await userEvent.type(screen.getByLabelText("Reason"), "r");
    await userEvent.click(screen.getByRole("button", { name: "Cancel item" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cancelled. The merge request was not closed: 403 from forge");
  });
});
