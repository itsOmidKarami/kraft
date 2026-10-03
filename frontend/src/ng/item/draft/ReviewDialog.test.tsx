import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewDialog } from "./ReviewDialog";
import { add, answer, mountDraft, ov, sent } from "./testkit";
import { V1 } from "../testkit";

afterEach(() => vi.unstubAllGlobals());
const DRAFT = "GET /work-items/w1/draft";
const withScan = [...V1.slice(0, 3), { id: "scan", kind: "exec" as const, gate_after: null, tasks: ["scan.run.run"], steps: [["scan.run.run"]] }, V1[3]];
const ok = [ov("merge_request.open.open_draft", { model: "opus", effort: "high" }), add("scan", "verification")];
const open = (answers: Record<string, [number, unknown]>, o: Parameters<typeof mountDraft>[2] = {}) => {
  answers[DRAFT] ??= answer(ok, [], withScan);
  return mountDraft(<ReviewDialog />, answers, { review: true, ...o });
};
const dialog = () => screen.findByRole("dialog");

describe("ReviewDialog", () => {
  it("lists one line per overridden field and per added node", async () => {
    open({});
    const d = await dialog();
    expect(within(d).getByRole("heading", { name: "Apply 3 changes to this item?" })).toBeInTheDocument();
    const lines = within(d).getByLabelText("Changes").textContent;
    expect(lines).toContain("~ merge_request.open.open_draft   model → opus");
    expect(lines).toContain("~ merge_request.open.open_draft   effort → high");
    expect(lines).toContain("+ scan   after verification · from the library (security)");
  });

  it("says when the run reaches an added node, else that overrides apply as it reaches each node", async () => {
    open({});
    expect(await screen.findByText("The run reaches scan after verification.")).toBeInTheDocument();
  });

  it("says overrides apply as the run reaches each node when nothing is added", async () => {
    open({ [DRAFT]: answer([ov("merge_request", undefined, { time_cap_minutes: 9 })]) });
    expect(await screen.findByText("They apply as the run reaches each node.")).toBeInTheDocument();
  });

  it("checks: resolves, only nodes not run, and the budget with and without a cap", async () => {
    open({});
    const d = await dialog();
    expect(within(d).getByText("✓ resolves")).toBeInTheDocument();
    expect(within(d).getByText("✓ only nodes that have not run")).toBeInTheDocument();
    expect(within(d).getByText("✓ no dollar cap on this item")).toBeInTheDocument();
  });

  it("shows the budget against its cap", async () => {
    open({ [DRAFT]: answer(ok, [], withScan, 10) });
    expect(await screen.findByText(/spent \$1\.50 of the \$10\.00 budget/)).toBeInTheDocument();
  });

  it("disables Apply with a problem, listing it; a click on it closes the dialog and selects its node", async () => {
    open({ [DRAFT]: answer(ok, [{ op: 0, message: "over the cap" }], withScan) });
    const d = await dialog();
    expect(within(d).getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(within(d).getByText("✕ 1 problem to fix first")).toBeInTheDocument();
    await userEvent.click(within(d).getByRole("button", { name: /over the cap/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByTestId("at")).toHaveTextContent("sel=merge_request");
  });

  it("disables Apply with an op the run has passed", async () => {
    open({ [DRAFT]: answer([ov("plan.write.plan", { model: "x" }, undefined, true)]) });
    const d = await dialog();
    expect(within(d).getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(within(d).getByText(/✕ 1 edit the run has passed/)).toBeInTheDocument();
  });

  it("takes the passed edits out of the draft so the rest can apply", async () => {
    const answers: Record<string, [number, unknown]> = { [DRAFT]: answer([ov("plan.write.plan", { model: "x" }, undefined, true), add("scan", "verification")], [], withScan) };
    answers["PUT /work-items/w1/draft"] = answer([add("scan", "verification")], [], withScan);
    const { calls } = open(answers);
    const d = await dialog();
    await userEvent.click(within(d).getByRole("button", { name: "Remove it from the draft" }));
    await waitFor(() => expect(sent(calls, "PUT").map((c) => c.body)).toEqual([{ ops: [{ op: "add_node", after: "verification", node: { id: "scan", extends: "security" } }] }]));
    await waitFor(() => expect(within(d).getByRole("button", { name: "Apply" })).toBeEnabled());
  });

  it("opens on Back to editing while Apply is blocked, never on Discard draft (R10b-05)", async () => {
    open({ [DRAFT]: answer([ov("plan.write.plan", { model: "x" }, undefined, true)]) });
    expect(within(await dialog()).getByRole("button", { name: "Back to editing" })).toHaveFocus();
  });

  it("moves focus to Apply once Remove it from the draft is gone with what it removed (R10b-04)", async () => {
    const answers: Record<string, [number, unknown]> = { [DRAFT]: answer([ov("plan.write.plan", { model: "x" }, undefined, true), add("scan", "verification")], [], withScan) };
    answers["PUT /work-items/w1/draft"] = answer([add("scan", "verification")], [], withScan);
    open(answers);
    const d = await dialog();
    await userEvent.click(within(d).getByRole("button", { name: "Remove it from the draft" }));
    await waitFor(() => expect(within(d).getByRole("button", { name: "Apply" })).toHaveFocus());
  });

  it("names a policy field by its Config label, not its YAML key (R10a-04)", async () => {
    open({ [DRAFT]: answer([ov("implementation", undefined, { total_time_cap_minutes: 45 })]) });
    expect(within(await dialog()).getByLabelText("Changes").textContent).toContain("~ implementation   total cap → 45m");
  });

  it("closes once removing the passed edits leaves nothing", async () => {
    open({ [DRAFT]: answer([ov("plan.write.plan", { model: "x" }, undefined, true)]), "PUT /work-items/w1/draft": answer([]) });
    await userEvent.click(within(await dialog()).getByRole("button", { name: "Remove it from the draft" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("applies once for a double click, toasts, reloads the item and closes", async () => {
    const reload = vi.fn();
    const { calls, toasts } = open({ "POST /work-items/w1/draft/apply": answer([]) }, { reload });
    const apply = within(await dialog()).getByRole("button", { name: "Apply" });
    await userEvent.dblClick(apply);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "POST")).toHaveLength(1);
    expect(reload).toHaveBeenCalled();
    expect(toasts).toContain("Applied 3 changes · the run reaches scan after verification");
  });

  it("stays open on a 409 and shows the passed edit after reading the draft again", async () => {
    const answers: Record<string, [number, unknown]> = { "POST /work-items/w1/draft/apply": [409, { detail: "moved", passed: [0] }] };
    const { calls } = open(answers);
    const d = await dialog();
    answers[DRAFT] = answer([ov("merge_request.open.open_draft", { model: "opus" }, undefined, true), add("scan", "verification")], [], V1);
    await userEvent.click(within(d).getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The run moved past 1 edit while you were reviewing.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "GET" && c.path === "/work-items/w1/draft")).toHaveLength(2);
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Apply" })).toBeDisabled());
  });

  it("shows the problems a 422 names, from the draft read again", async () => {
    const answers: Record<string, [number, unknown]> = { "POST /work-items/w1/draft/apply": [422, { detail: "1 problem(s)", problems: [{ op: 0, message: "too big" }] }] };
    open(answers);
    const d = await dialog();
    answers[DRAFT] = answer(ok, [{ op: 0, message: "too big" }], withScan);
    await userEvent.click(within(d).getByRole("button", { name: "Apply" }));
    expect(await within(screen.getByRole("dialog")).findByRole("button", { name: /too big/ })).toBeInTheDocument();
  });

  it("toasts and closes when the draft is gone (a 404)", async () => {
    const { toasts } = open({ "POST /work-items/w1/draft/apply": [404, { detail: "this work item has no draft" }] });
    await userEvent.click(within(await dialog()).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(toasts).toContain("That draft is gone");
  });

  it("toasts the server's detail and closes on an ended item", async () => {
    const { toasts } = open({ "POST /work-items/w1/draft/apply": [409, { detail: "work item is completed; its chain does not run again" }] });
    await userEvent.click(within(await dialog()).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(toasts).toContain("work item is completed; its chain does not run again");
  });

  it("asks before it discards, then deletes the draft", async () => {
    const { calls } = open({ "DELETE /work-items/w1/draft": [204, undefined] });
    await userEvent.click(within(await dialog()).getByRole("button", { name: "Discard draft" }));
    expect(sent(calls, "DELETE")).toEqual([]);
    expect(screen.getByText("Discard this draft? It cannot be brought back.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(sent(calls, "DELETE")).toHaveLength(1));
  });

  it("sends nothing for Back to editing or Escape", async () => {
    const { calls } = open({});
    await userEvent.click(within(await dialog()).getByRole("button", { name: "Back to editing" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });
});
