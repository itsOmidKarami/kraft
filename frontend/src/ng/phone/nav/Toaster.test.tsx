import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { showToast } from "../../ui/Toast";
import { Toaster, TOAST_MS } from "./Toaster";

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => vi.useRealTimers());

describe("the phone toaster (A.6)", () => {
  it("shows a toast raised by shared code, one at a time, and drops it after 3.2 s", () => {
    render(<Toaster />);
    act(() => showToast("Copied id"));
    act(() => showToast("Paused at plan."));
    expect(screen.queryByText("Copied id")).toBeNull();
    expect(screen.getByText("Paused at plan.")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(TOAST_MS + 10));
    expect(screen.queryByText("Paused at plan.")).toBeNull();
    expect(TOAST_MS).toBe(3200);
  });

  it("runs the action button once and removes the toast", async () => {
    const run = vi.fn();
    render(<Toaster />);
    act(() => showToast("Archived", { action: { label: "Undo", run } }));
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Archived")).toBeNull();
  });

  it("stays while its action holds focus", () => {
    render(<Toaster />);
    act(() => showToast("Archived", { action: { label: "Undo", run: () => {} } }));
    screen.getByRole("button", { name: "Undo" }).focus();
    act(() => void vi.advanceTimersByTime(TOAST_MS + 10));
    expect(screen.getByText("Archived")).toBeInTheDocument();
  });
});
