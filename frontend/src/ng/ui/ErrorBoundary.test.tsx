import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Bad(): never {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  it("shows a message and a reload instead of blanking the app when a child throws, and clears on a new resetKey", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(<ErrorBoundary resetKey="/a"><Bad /></ErrorBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("This page could not be shown");
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    rerender(<ErrorBoundary resetKey="/b"><p>fine</p></ErrorBoundary>);
    expect(screen.getByText("fine")).toBeInTheDocument();
  });
});
