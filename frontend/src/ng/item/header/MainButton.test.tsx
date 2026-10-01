import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Main, PanelItem } from "../status";
import { MainButton } from "./MainButton";

function Harness({ main = "pause", panel = ["escalate", "complete", "archive", "cancel"], archivable = false, onItem = vi.fn() }: { main?: Main; panel?: PanelItem[]; archivable?: boolean; onItem?: (i: PanelItem) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  return <><button>before</button><MainButton main={main} panel={panel} archivable={archivable} onMain={() => {}} onItem={onItem} groupRef={ref} /></>;
}

describe("MainButton", () => {
  it("opens the panel when the toggle takes keyboard focus, and Escape returns focus to the toggle with the panel shut", async () => {
    render(<Harness />);
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    const toggle = screen.getByRole("button", { name: "More actions" });
    expect(toggle).toHaveFocus();
    expect(screen.getByRole("menu", { name: "Item actions" })).toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /Escalate/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    // Archive is skipped while the item is still live.
    expect(screen.getByRole("menuitem", { name: /Cancel/ })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(toggle).toHaveFocus();
  });

  it("disables Archive until the item is done or cancelled, and calls back with the item picked", async () => {
    const onItem = vi.fn();
    const { unmount } = render(<Harness onItem={onItem} />);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("menuitem", { name: /Archive/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("menuitem", { name: /Mark complete/ }));
    expect(onItem).toHaveBeenCalledWith("complete");
    unmount();
    render(<Harness main="archive" panel={["archive"]} archivable />);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("menuitem", { name: /Archive/ })).toBeEnabled();
  });

  it("has no toggle when the panel is off (done, archived)", () => {
    render(<Harness main="restore" panel={[]} />);
    expect(screen.getByRole("button", { name: /Restore/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });
});
