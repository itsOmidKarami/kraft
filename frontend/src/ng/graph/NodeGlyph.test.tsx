import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NodeGlyph } from "./NodeGlyph";
import { accessibleName } from "./types";

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

  it.each([
    ["two running", "md", 2, undefined, "2", false],
    ["one running", "lg", 1, undefined, "1", false],
    ["none", "md", 0, undefined, null, false],
    ["not given", "md", undefined, undefined, null, false],
    ["a box too small for badges", "sm", 2, 1, null, false],
    ["two running and one that did not finish", "md", 2, 1, "2", true],
    ["one that did not finish and none running", "md", undefined, 1, "", true],
  ] as const)("draws the sub-agents as a count badge on the box, with a cross for one that did not finish (%s)", (_n, size, subagents, unfinished, text, cross) => {
    const badge = render(<NodeGlyph size={size} subagents={subagents} unfinished={unfinished} />).container.querySelector(".glyph-subagents");
    if (text === null) return expect(badge).toBeNull();
    expect(badge).toHaveTextContent(text);
    expect(!!badge!.querySelector(".glyph-subagents-x")).toBe(cross);
  });

  it.each([
    [2, 0, "implement, task, running, 2 sub-agents running"],
    [1, 0, "implement, task, running, 1 sub-agent running"],
    [0, 0, "implement, task, running"],
    [1, 2, "implement, task, running, 1 sub-agent running, 2 sub-agents did not finish"],
    [0, 1, "implement, task, running, 1 sub-agent did not finish"],
  ])("names %s running and %s unfinished sub-agents on the button", (subagents, unfinished, name) => {
    expect(accessibleName({ id: "implement", state: "current", running: true, subagents, unfinished }, "task")).toBe(name);
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
