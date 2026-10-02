import { fireEvent, render, screen } from "@testing-library/react";
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

  it("offers the row's one action and hands it over, none for a running or done row", () => {
    const { onAction, item } = mount("failed", { stop: { kind: "failed", node: "merge_request", reason: null, resume_at: null } as WorkItem["stop"] });
    fireEvent.click(screen.getByRole("button", { name: "Retry…" }));
    expect(onAction).toHaveBeenCalledWith(item, expect.objectContaining({ kind: "peek" }));
  });

  it.each(["running", "done"] as const)("has no action when %s", (s) => {
    mount(s);
    expect(screen.getAllByRole("button")).toHaveLength(1);
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
