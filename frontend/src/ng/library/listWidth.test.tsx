import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIST, useListWidth } from "./listWidth";

function Probe({ page = 0 }: { page?: number }) {
  const { width, handle } = useListWidth(page);
  return <div {...handle} data-testid="handle" style={{ width }} />;
}
const handle = (i = 0) => screen.getAllByTestId("handle")[i];
const width = (i = 0) => Number(handle(i).getAttribute("aria-valuenow"));
const move = (clientX: number) => act(() => void window.dispatchEvent(new MouseEvent("pointermove", { clientX })));
const release = (type: "pointerup" | "pointercancel") => act(() => void window.dispatchEvent(new MouseEvent(type, { clientX: 0 })));

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

  it("leaves a key with a modifier to the browser and to the canvas", () => {
    const outer = vi.fn();
    render(<div onKeyDown={outer}><Probe /></div>);
    fireEvent.keyDown(handle(), { key: "ArrowRight", altKey: true });
    expect(width()).toBe(LIST.DEFAULT);
    expect(outer).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    expect(outer).toHaveBeenCalledTimes(1);
  });

  it("holds the width to what the page leaves the canvas, and follows the page", () => {
    const { rerender } = render(<Probe page={LIST.CANVAS + 300} />);
    fireEvent.keyDown(handle(), { key: "End" });
    expect(width()).toBe(300);
    expect(handle()).toHaveAttribute("aria-valuemax", "300");
    fireEvent.pointerDown(handle(), { clientX: 300 });
    move(5000);
    expect(width()).toBe(300);
    release("pointerup");
    // A narrower page takes the width in at once; a page too small for the minimum still gives it.
    rerender(<Probe page={LIST.CANVAS + 220} />);
    expect(width()).toBe(220);
    rerender(<Probe page={100} />);
    expect(width()).toBe(LIST.MIN);
  });

  it("follows a drag by its distance, holds it to the limits, and remembers where it ended", () => {
    const { unmount } = render(<Probe />);
    fireEvent.pointerDown(handle(), { clientX: 250 });
    move(330);
    expect(width()).toBe(LIST.DEFAULT + 80);
    move(5000);
    expect(width()).toBe(LIST.MAX);
    move(300);
    release("pointerup");
    expect(width()).toBe(LIST.DEFAULT + 50);
    // Let go: the pointer no longer drags it.
    move(400);
    expect(width()).toBe(LIST.DEFAULT + 50);
    unmount();
    render(<Probe />);
    expect(width()).toBe(LIST.DEFAULT + 50);
  });

  it("stops following when the pointer is cancelled, keeps what was stored, and lets go on unmount", () => {
    const first = render(<Probe />);
    fireEvent.keyDown(handle(), { key: "End" });
    fireEvent.pointerDown(handle(), { clientX: 480 });
    move(400);
    expect(width()).toBe(400);
    release("pointercancel");
    move(300);
    expect(width()).toBe(400);
    first.unmount();
    // A click that never moved stores nothing, and a drag cut short by leaving the page leaves no listener.
    const again = render(<Probe />);
    expect(width()).toBe(LIST.MAX);
    fireEvent.pointerDown(handle(), { clientX: 480 });
    release("pointerup");
    expect(width()).toBe(LIST.MAX);
    fireEvent.pointerDown(handle(), { clientX: 480 });
    const spy = vi.spyOn(window, "removeEventListener");
    again.unmount();
    expect(spy.mock.calls.map(([type]) => type).sort()).toEqual(["pointercancel", "pointermove", "pointerup"]);
    render(<Probe />);
    expect(width()).toBe(LIST.MAX);
  });

  it("keeps a wide width stored on a roomier page through a click on a narrow one", () => {
    localStorage.setItem("kraft.ng.library.list", String(LIST.MAX));
    const narrow = render(<Probe page={LIST.CANVAS + 300} />);
    expect(width()).toBe(300);
    fireEvent.pointerDown(handle(), { clientX: 300 });
    release("pointerup");
    narrow.unmount();
    render(<Probe />);
    expect(width()).toBe(LIST.MAX);
  });

  it("goes back to the default on a double click, and still works when storage throws", () => {
    render(<Probe />);
    fireEvent.keyDown(handle(), { key: "End" });
    fireEvent.doubleClick(handle());
    expect(width()).toBe(LIST.DEFAULT);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Probe />);
    fireEvent.keyDown(handle(1), { key: "ArrowRight" });
    expect(width(1)).toBe(LIST.DEFAULT + LIST.STEP);
  });
});
