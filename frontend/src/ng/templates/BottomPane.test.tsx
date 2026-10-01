import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigDraft } from "./draft/useConfigDraft";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import { resolvedNode } from "./draft/view";
import { BOTTOM, BottomPane, clampBottom, handlerOf, tabsFor, type BottomTab } from "./BottomPane";

function mount(node: string, o: { sel?: string; tab?: BottomTab; open?: boolean } = {}) {
  const draft = {
    view: DEFAULT_VIEW,
    ops: vi.fn(() => Promise.resolve({ status: 200, body: { ops: [] } })),
    resolvedNode: (id: string) => resolvedNode(DEFAULT_VIEW.result, id),
  } as unknown as ConfigDraft;
  const cb = { onTab: vi.fn(), onToggle: vi.fn(), onPick: vi.fn(), onOpen: vi.fn(), onLeave: vi.fn() };
  render(<BottomPane node={node} draft={draft} selPath={o.sel ?? node} tab={o.tab ?? "on_failure"} open={o.open ?? true} canvasH={600} right={380} {...cb} />);
  return { draft, ...cb };
}
const tabs = () => within(screen.getByRole("tablist")).getAllByRole("tab").map((t) => t.textContent);

beforeEach(() => localStorage.clear());

describe("bottom pane", () => {
  it("shows only On failure for a main task, all three for the node, On conflict when on base change is set", () => {
    expect(tabsFor("verification.review.code_review", "verification", false)).toEqual(["on_failure"]);
    expect(tabsFor("verification", "verification", false)).toEqual(["on_failure", "fix_loop", "escalation"]);
    expect(tabsFor("verification.fix_loop", "verification", true)).toEqual(["on_failure", "fix_loop", "escalation", "on_conflict"]);
    mount("merge_request_feedback");
    expect(tabs()).toEqual(["On failure", "Fix loop", "Escalation", "On conflict"]);
  });

  it("names the handler a path sits in", () => {
    expect(handlerOf("verification.fix_loop.judge")).toBe("fix_loop");
    expect(handlerOf("merge_request_feedback.on_failure.repair.repair_feedback")).toBe("on_failure");
    expect(handlerOf("x.on_base_changed.on_conflict.main.t")).toBe("on_conflict");
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
    expect(screen.getByText(/Without one, Kraft auto-escalates after 0m/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add one" }));
    const menu = await screen.findByRole("dialog", { name: "Add an escalation task" });
    expect(within(menu).queryByRole("menuitem", { name: "Blank forge task" })).toBeNull();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Blank agent task" }));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "add_task", slot: "escalation", node: "verification", kind: "agent" }]);
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
