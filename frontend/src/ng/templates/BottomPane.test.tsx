import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigDraft } from "./draft/useConfigDraft";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import { mergeView } from "./draft/fixture.merge";
import type { DraftView } from "./draft/types";
import { resolvedNode } from "./draft/view";
import { BOTTOM, BottomPane, clampBottom, handlerOf, READ_ONLY_PLAN, READ_ONLY_RECOVERY, rewindLine, tabsFor, WRITING_RECOVERY, type BottomTab } from "./BottomPane";

function mount(node: string, o: { sel?: string; tab?: BottomTab; open?: boolean; view?: DraftView } = {}) {
  const view = o.view ?? DEFAULT_VIEW;
  const draft = {
    view,
    ops: vi.fn(() => Promise.resolve({ status: 200, body: { ops: [] } })),
    resolvedNode: (id: string) => resolvedNode(view.result, id),
  } as unknown as ConfigDraft;
  const cb = { onTab: vi.fn(), onToggle: vi.fn(), onPick: vi.fn(), onOpen: vi.fn(), onLeave: vi.fn() };
  render(<BottomPane scope={{ area: "chains", key: "default" }} node={node} draft={draft} selPath={o.sel ?? node} tab={o.tab ?? null} open={o.open ?? true} canvasH={600} right={380} {...cb} />);
  return { draft, ...cb };
}
const tabs = () => within(screen.getByRole("tablist")).getAllByRole("tab").map((t) => t.textContent);

beforeEach(() => localStorage.clear());

