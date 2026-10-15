import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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

  it.each([
    ["a gate waiting on a person", "gate", "amber", ".glyph-gate .glyph-box.is-amber"],
    ["a paused item", "exec", "paused", ".glyph-box.is-paused"],
    ["a paused item at a gate", "gate", "paused", ".glyph-gate .glyph-box.is-paused"],
  ] as const)("%s draws its state class, and graph.css colours it (amber only for needs-you)", (_n, kind, state, rule) => {
    const { container } = render(<NodeGlyph kind={kind} size="sm" state={state} />);
    expect(container.querySelector(`.glyph-box.is-${state}`)).not.toBeNull();
    const css = readFileSync(resolve(__dirname, "graph.css"), "utf8");
    const line = css.split("\n").find((l) => l.startsWith(rule + " {"))!;
    expect(line, rule).toBeDefined();
    expect(line.includes("var(--warn)")).toBe(state === "amber");
  });

  it("is decoration: hidden from assistive tech", () => {
    expect(render(<NodeGlyph />).container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });
});
