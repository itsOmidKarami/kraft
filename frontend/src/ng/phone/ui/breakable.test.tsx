import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { breakable } from "./breakable";

describe("breakable", () => {
  it.each([
    ["verification.fix_loop", ["verification.", "fix_", "loop"]],
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
});
