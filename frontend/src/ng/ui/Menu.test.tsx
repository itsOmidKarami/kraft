import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Menu } from "./Menu";

const setup = (onPick = vi.fn()) => {
  render(
    <Menu
      label="More"
      trigger="⋯"
      items={[
        { label: "Rename", onSelect: () => onPick("Rename") },
        { label: "Archive", onSelect: () => onPick("Archive"), disabled: true },
        { label: "Duplicate", onSelect: () => onPick("Duplicate") },
        { label: "Remove", onSelect: () => onPick("Remove"), danger: true },
      ]}
    />,
  );
  const trigger = screen.getByRole("button", { name: "More" });
  fireEvent.click(trigger);
  return { trigger, onPick };
};
const focused = () => document.activeElement?.textContent;

describe("ng Menu", () => {
  it("opens with focus on the first item; ↑/↓, Home and End rove, skipping disabled ones", () => {
    const { trigger } = setup();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(focused()).toBe("Rename");
    const key = (k: string) => fireEvent.keyDown(document.activeElement!, { key: k });
    key("ArrowDown");
    expect(focused()).toBe("Duplicate");
    key("End");
    expect(focused()).toBe("Remove");
    key("ArrowDown");
    expect(focused()).toBe("Rename");
    key("ArrowUp");
    expect(focused()).toBe("Remove");
    key("Home");
    expect(focused()).toBe("Rename");
  });

  it("Escape closes and gives focus back to the trigger", () => {
    const { trigger } = setup();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("picking an item runs it, closes, and gives focus back", () => {
    const { trigger, onPick } = setup();
    fireEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(onPick).toHaveBeenCalledWith("Duplicate");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("draws a pick list: a text trigger named by its text, ✓ and a hint per item, read as radios, and a note", () => {
    render(
      <Menu label="Group by" triggerClass="board-menu-btn" trigger="Group Status" note="Needs you always comes first."
        items={[{ label: "Status", checked: true, onSelect: () => {} }, { label: "Repo", checked: false, hint: "4", onSelect: () => {} }]} />,
    );
    const trigger = screen.getByRole("button", { name: "Group Status" });
    expect(trigger).toHaveClass("board-menu-btn");
    fireEvent.click(trigger);
    expect(screen.getByRole("menu", { name: "Group by" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /Status/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: /Repo/ })).toHaveTextContent("Repo4");
    expect(screen.getByText("Needs you always comes first.")).toBeInTheDocument();
  });
});
