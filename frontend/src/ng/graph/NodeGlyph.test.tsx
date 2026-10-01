import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NodeGlyph } from "./NodeGlyph";

const pill = (c: HTMLElement) => c.querySelector(".glyph-att");

describe("NodeGlyph", () => {
  it("shows the attempt pill only from attempt 2, red when a cap stopped it", () => {
    expect(pill(render(<NodeGlyph size="lg" attempt={1} />).container)).toBeNull();
    const two = pill(render(<NodeGlyph size="lg" attempt={2} />).container)!;
    expect(two).toHaveTextContent("×2");
    expect(two).not.toHaveClass("is-stopped");
    expect(pill(render(<NodeGlyph size="lg" attempt={3} attemptStopped />).container)).toHaveClass("is-stopped");
  });

  it("carries no badges on the small strip size", () => {
    const { container } = render(<NodeGlyph size="sm" attempt={2} esc prob running />);
    expect(container.querySelector(".glyph-att, .glyph-esc, .glyph-prob, .glyph-running")).toBeNull();
  });

  it("shows paused instead of running, and the problem badge only without a pill", () => {
    const { container } = render(<NodeGlyph size="md" running paused prob attempt={2} />);
    expect(container.querySelector(".glyph-paused")).not.toBeNull();
    expect(container.querySelector(".glyph-running")).toBeNull();
    expect(container.querySelector(".glyph-prob")).toBeNull();
  });

  it("is decoration: hidden from assistive tech", () => {
    expect(render(<NodeGlyph />).container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });
});
