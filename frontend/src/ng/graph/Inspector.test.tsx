import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
