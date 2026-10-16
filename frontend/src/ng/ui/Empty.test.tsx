import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Empty } from "./Empty";

describe("ng Empty", () => {
  it.each([
    ["inline", "P"],
    ["block", "DIV"],
  ] as const)("%s renders its text in a %s with its variant class", (variant, tag) => {
    render(<Empty variant={variant} role="status">Nothing here.</Empty>);
    const el = screen.getByRole("status");
    expect(el.tagName).toBe(tag);
    expect(el).toHaveClass("empty", `empty-${variant}`);
    expect(el).toHaveTextContent("Nothing here.");
  });

  it("block puts its action after the text", () => {
    render(<Empty variant="block" action={<button type="button">Retry</button>}>Failed.</Empty>);
    expect(screen.getByRole("button", { name: "Retry" }).previousElementSibling).toHaveTextContent("Failed.");
  });
});
