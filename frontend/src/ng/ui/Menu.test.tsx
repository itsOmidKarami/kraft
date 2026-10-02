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

  it("moves focus in only once the list can take it: a browser refuses focus to a visibility:hidden element", () => {
    // jsdom focuses a hidden element anyway, so read what a browser would see at the moment focus lands.
    const seen: string[] = [];
    const onFocus = (e: FocusEvent) => seen.push(getComputedStyle(e.target as Element).visibility);
    document.addEventListener("focusin", onFocus);
    try {
      setup();
    } finally {
      document.removeEventListener("focusin", onFocus);
    }
    expect(focused()).toBe("Rename");
    expect(seen).toEqual(["visible"]);
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

  it("draws a one-of list (W8): radios with the chosen one ticked, a sub-line, a heading", () => {
    render(
      <Menu
        label="Compare from"
        trigger="Compare from base ▾"
        triggerClass="word-btn"
        heading="Compare from"
        items={[
          { label: "base", sub: "the item's starting point", checked: true, onSelect: () => {} },
          { label: "your last review", sub: "No review submitted yet", checked: false, disabled: true, onSelect: () => {} },
        ]}
      />,
    );
    const trigger = screen.getByRole("button", { name: "Compare from base ▾" });
    expect(trigger).toHaveClass("word-btn");
    fireEvent.click(trigger);
    const [base, last] = screen.getAllByRole("menuitemradio");
    expect(base).toHaveAttribute("aria-checked", "true");
    expect(base).toHaveTextContent("the item's starting point");
    expect(last).toHaveAttribute("aria-checked", "false");
    expect(last).toBeDisabled();
    expect(screen.getByText("Compare from")).toBeInTheDocument();
  });

  it("marks an item with a draft or problem dot and a glyph, and names the dot for a screen reader", () => {
    render(
      <Menu
        label="Policy section"
        trigger="Limits ▾"
        triggerClass="word-btn"
        items={[
          { label: "Limits", icon: <svg data-testid="i" />, checked: true, dot: "problem", onSelect: () => {} },
          { label: "Loops", dot: "draft", dotLabel: "unpublished changes", checked: false, onSelect: () => {} },
          { label: "Housekeeping", checked: false, onSelect: () => {} },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Limits ▾" }));
    const [limits, loops, house] = screen.getAllByRole("menuitemradio");
    expect(limits.querySelector(".menu-dot.is-problem")).toHaveAttribute("aria-label", "has a problem");
    expect(limits.querySelector(".menu-icon [data-testid='i']")).not.toBeNull();
    expect(loops.querySelector(".menu-dot.is-draft")).toHaveAttribute("aria-label", "unpublished changes");
    expect(house.querySelector(".menu-dot")).toBeNull();
  });
});
