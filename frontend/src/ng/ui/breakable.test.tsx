import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { breakable, breakPaths } from "./breakable";

describe("breakable", () => {
  it.each([
    ["verification.fix_loop", ["verification.", "fix_loop"]],
    ["work_item_needs_human", ["work_", "item_", "needs_", "human"]],
    ["merge_request", ["merge_", "request"]],
    ["plain", ["plain"]],
  ])("offers a break only after a separator in %s", (text, parts) => {
    const { container } = render(<span>{breakable(text)}</span>);
    expect(container.textContent).toBe(text);
    expect(container.querySelectorAll("wbr")).toHaveLength(parts.length - 1);
    const seen = [...container.firstElementChild!.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent);
    expect(seen).toEqual(parts);
  });

  it.each([
    // Never after a path's leading slash: a line would end on a lone "/".
    ["moved to main in /Volumes/Work/x", ["moved to main in /Volumes/", "Work/", "x"]],
    ["refused by harness x (/Users/a/b)", ["refused by harness x (/Users/", "a/", "b)"]],
    ["outside the sandbox in `/Users/a/b`", ["outside the sandbox in `/Users/", "a/", "b`"]],
    ["Spend cap reached · $5.00 of $5.00", ["Spend cap reached · $5.00 of $5.00"]],
    ["would overwrite local.cfg", ["would overwrite local.cfg"]],
    ["review › code_review", ["review › code_review"]],
  ])("breakPaths offers a break only after a slash in %s", (text, parts) => {
    const { container } = render(<span>{breakPaths(text)}</span>);
    expect(container.textContent).toBe(text);
    const seen = [...container.firstElementChild!.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent);
    expect(seen).toEqual(parts);
  });
});
