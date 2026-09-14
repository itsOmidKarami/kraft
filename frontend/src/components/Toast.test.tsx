import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import { ToastHost, showToast } from "./Toast";

describe("Toast", () => {
  it("renders a toast fired via showToast and clears it after 2.6s", () => {
    vi.useFakeTimers();
    render(<ToastHost />);
    act(() => showToast("Approved — chain continues"));
    expect(screen.getByText("Approved — chain continues")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(2600);
    });
    expect(screen.queryByText("Approved — chain continues")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows at most three at once, newest kept (W6.1)", () => {
    render(<ToastHost />);
    act(() => ["one", "two", "three", "four"].forEach((m) => showToast(m)));
    expect(screen.queryByText("one")).not.toBeInTheDocument();
    for (const m of ["two", "three", "four"]) expect(screen.getByText(m)).toBeInTheDocument();
  });

  it("stacks more than one toast", () => {
    render(<ToastHost />);
    act(() => {
      showToast("first");
      showToast("second");
    });
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
  });

  it("holds a toast for its own duration and renders its action, which runs and dismisses (W4.9)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const run = vi.fn();
    render(<ToastHost />);
    act(() => showToast("2 items archived", undefined, { ms: 6000, action: { label: "Undo", run } }));
    act(() => {
      vi.advanceTimersByTime(2600);
    });
    expect(screen.getByText(/2 items archived/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(run).toHaveBeenCalledOnce();
    expect(screen.queryByText(/2 items archived/)).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
