import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";
import { LIST, useListWidth } from "./listWidth";

function Probe({ room = 1200 }: { room?: number }) {
  const page = useRef<HTMLDivElement>(null);
  const { width, handle } = useListWidth(page);
  return (
    <div ref={page} data-room={room}>
      <div {...handle} data-testid="handle" style={{ width }} />
    </div>
  );
}
const handle = () => screen.getByTestId("handle");
const width = () => Number(handle().getAttribute("aria-valuenow"));

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("useListWidth", () => {
  it("starts at the default, and keys ← → by a step, Home and End to the ends", () => {
    render(<Probe />);
    expect(width()).toBe(LIST.DEFAULT);
    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    expect(width()).toBe(LIST.DEFAULT + LIST.STEP);
    fireEvent.keyDown(handle(), { key: "ArrowLeft" });
    fireEvent.keyDown(handle(), { key: "ArrowLeft" });
    expect(width()).toBe(LIST.DEFAULT - LIST.STEP);
    fireEvent.keyDown(handle(), { key: "Home" });
    expect(width()).toBe(LIST.MIN);
    fireEvent.keyDown(handle(), { key: "ArrowLeft" });
    expect(width()).toBe(LIST.MIN);
    fireEvent.keyDown(handle(), { key: "End" });
    expect(width()).toBe(LIST.MAX);
  });

  it("follows a drag by its distance, holds it to the limits, and remembers where it ended", () => {
    const { unmount } = render(<Probe />);
    fireEvent.pointerDown(handle(), { clientX: 250 });
    act(() => void window.dispatchEvent(new MouseEvent("pointermove", { clientX: 330 })));
    expect(width()).toBe(LIST.DEFAULT + 80);
    act(() => void window.dispatchEvent(new MouseEvent("pointermove", { clientX: 5000 })));
    expect(width()).toBe(LIST.MAX);
    act(() => void window.dispatchEvent(new MouseEvent("pointerup", { clientX: 300 })));
    expect(width()).toBe(LIST.DEFAULT + 50);
    unmount();
    render(<Probe />);
    expect(width()).toBe(LIST.DEFAULT + 50);
  });

  it("goes back to the default on a double click, and still works when storage throws", () => {
    render(<Probe />);
    fireEvent.keyDown(handle(), { key: "End" });
    fireEvent.doubleClick(handle());
    expect(width()).toBe(LIST.DEFAULT);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Probe />);
    fireEvent.keyDown(screen.getAllByTestId("handle")[1], { key: "ArrowRight" });
    expect(Number(screen.getAllByTestId("handle")[1].getAttribute("aria-valuenow"))).toBe(LIST.DEFAULT + LIST.STEP);
  });
});
