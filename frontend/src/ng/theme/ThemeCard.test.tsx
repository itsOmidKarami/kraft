import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ThemeCard } from "./ThemeCard";

describe("ThemeCard", () => {
  it("shows a gate by its name and the one word for sending it back", () => {
    render(<ThemeCard surface="graphite" accent="blue" amount="subtle" mode="light" />);
    expect(screen.getByText("Request changes")).toBeInTheDocument();
    expect(screen.getByText(/approve plan is waiting/)).toBeInTheDocument();
    expect(screen.queryByText(/plan_approval/)).toBeNull();
  });
});
