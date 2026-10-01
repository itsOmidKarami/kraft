import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NodeIcon } from "./icons";

const svg = (el: React.ReactElement) => render(el).container.querySelector("svg")!;

describe("NodeIcon", () => {
  it("draws a known icon by name", () => {
    expect(svg(<NodeIcon name="shield" kind="agent" />)).toHaveClass("lucide-shield");
  });
  it("falls back to the kind's icon for an unknown name (R32)", () => {
    expect(svg(<NodeIcon name="no-such-icon" kind="subprocess" />)).toHaveClass("lucide-terminal");
  });
  it("draws a box for an exec node with no icon", () => {
    expect(svg(<NodeIcon />)).toHaveClass("lucide-box");
  });
});
