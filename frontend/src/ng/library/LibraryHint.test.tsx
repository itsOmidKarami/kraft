import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LibraryHint } from "./LibraryHint";

describe("LibraryHint", () => {
  it("links to the library component the chain's one extends", () => {
    render(<MemoryRouter><LibraryHint section="tasks" name="spec_author" /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "tasks.spec_author" })).toHaveAttribute("href", "/templates/library/tasks.spec_author");
  });

  it("names it without a link outside a router", () => {
    render(<LibraryHint section="nodes" name="verification" />);
    expect(screen.getByText("nodes.verification")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
