import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders GFM tables, links as plain anchors in a new tab, and fenced code as pre > code", () => {
    const { container } = render(<Markdown text={"| a | b |\n|---|---|\n| 1 | 2 |\n\n[docs](https://example.com)\n\n```py\nx = 1\n```"} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "docs" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(container.querySelector("pre > code")).toHaveTextContent("x = 1");
  });

  it("hands a fenced block's text and language to `code`, and leaves inline code alone", () => {
    const code = vi.fn((text: string, lang?: string) => <b data-lang={lang}>{text.toUpperCase()}</b>);
    const { container } = render(<Markdown text={"use `x` here\n\n```py\nx = 1\n```"} code={code} />);
    expect(code).toHaveBeenCalledWith("x = 1", "py");
    expect(container.querySelector("pre > code > b")).toHaveTextContent("X = 1");
    expect(container.querySelector("p > code")).toHaveTextContent("x");
  });
});
