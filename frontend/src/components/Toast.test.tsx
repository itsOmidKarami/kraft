import { render, screen } from "@testing-library/react";
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

  it("stacks more than one toast", () => {
    render(<ToastHost />);
    act(() => {
      showToast("first");
      showToast("second");
    });
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
  });
});
