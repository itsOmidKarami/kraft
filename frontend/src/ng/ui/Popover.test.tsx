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
});
