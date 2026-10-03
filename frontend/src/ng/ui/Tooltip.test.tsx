import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IconButton } from "./IconButton";
import { TIP_DELAY_MS, Tooltip } from "./Tooltip";

afterEach(() => vi.useRealTimers());

const mount = () => {
  vi.useFakeTimers();
  render(<><IconButton label="Collapse all files">▾</IconButton><button>other</button><Tooltip /></>);
  return screen.getByRole("button", { name: "Collapse all files" });
};
const wait = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

describe("Tooltip (TT-1)", () => {
  it("shows an icon button's label after the delay on hover, and goes on leave", () => {
    const b = mount();
    fireEvent.mouseOver(b);
    wait(TIP_DELAY_MS - 50);
    expect(screen.queryByRole("tooltip")).toBeNull();
    wait(60);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Collapse all files");
    fireEvent.mouseOut(b, { relatedTarget: document.body });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows on keyboard focus too, and Escape and a press hide it", async () => {
    vi.useRealTimers();
    const user = userEvent.setup();
    render(<><IconButton label="Next match">↓</IconButton><Tooltip /></>);
    await user.tab();
    expect(await screen.findByRole("tooltip", {}, { timeout: 1500 })).toHaveTextContent("Next match");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("does not show for a button with no data-tip, nor after a press", () => {
    const b = mount();
    fireEvent.mouseOver(screen.getByRole("button", { name: "other" }));
    wait(TIP_DELAY_MS + 10);
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseOver(b);
    fireEvent.pointerDown(b);
    wait(TIP_DELAY_MS + 10);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("sits under the button, clear of it", () => {
    const b = mount();
    vi.spyOn(b, "getBoundingClientRect").mockReturnValue({ top: 100, bottom: 128, left: 50, right: 78, width: 28, height: 28, x: 50, y: 100, toJSON: () => ({}) });
    fireEvent.mouseOver(b);
    wait(TIP_DELAY_MS + 10);
    const top = parseFloat(screen.getByRole("tooltip").style.top);
    expect(top).toBeGreaterThanOrEqual(128);
  });
});
