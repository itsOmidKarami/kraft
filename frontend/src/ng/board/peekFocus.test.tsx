import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePeekFocus } from "./peekFocus";

/** jsdom has no layout: the peek starts at x 600, and a row's action at x 700 sits under it. */
const LEFT: Record<string, number> = { "board-act-btn": 700, pane: 600 };
beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const left = Object.entries(LEFT).find(([c]) => this.classList.contains(c))?.[1] ?? 10;
    return { left, right: left + 80, top: 0, bottom: 20, width: 80, height: 20, x: left, y: 0, toJSON: () => ({}) } as DOMRect;
  });
  vi.spyOn(Element.prototype, "getClientRects").mockImplementation(() => [{}] as unknown as DOMRectList);
});
afterEach(() => vi.restoreAllMocks());

function Board({ sel }: { sel: string }) {
  const body = useRef<HTMLDivElement>(null);
  usePeekFocus(body, sel, 400);
  return (
    <div ref={body}>
      <div className="board-list">
        {["a", "b", "c"].map((id) => (
          <div key={id} data-row={id}>
            <button type="button" className="board-row-main">row {id}</button>
            <button type="button" className="board-act-btn">act {id}</button>
          </div>
        ))}
      </div>
      {sel && <aside className="pane"><button type="button">peek first</button><button type="button">peek last</button></aside>}
    </div>
  );
}

describe("usePeekFocus", () => {
  it("takes what the peek covers out of the keyboard's reach, and gives it back on close", () => {
    const { rerender } = render(<Board sel="b" />);
    expect(screen.getByText("act a")).toHaveAttribute("inert");
    expect(screen.getByText("row a")).not.toHaveAttribute("inert");
    rerender(<Board sel="" />);
    expect(screen.getByText("act a")).not.toHaveAttribute("inert");
  });

  it("puts the peek in the tab order right after the selected row, both ways", async () => {
    render(<Board sel="b" />);
    screen.getByText("row b").focus();
    await userEvent.tab();
    expect(screen.getByText("peek first")).toHaveFocus();
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByText("row c")).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByText("peek last")).toHaveFocus();
    await userEvent.tab({ shift: true });
    await userEvent.tab({ shift: true });
    expect(screen.getByText("row b")).toHaveFocus();
  });
});
