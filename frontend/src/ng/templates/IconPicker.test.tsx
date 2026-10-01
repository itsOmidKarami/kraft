import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Bug, Rocket, Shield } from "lucide-react";
import { describe, expect, it, vi } from "vitest";
import { IconPicker } from "./IconPicker";

vi.mock("../iconSetData", () => ({ ICONS: { bug: Bug, rocket: Rocket, shield: Shield } }));

function mount(current?: string) {
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  const cb = { onPick: vi.fn(), onClose: vi.fn() };
  render(<IconPicker anchor={{ current: anchor }} current={current} {...cb} />);
  return cb;
}

describe("icon picker", () => {
  it("searches the whole set, and Enter in the search picks the first match", async () => {
    const cb = mount("shield");
    const grid = await screen.findByRole("listbox", { name: "Icons" });
    expect(within(grid).getAllByRole("option").map((o) => o.getAttribute("aria-label"))).toEqual(["bug", "rocket", "shield"]);
    expect(within(grid).getByRole("option", { name: "shield" })).toHaveAttribute("aria-selected", "true");
    const q = screen.getByRole("textbox", { name: "Search icons" });
    await waitFor(() => expect(q).toHaveFocus());
    await userEvent.type(q, "ro{Enter}");
    expect(within(grid).getAllByRole("option")).toHaveLength(1);
    expect(cb.onPick).toHaveBeenCalledWith("rocket");
  });

  it("moves through the grid by arrows and picks with Enter; Default picks none", async () => {
    const cb = mount();
    await screen.findByRole("listbox", { name: "Icons" });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Search icons" })).toHaveFocus());
    await userEvent.keyboard("{ArrowDown}{ArrowRight}{Enter}");
    expect(cb.onPick).toHaveBeenLastCalledWith("rocket");
    await userEvent.click(screen.getByRole("button", { name: "Default" }));
    expect(cb.onPick).toHaveBeenLastCalledWith(null);
    await userEvent.keyboard("{Escape}");
    expect(cb.onClose).toHaveBeenCalled();
  });
});
