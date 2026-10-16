import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { showToast } from "../../ui/Toast";
import { TOAST_MS } from "../../ui/toastModel";
import { Toaster } from "./Toaster";

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => vi.useRealTimers());

describe("the phone toaster (A.6)", () => {
  it("stacks toasts raised by shared code as the desktop does, and drops them after TOAST_MS", () => {
    render(<Toaster />);
    for (const m of ["one", "two", "three", "four"]) act(() => showToast(m));
    expect(screen.queryByText("one")).toBeNull();
    expect(screen.getAllByText(/two|three|four/)).toHaveLength(3);
    act(() => void vi.advanceTimersByTime(TOAST_MS + 10));
    expect(screen.queryByText("four")).toBeNull();
  });

  it("keeps an error until it is dismissed, announced as an alert", async () => {
    render(<Toaster />);
    act(() => showToast("Could not pause", { tone: "error" }));
    act(() => void vi.advanceTimersByTime(TOAST_MS * 5));
    expect(within(screen.getByRole("alert")).getByText("Could not pause")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Could not pause")).toBeNull();
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
