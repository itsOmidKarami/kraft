import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { createPortal } from "react-dom";
import { describe, expect, it, vi } from "vitest";
import { Inspector } from "./Inspector";

const docked = { width: 380, overlay: false, handle: { role: "separator", "aria-label": "Resize pane", tabIndex: 0 } } as never;

function Pane({ size = docked, onFocus }: { size?: never; onFocus?: () => void }) {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState("overview");
  return (
    <Inspector id="p" open={open} size={size} title="verification" crumbs={[{ label: "default", onClick: vi.fn() }, { label: "kraft-cb59" }]} prob={{ msg: "Stopped here." }}
      tabs={[{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }]} tab={tab} onTab={setTab}
      onCollapse={() => setOpen(false)} onExpand={() => setOpen(true)} onFocus={onFocus}>
      <p>body</p>
    </Inspector>
  );
}

describe("Inspector", () => {
  it("links crumbs that have a target and leaves the rest as text", () => {
    render(<Pane />);
    expect(screen.getByRole("button", { name: "default" })).toBeInTheDocument();
    expect(screen.getByText("kraft-cb59").tagName).toBe("SPAN");
    expect(screen.queryByRole("button", { name: "kraft-cb59" })).toBeNull();
  });

  it("leaves Escape in a dialog portaled out of the pane to that dialog", async () => {
    const onCollapse = vi.fn();
    render(
      <Inspector id="p" open size={docked} title="verification" crumbs={[]} onCollapse={onCollapse} onExpand={() => {}}>
        <button type="button">in the pane</button>
        {createPortal(<textarea aria-label="Message" />, document.body)}
      </Inspector>,
    );
    screen.getByRole("textbox", { name: "Message" }).focus();
    await userEvent.keyboard("{Escape}");
    expect(onCollapse).not.toHaveBeenCalled();
    screen.getByRole("button", { name: "in the pane" }).focus();
    await userEvent.keyboard("{Escape}");
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });

  // R11b-02: Escape in a Reject… note or a Retry steer collapsed the pane and the text went with it.
  it.each([
    ["a note with text in it", false, <textarea aria-label="field" defaultValue="The spec misses eviction." />],
    ["a line with text in it", false, <input aria-label="field" defaultValue="0.03" />],
    ["an empty note", true, <textarea aria-label="field" />],
    ["a checkbox", true, <input type="checkbox" aria-label="field" />],
    // An Escape that ends an input method's composition is the field's (Safari sets isComposing).
    ["an empty note, composing", false, <textarea aria-label="field" />, true],
  ])("Escape in %s: the pane collapses %s", async (_name, collapses, field, composing = false) => {
    const onCollapse = vi.fn();
    render(<Inspector id="p" open size={docked} title="verification" crumbs={[]} onCollapse={onCollapse} onExpand={() => {}}>{field}</Inspector>);
    screen.getByLabelText("field").focus();
    if (composing) fireEvent.keyDown(screen.getByLabelText("field"), { key: "Escape", isComposing: true });
    else await userEvent.keyboard("{Escape}");
    expect(onCollapse).toHaveBeenCalledTimes(collapses ? 1 : 0);
    if (!collapses) expect(screen.getByLabelText("field")).toHaveFocus();
  });

  it("collapses to the rail and expands from it", async () => {
    const user = userEvent.setup();
    render(<Pane />);
    await user.click(screen.getByRole("button", { name: "Collapse pane" }));
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByLabelText("has a problem")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Expand verification" }));
    expect(screen.getByRole("tablist")).toBeInTheDocument();
  });

  it("puts focus on the rail when the collapse button is pressed by keyboard (ux2-W5)", async () => {
    const user = userEvent.setup();
    render(<Pane />);
    screen.getByRole("button", { name: "Collapse pane" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Expand pane" })).toHaveFocus();
  });

  it("collapses on Escape and puts focus on the rail", async () => {
    const user = userEvent.setup();
    render(<Pane />);
    await user.click(screen.getByRole("tab", { name: "Config" }));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Expand pane" })).toHaveFocus();
  });

  it("scrolls only the tab body, a panel of the active tab", () => {
    render(<Pane />);
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveClass("pane-body");
    expect(panel).toHaveAttribute("aria-labelledby", "p-tab-overview");
  });

  it("has a resize handle when docked and none when overlaid; Focus only when offered", async () => {
    const onFocus = vi.fn();
    const { unmount } = render(<Pane onFocus={onFocus} />);
    expect(screen.getByRole("separator", { name: "Resize pane" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Focus/ }));
    expect(onFocus).toHaveBeenCalled();
    unmount();
    render(<Pane size={{ width: 360, overlay: true, handle: undefined } as never} />);
    expect(screen.queryByRole("separator")).toBeNull();
    expect(screen.queryByRole("button", { name: /Focus/ })).toBeNull();
  });
});
