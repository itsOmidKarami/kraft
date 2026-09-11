import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { SkipControl } from "./SkipControl";

vi.mock("../api");

describe("SkipControl", () => {
  it("shows a Skip… button that reveals an optional note field", () => {
    render(<SkipControl itemId="w1" />);
    expect(screen.queryByPlaceholderText(/why skip/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    expect(screen.getByPlaceholderText(/why skip/i)).toBeInTheDocument();
  });

  it("calls skipWorkItem with a trimmed note, or none, and closes on success", async () => {
    vi.mocked(api.skipWorkItem).mockResolvedValue(undefined);
    render(<SkipControl itemId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    fireEvent.change(screen.getByPlaceholderText(/why skip/i), {
      target: { value: "  known flake  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /skip step/i }));
    await waitFor(() => expect(api.skipWorkItem).toHaveBeenCalledWith("w1", "known flake"));
    await waitFor(() =>
      expect(screen.queryByPlaceholderText(/why skip/i)).not.toBeInTheDocument(),
    );
  });

  it("surfaces the API error and leaves the form open", async () => {
    vi.mocked(api.skipWorkItem).mockRejectedValue(new Error("work item is completed"));
    render(<SkipControl itemId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    fireEvent.click(screen.getByRole("button", { name: /skip step/i }));
    expect(await screen.findByText("work item is completed")).toBeInTheDocument();
  });
});
