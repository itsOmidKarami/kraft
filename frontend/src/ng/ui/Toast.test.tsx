import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { showToast, Toaster } from "./Toast";

afterEach(() => vi.useRealTimers());

describe("Toast", () => {
  it("goes after its time; a number still sets the time", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => showToast("Copied id", 1000));
    expect(screen.getByText("Copied id")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1001));
    expect(screen.queryByText("Copied id")).toBeNull();
  });

  it("carries an action that runs once and takes the toast with it", () => {
    render(<Toaster />);
    const run = vi.fn();
    act(() => showToast("2 items archived", { action: { label: "Undo", run } }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("2 items archived")).toBeNull();
  });

  it("stays while its action has focus, and goes once focus leaves", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => showToast("Cancelling 2 items…", { ms: 1000, action: { label: "Undo", run: () => {} } }));
    act(() => screen.getByRole("button", { name: "Undo" }).focus());
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText("Cancelling 2 items…")).toBeInTheDocument();
    act(() => screen.getByRole("button", { name: "Undo" }).blur());
    act(() => vi.advanceTimersByTime(600));
    expect(screen.queryByText("Cancelling 2 items…")).toBeNull();
  });

  it("never pushes an unread error out for normal toasts", () => {
    render(<Toaster />);
    act(() => showToast("Failed to save", { tone: "error" }));
    for (const m of ["b1", "b2", "b3", "b4"]) act(() => showToast(m));
    expect(screen.getByText("Failed to save")).toBeInTheDocument();
    expect(screen.queryByText("b1")).toBeNull();
    expect(screen.queryByText("b2")).toBeNull();
    expect(screen.getByText("b4")).toBeInTheDocument();
  });

  it("stacks three at most and says an error as an alert that stays until dismissed", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    for (const m of ["a1", "a2", "a3", "a4"]) act(() => showToast(m));
    expect(screen.queryByText("a1")).toBeNull();
    expect(within(screen.getByRole("status")).getAllByText(/a\d/)).toHaveLength(3);
    act(() => showToast("Failed to save", { tone: "error" }));
    act(() => vi.advanceTimersByTime(60_000));
    expect(within(screen.getByRole("alert")).getByText("Failed to save")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Failed to save")).toBeNull();
  });
});
