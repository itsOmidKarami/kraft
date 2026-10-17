import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DisplayStatus, WorkItem } from "../../types";
import { detail } from "../item/testkit";
import { Row, type RowProps } from "./Row";

const mount = (display_status: DisplayStatus, over: Partial<WorkItem> = {}, props: Partial<RowProps> = {}) => {
  const h = { onSelect: vi.fn(), onOpen: vi.fn(), onCheck: vi.fn(), onAction: vi.fn() };
  const item = detail({ display_status, title: "Design the cache", ...over });
  render(<Row item={item} selected={false} checked={false} offline={false} now={Date.parse("2026-09-13T10:00:00Z")} {...h} {...props} />);
  return { ...h, item, main: screen.getByRole("button", { name: /Design the cache/ }) };
};

describe("Row", () => {
  it("selects on click (Enter clicks a button), opens on ⌘-click, ⌘Enter and double-click", () => {
    const { main, onSelect, onOpen } = mount("running");
    fireEvent.click(main);
    expect(onSelect).toHaveBeenLastCalledWith("w1");
    fireEvent.click(main, { metaKey: true });
    fireEvent.keyDown(main, { key: "Enter", metaKey: true });
    fireEvent.doubleClick(main);
    expect(onOpen).toHaveBeenCalledTimes(3);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("toggles its checkbox from Space on the row and from the box itself", () => {
    const { main, onCheck } = mount("running");
    fireEvent.keyDown(main, { key: " " });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Design the cache" }));
    expect(onCheck).toHaveBeenCalledTimes(2);
  });

  it("leads the meta line with the reason, drops the repo and chain when asked, and says node progress in words", () => {
    const { main } = mount("failed", { stop: { kind: "failed", node: "build", reason: null, resume_at: null } as WorkItem["stop"] }, { hideRepo: true, hideChain: true });
    const meta = main.querySelector(".board-meta")!;
    expect(meta.firstElementChild).toHaveClass("board-meta-tail");
    expect(meta.querySelector(".board-meta-repo, .board-meta-chain")).toBeNull();
    expect(meta).toHaveTextContent(/\d+ of \d+ nodes done/);
  });

  it("says an in-place item runs in your checkout, and a worktree item nothing of the kind", () => {
    expect(mount("running", { in_place: true }).main.querySelector(".board-meta")).toHaveTextContent("in your checkout");
    cleanup();
    expect(mount("running", { in_place: false }).main.querySelector(".board-meta")).not.toHaveTextContent("in your checkout");
  });

  it("offers the row's one action and hands it over, none for a running or done row", () => {
    const { onAction, item } = mount("failed", { stop: { kind: "failed", node: "merge_request", reason: null, resume_at: null } as WorkItem["stop"] });
    fireEvent.click(screen.getByRole("button", { name: "Retry…" }));
    expect(onAction).toHaveBeenCalledWith(item, expect.objectContaining({ kind: "peek" }));
  });

  it.each<[string, DisplayStatus, Partial<WorkItem>, string | null]>([
    ["failed", "failed", {}, "is-failed"],
    ["paused", "paused", { current_node_id: "build" }, "is-paused"],
    ["gate", "needs_you", { stop: { kind: "gate", node: "spec_approval", reason: null, resume_at: null } as WorkItem["stop"] }, "is-gate"],
    ["question", "needs_you", { stop: { kind: "question", node: "build", reason: "which db?", resume_at: null } as WorkItem["stop"] }, "is-question"],
    ["running", "running", {}, null],
  ])("tells a %s stop by an icon, not only by colour", (_n, status, over, cls) => {
    const { main } = mount(status, over);
    const tail = main.querySelector(".board-meta-tail")!;
    if (cls) {
      expect(tail).toHaveClass(cls);
      expect(tail.querySelector("svg.board-cue")).not.toBeNull();
    } else expect(tail.querySelector("svg")).toBeNull();
  });

  it.each<[DisplayStatus, Partial<WorkItem>, boolean]>([
    ["paused", { current_node_id: "build" }, false],
    ["failed", {}, true],
    ["needs_you", { stop: { kind: "gate", node: "spec_approval", reason: null, resume_at: null } as WorkItem["stop"] }, true],
  ])("a %s row's tail is amber only when it needs a human", (status, over, amber) => {
    const { main } = mount(status, over);
    const tail = main.querySelector(".board-meta-tail")!;
    expect(tail.classList.contains("is-hot")).toBe(amber);
  });

  it.each(["running", "done"] as const)("has no action when %s", (s) => {
    mount(s);
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("describes its action by the item, so five \"Review\" buttons are told apart", () => {
    mount("needs_you", { stop: { kind: "gate", node: "spec_approval", reason: null, resume_at: null }, pending_gate: "spec_approval" });
    expect(screen.getByRole("button", { name: "Review" })).toHaveAccessibleDescription("Design the cache");
  });

  it("turns the action off while offline and shows an action's error on the row", () => {
    mount("paused", {}, { offline: true, error: "work item is active, not paused" });
    expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("work item is active, not paused");
  });

  it("names the item in its meta by bead id, else the short id, never shrinking it", () => {
    mount("running");
    expect(screen.getByText("kraft-cb59")).toHaveClass("board-meta-id");
  });

  it("keeps the id and the age as their own unshrinking parts of the meta line", () => {
    const { container } = render(<Row item={detail({ display_status: "running" })} selected={false} checked={false} offline={false} now={Date.parse("2026-09-13T10:00:00Z")} onSelect={() => {}} onOpen={() => {}} onCheck={() => {}} onAction={() => {}} />);
    expect(container.querySelector(".ticks")).not.toBeNull();
    expect(container.querySelector(".board-meta-age")).toHaveTextContent("1h ago");
    expect(container.querySelector(".board-meta-id")).toHaveTextContent("kraft-cb59");
  });
});
