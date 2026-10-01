import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders GFM tables, links as plain anchors in a new tab, and fenced code as pre > code", () => {
    const { container } = render(<Markdown text={"| a | b |\n|---|---|\n| 1 | 2 |\n\n[docs](https://example.com)\n\n```py\nx = 1\n```"} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "docs" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(container.querySelector("pre > code")).toHaveTextContent("x = 1");
  });
});
