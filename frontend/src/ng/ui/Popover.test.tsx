import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { Popover } from "./Popover";

function Harness({ onClose }: { onClose: () => void }) {
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchor}>anchor</button>
      <p>outside</p>
      <Popover anchor={anchor} open onClose={onClose}>
        <button>inside</button>
      </Popover>
    </>
  );
}

describe("ng Popover", () => {
  it("closes on Escape and on a press outside it and its anchor, not on one inside", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.mouseDown(screen.getByText("inside"));
    fireEvent.mouseDown(screen.getByText("anchor"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("renders nothing while closed", () => {
    const anchor = { current: null };
    render(<Popover anchor={anchor} open={false} onClose={() => {}}>x</Popover>);
    expect(screen.queryByText("x")).toBeNull();
  });

  it("opens under its anchor, or above it when there is no room below", () => {
    const place = (anchorTop: number) => {
      document.body.innerHTML = "";
      const anchor = document.createElement("button");
      document.body.appendChild(anchor);
      vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({ top: anchorTop, bottom: anchorTop + 20, left: 10, right: 60, width: 50, height: 20, x: 10, y: anchorTop, toJSON: () => ({}) });
      vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(200);
      vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(100);
      const { unmount } = render(<Popover anchor={{ current: anchor }} open onClose={() => {}}><p>card</p></Popover>);
      const top = (screen.getByText("card").parentElement as HTMLElement).style.top;
      unmount();
      vi.restoreAllMocks();
      return top;
    };
    Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
    expect(place(100)).toBe("124px");
    expect(place(760)).toBe("556px");
  });
});
