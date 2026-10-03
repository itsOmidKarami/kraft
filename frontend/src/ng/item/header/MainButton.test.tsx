import { render, screen, within } from "@testing-library/react";
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
  it("is one button with ▾ and no separate toggle; Enter opens its menu on the main action, and Escape hands focus back with the menu shut", async () => {
    render(<Harness />);
    await userEvent.tab();
    await userEvent.tab();
    const main = screen.getByRole("button", { name: "Pause" });
    expect(main).toHaveFocus();
    expect(main).toHaveAttribute("aria-haspopup", "menu");
    expect(screen.getAllByRole("button")).toHaveLength(2);
    // Focus alone opens nothing: the menu would cover the button under the reader.
    expect(screen.queryByRole("menu")).toBeNull();
    await userEvent.keyboard("{Enter}");
    expect(within(menu()).getAllByRole("menuitem").map((m) => m.textContent?.trim())).toEqual(["Pause", "Escalate…", "Mark complete…", "Archive", "Cancel…"]);
    await vi.waitFor(() => expect(within(menu()).getByRole("menuitem", { name: "Pause" })).toHaveFocus());
    await userEvent.keyboard("{ArrowDown}");
    expect(within(menu()).getByRole("menuitem", { name: /Escalate/ })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(main).toHaveFocus();
  });

  it.each([
    ["hover", async (b: HTMLElement) => userEvent.hover(b)],
    ["a click", async (b: HTMLElement) => { b.dispatchEvent(new MouseEvent("click", { bubbles: true })); }],
  ])("opens its menu on %s, laid over the button", async (_, open) => {
    render(<Harness />);
    const main = screen.getByRole("button", { name: "Pause" });
    await open(main);
    expect(await screen.findByRole("menu", { name: "Item actions" })).toBeInTheDocument();
    expect(main).toHaveAttribute("aria-expanded", "true");
  });

  it("runs the main action from the menu's first row, a panel item from its row, and disables Archive until the item is done or cancelled", async () => {
    const onItem = vi.fn();
    const onMain = vi.fn();
    const { unmount } = render(<Harness onItem={onItem} onMain={onMain} />);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(within(menu()).getByRole("menuitem", { name: /Archive/ })).toBeDisabled();
    await userEvent.click(within(menu()).getByRole("menuitem", { name: "Pause" }));
    expect(onMain).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    await userEvent.click(within(menu()).getByRole("menuitem", { name: /Mark complete/ }));
    expect(onItem).toHaveBeenCalledWith("complete");
    unmount();
    render(<Harness main="archive" panel={["archive", "cancel"]} archivable />);
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));
    expect(within(menu()).getAllByRole("menuitem").map((m) => m.textContent?.trim())).toEqual(["Archive", "Cancel…"]);
  });

  it("is the action itself, with no menu, when the panel is off (done, archived)", async () => {
    const onMain = vi.fn();
    render(<Harness main="restore" panel={[]} onMain={onMain} />);
    const main = screen.getByRole("button", { name: /Restore/ });
    expect(main).not.toHaveAttribute("aria-haspopup");
    await userEvent.click(main);
    expect(onMain).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
