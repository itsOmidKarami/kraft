import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Main, PanelItem } from "../status";
import { MainButton } from "./MainButton";

function Harness({ main = "pause", panel = ["escalate", "complete", "archive", "cancel"], archivable = false, onItem = vi.fn(), onMain = vi.fn() }: { main?: Main; panel?: PanelItem[]; archivable?: boolean; onItem?: (i: PanelItem) => void; onMain?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  return <><button>before</button><MainButton main={main} panel={panel} archivable={archivable} onMain={onMain} onItem={onItem} groupRef={ref} /></>;
}
const menu = () => screen.getByRole("menu", { name: "Item actions" });

describe("MainButton", () => {
  it("runs the main action from its label, by a click, Enter or Space, without opening the menu", async () => {
    const onMain = vi.fn();
    render(<Harness onMain={onMain} />);
    const label = screen.getByRole("button", { name: "Pause" });
    label.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(onMain).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    label.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(onMain).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it.each(["{ArrowDown}", "{Enter}"])("opens the menu on the main action from ▾ with %s, and Escape hands focus back to ▾", async (key) => {
    render(<Harness />);
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    const toggle = screen.getByRole("button", { name: "More actions" });
    expect(toggle).toHaveFocus();
    // Focus alone opens nothing.
    expect(screen.queryByRole("menu")).toBeNull();
    await userEvent.keyboard(key);
    expect(within(menu()).getAllByRole("menuitem").map((m) => m.textContent?.trim())).toEqual(["Pause", "Escalate…", "Mark complete…", "Archive", "Cancel…"]);
    await vi.waitFor(() => expect(within(menu()).getByRole("menuitem", { name: "Pause" })).toHaveFocus());
    await userEvent.keyboard("{ArrowDown}");
    expect(within(menu()).getByRole("menuitem", { name: /Escalate/ })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(toggle).toHaveFocus();
  });

  it.each([
    ["hovering the button", async () => userEvent.hover(screen.getByRole("button", { name: "Pause" }))],
    ["a click on ▾", async () => void screen.getByRole("button", { name: "More actions" }).dispatchEvent(new MouseEvent("click", { bubbles: true }))],
  ])("opens the menu on %s", async (_, open) => {
    render(<Harness />);
    await open();
    expect(await screen.findByRole("menu", { name: "Item actions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More actions" })).toHaveAttribute("aria-expanded", "true");
  });

  it.each([
    ["leaves for the page", false],
    ["leaves for a toast over the button, and then the toast", true],
  ])("closes the hover menu 160ms after the pointer %s", async (_, viaToast) => {
    vi.useFakeTimers();
    render(<><Harness /><div className="toasts"><div className="toast">Copied</div></div></>);
    const group = document.querySelector(".item-main")!;
    const toasts = document.querySelector(".toasts")!;
    fireEvent.mouseEnter(group);
    expect(screen.getByRole("menu", { name: "Item actions" })).toBeInTheDocument();
    fireEvent.mouseLeave(group, { relatedTarget: viaToast ? toasts.firstElementChild : document.body });
    if (viaToast) {
      act(() => void vi.advanceTimersByTime(1000));
      expect(screen.getByRole("menu", { name: "Item actions" })).toBeInTheDocument();
      fireEvent.mouseLeave(toasts, { relatedTarget: document.body });
    }
    act(() => void vi.advanceTimersByTime(150));
    expect(screen.getByRole("menu", { name: "Item actions" })).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(20));
    expect(screen.queryByRole("menu")).toBeNull();
    vi.useRealTimers();
  });

  it("keeps the hover menu open when the pointer comes back from a toast to the button", () => {
    vi.useFakeTimers();
    render(<><Harness /><div className="toasts"><div className="toast">Copied</div></div></>);
    const group = document.querySelector(".item-main")!;
    const toasts = document.querySelector(".toasts")!;
    fireEvent.mouseEnter(group);
    fireEvent.mouseLeave(group, { relatedTarget: toasts.firstElementChild });
    fireEvent.mouseLeave(toasts, { relatedTarget: group });
    fireEvent.mouseEnter(group);
    act(() => void vi.advanceTimersByTime(1000));
    expect(screen.getByRole("menu", { name: "Item actions" })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("runs the main action from the menu's first row, but not from its ▴ over ▾; a panel item from its row; Archive disabled until done or cancelled", async () => {
    const onItem = vi.fn();
    const onMain = vi.fn();
    const { unmount } = render(<Harness onItem={onItem} onMain={onMain} />);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(within(menu()).getByRole("menuitem", { name: /Archive/ })).toBeDisabled();
    await userEvent.click(menu().querySelector(".item-panel-caret")!);
    expect(onMain).not.toHaveBeenCalled();
    expect(menu()).toBeInTheDocument();
    await userEvent.click(within(menu()).getByRole("menuitem", { name: "Pause" }));
    expect(onMain).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.click(within(menu()).getByRole("menuitem", { name: /Mark complete/ }));
    expect(onItem).toHaveBeenCalledWith("complete");
    unmount();
    render(<Harness main="archive" panel={["archive", "cancel"]} archivable />);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(within(menu()).getAllByRole("menuitem").map((m) => m.textContent?.trim())).toEqual(["Archive", "Cancel…"]);
  });

  it("has no ▾ and no menu when the panel is off (done, archived)", async () => {
    const onMain = vi.fn();
    render(<Harness main="restore" panel={[]} onMain={onMain} />);
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Restore/ }));
    expect(onMain).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