describe("bottom pane", () => {
  it.each([
    { id: "a main task: On failure alone", sel: "verification.review.code_review", node: "verification", of: {}, want: ["on_failure"] },
    { id: "a node: all three", sel: "verification", node: "verification", of: {}, want: ["on_failure", "fix_loop", "escalation"] },
    { id: "on base change set: On conflict too", sel: "verification.fix_loop", node: "verification", of: { conflict: true }, want: ["on_failure", "fix_loop", "escalation", "on_conflict"] },
    { id: "a merge node: On merge rejected first, no Fix loop", sel: "merge", node: "merge", of: { merges: true }, want: ["on_merge_rejected", "on_failure", "escalation"] },
    { id: "a merge node with a fix loop: it shows, to be removed", sel: "merge", node: "merge", of: { merges: true, fixLoop: true, conflict: true }, want: ["on_merge_rejected", "on_failure", "fix_loop", "escalation", "on_conflict"] },
    { id: "a merge node's main task: On failure alone", sel: "merge.main.merge", node: "merge", of: { merges: true }, want: ["on_failure"] },
  ])("tabs for $id", ({ sel, node, of, want }) => {
    expect(tabsFor(sel, node, of)).toEqual(want);
  });

  it("draws On conflict once on base change is set", () => {
    mount("merge_request_feedback");
    expect(tabs()).toEqual(["On failure", "Fix loop", "Escalation", "On conflict"]);
  });

  it.each([
    { id: "a node that merges", policy: false },
    { id: "a node that declares the policy", policy: true },
  ])("leads with On merge rejected, and opens on it, on $id", ({ policy }) => {
    mount("merge", { view: mergeView({ policy }) });
    expect(tabs()).toEqual(["On merge rejected", "On failure", "Escalation"]);
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent("On merge rejected");
  });

  it("names the handler a path sits in", () => {
    expect(handlerOf("verification.fix_loop.judge")).toBe("fix_loop");
    expect(handlerOf("merge_request_feedback.on_failure.repair.repair_feedback")).toBe("on_failure");
    expect(handlerOf("x.on_base_changed.on_conflict.main.t")).toBe("on_conflict");
    expect(handlerOf("merge.on_merge_rejected.repair.main.repair")).toBe("on_merge_rejected");
    expect(handlerOf("verification.review.code_review")).toBeNull();
  });

  it("collapsed, it is the tab bar alone; a tab expands it", async () => {
    const { onToggle, onTab } = mount("verification", { open: false });
    expect(screen.queryByRole("separator")).toBeNull();
    expect(screen.getByRole("region", { name: "Failure handling" })).toHaveStyle({ height: `${BOTTOM.BAR}px` });
    await userEvent.click(screen.getByRole("tab", { name: "Escalation" }));
    expect(onToggle).toHaveBeenCalled();
    expect(onTab).toHaveBeenCalledWith("escalation");
  });

  it("picking the Fix loop tab selects the loop", async () => {
    const { onPick } = mount("verification");
    await userEvent.click(screen.getByRole("tab", { name: "Fix loop" }));
    expect(onPick).toHaveBeenCalledWith("verification.fix_loop");
  });

  it("draws the node's on_failure handler and switches its level from a main task", async () => {
    mount("merge_request_feedback");
    expect(screen.getByRole("group", { name: "On failure" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "repair_feedback, agent task" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove handler" })).toBeInTheDocument();
  });

  it("an empty state's action adds the handler, then the task menu fills its main step", async () => {
    const { draft } = mount("verification", { sel: "verification.review.code_review" });
    const path = screen.getByText("Handler for").closest(".bp-path") as HTMLElement;
    expect(within(path).getAllByRole("button").map((b) => b.textContent)).toEqual(["code_review", "review", "verification"]);
    expect(screen.getByText(/No handler for code_review/)).toBeInTheDocument();
    await userEvent.click(within(path).getByRole("button", { name: "review" }));
    expect(screen.getByText(/No handler for step review/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a recovery plan" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "add_handler", path: "verification.review", kind: "on_failure" }]);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Blank subprocess task" }));
    expect(draft.ops).toHaveBeenLastCalledWith([{ op: "add_task", container: "verification.review.on_failure", step: "main", id: "subprocess", kind: "subprocess" }]);
  });

  it("an escalation is added from the agent-only menu into its slot", async () => {
    const { draft } = mount("verification", { tab: "escalation" });
    expect(screen.getByText("Without one, Kraft auto-escalates at once (policy).")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add one" }));
    const menu = await screen.findByRole("dialog", { name: "Add an escalation task" });
    expect(within(menu).queryByRole("menuitem", { name: "Blank forge task" })).toBeNull();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Blank agent task" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "add_task", slot: "escalation", node: "verification", kind: "agent" }]);
  });

  it("a merge rejection's empty state adds the whole policy, then the task menu fills its repair", async () => {
    const { draft } = mount("merge", { tab: "on_merge_rejected", view: mergeView({ policy: false }) });
    expect(screen.getByText("Without one, the drop stops the item for you. After the repair, the item restarts from an earlier node instead of retrying the merge in place.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add one" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "add_handler", path: "merge", kind: "on_merge_rejected" }]);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Blank agent task" }));
    expect(draft.ops).toHaveBeenLastCalledWith([{ op: "add_task", container: "merge.on_merge_rejected.repair", step: "main", id: "agent", kind: "agent" }]);
  });

  it("draws a merge rejection's repair and removes the policy with it", async () => {
    const { draft, onLeave } = mount("merge", { tab: "on_merge_rejected", view: mergeView() });
    expect(within(screen.getByRole("group", { name: "On merge rejected" })).getByRole("button", { name: "repair, agent task" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove repair" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "remove_handler", path: "merge", kind: "on_merge_rejected" }]);
    await vi.waitFor(() => expect(onLeave).toHaveBeenCalled());
  });

  it("says over a merge rejection's repair where the item restarts, and the gate it passes again", () => {
    mount("merge", { view: mergeView() });
    expect(screen.getByText("After the repair, the item restarts from merge_request_feedback and goes through final_review again, instead of retrying the merge in place.")).toHaveClass("bp-note");
  });

  const row = (ids: string) => ids.split(" ").map((id) => ({ id, gate: id.startsWith("g") }));
  it.each([
    { id: "no gate between", nodes: "a b m", restart: "b", want: "After the repair, the item restarts from b, instead of retrying the merge in place." },
    { id: "a gate before the restart is not passed again", nodes: "g0 a g1 b m", restart: "a", want: "After the repair, the item restarts from a and goes through g1 again, instead of retrying the merge in place." },
    { id: "three gates", nodes: "a g1 g2 b g3 m", restart: "a", want: "After the repair, the item restarts from a and goes through g1, g2 and g3 again, instead of retrying the merge in place." },
    { id: "no restart yet", nodes: "a m", restart: undefined, want: "After the repair, the item restarts from an earlier node instead of retrying the merge in place." },
  ])("words a merge rejection's rewind: $id", ({ nodes, restart, want }) => {
    expect(rewindLine(row(nodes), "m", restart)).toBe(want);
  });

  it.each([
    { id: "in its empty state, with none yet", recovery: false, note: READ_ONLY_RECOVERY },
    { id: "over the one it has", recovery: true, note: READ_ONLY_RECOVERY },
  ])("a merge node's on_failure says it may only read: $id", ({ recovery, note }) => {
    mount("merge", { tab: "on_failure", view: mergeView({ recovery }) });
    expect(screen.getByText(note)).toBeInTheDocument();
    expect(screen.queryByText(/goes straight to the fix loop/)).toBeNull();
  });

  it("a node that merges without the policy keeps the usual on_failure", () => {
    mount("merge", { tab: "on_failure", view: mergeView({ policy: false }) });
    expect(screen.getByText("Without one, the failure goes straight to the fix loop.")).toBeInTheDocument();
    expect(screen.queryByText(READ_ONLY_RECOVERY)).toBeNull();
  });

  it("a problem on a recovery's step takes the note, in the server's words", () => {
    const view = mergeView({ recovery: true });
    const problems = [{ path: "merge.on_failure.main", field: null, message: "Value error, 'main' is a reserved path segment", file: "chains/default.yaml", line: null, col: null }];
    mount("merge", { tab: "on_failure", view: { ...view, result: { ...view.result, problems } } });
    expect(screen.getByText("'main' is a reserved path segment")).toHaveClass("bp-note", "is-bad");
    expect(screen.queryByText(READ_ONLY_RECOVERY)).toBeNull();
  });

  it.each([
    { id: "a merge node's, read only on the plan", view: () => mergeView({ recovery: true }), note: READ_ONLY_RECOVERY, bad: false, on: true, locked: true },
    // The server refuses it for the whole chain, a problem with no path the pane could mark.
    { id: "a merge node's that may write", view: () => mergeView({ recovery: "writes" }), note: WRITING_RECOVERY, bad: true, on: false, locked: false },
    { id: "another node's, read only on the plan", view: () => mergeView({ policy: false, recovery: true }), note: READ_ONLY_PLAN, bad: false, on: true, locked: false },
    { id: "another node's that may write", view: () => mergeView({ policy: false, recovery: "writes" }), note: null, bad: false, on: false, locked: false },
  ])("an on_failure's read only is its plan's: $id", ({ view, note, bad, on, locked }) => {
    mount("merge", { tab: "on_failure", view: view() });
    const ro = screen.getByRole("switch", { name: "Read only" });
    expect(ro).toHaveAttribute("aria-checked", String(on));
    if (locked) expect(ro).toBeDisabled();
    else expect(ro).toBeEnabled();
    for (const t of [READ_ONLY_RECOVERY, WRITING_RECOVERY, READ_ONLY_PLAN].filter((t) => t !== note)) expect(screen.queryByText(t)).toBeNull();
    if (note) expect(screen.getByText(note)).toHaveClass(...(bad ? ["bp-note", "is-bad"] : ["bp-note"]));
  });

  it("sets an on_failure's read only on the plan", async () => {
    const { draft } = mount("merge", { tab: "on_failure", view: mergeView({ recovery: "writes" }) });
    await userEvent.click(screen.getByRole("switch", { name: "Read only" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "set_field", path: "merge.on_failure", field: "read_only", value: true }]);
  });

  it("a step added to a merge node's read-only recovery needs no read_only of its own: the plan's covers it", async () => {
    const { draft } = mount("merge", { tab: "on_failure", view: mergeView({ recovery: true }) });
    const g = screen.getByRole("group", { name: "On failure" });
    await userEvent.click(within(g).getAllByRole("button", { name: "Add a step here" }).at(-1)!);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Blank subprocess task" }));
    expect(draft.ops).toHaveBeenCalledWith([
      { op: "add_step", container: "merge.on_failure", at: 1, id: "step_2" },
      { op: "add_task", container: "merge.on_failure", step: "step_2", id: "subprocess", kind: "subprocess" },
    ]);
  });

  it("removes the fix loop with remove_handler and goes back to the node", async () => {
    const { draft, onLeave } = mount("verification", { tab: "fix_loop" });
    expect(screen.getByRole("button", { name: /judge/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove fix loop" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "remove_handler", path: "verification", kind: "fix_loop" }]);
    await vi.waitFor(() => expect(onLeave).toHaveBeenCalled());
  });

  it("resizes from 120 to the canvas height minus 90, by keys too, and remembers it", async () => {
    expect(clampBottom(50, 600)).toBe(120);
    expect(clampBottom(900, 600)).toBe(510);
    mount("verification");
    const sep = screen.getByRole("separator", { name: "Resize the bottom pane" });
    sep.focus();
    await userEvent.keyboard("{End}");
    expect(sep).toHaveAttribute("aria-valuenow", "510");
    expect(localStorage.getItem(BOTTOM.KEY)).toBe("510");
    await userEvent.keyboard("{Home}{ArrowUp}");
    expect(sep).toHaveAttribute("aria-valuenow", "136");
  });
});
