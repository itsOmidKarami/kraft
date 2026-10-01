import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { floorLanes, harnessLanes, profileLanes } from "./model";
import { NEVER, NO_ENTRY, TASKS, renderPage, resolved, serve, view } from "./testkit";

afterEach(() => vi.unstubAllGlobals());

describe("lane models", () => {
  it("floor: one lane per harness with its Access tag and own tasks, dimmed when Never and empty", () => {
    const lanes = floorLanes(resolved(), []);
    expect(lanes.map((l) => [l.title, l.tag])).toEqual([["claude", "Available"], ["claude-sandbox", "Override"], ["codex", "Available"], ["cursor", "Never"], ["gemini", "Never"]]);
    expect(lanes[0].right).toBe("claude · 4 tasks");
    expect(lanes[3].dim).toBe(true);
    expect(lanes[1].dim).toBe(false);
  });

  it("floor: the first five tasks and a +N more", () => {
    const many = Array.from({ length: 8 }, (_, i) => ({ chain: "c", path: `n.s.t${i}`, profile: null, fallback: false }));
    const r = resolved();
    r.harnesses[0].tasks = many;
    const [claude] = floorLanes(r, []);
    expect(claude.tasks).toHaveLength(5);
    expect(claude.more).toBe(3);
  });

  it("floor: a lane is red when one of its tasks has a problem", () => {
    expect(floorLanes(resolved(), [NEVER])[0].red).toBe(true);
    expect(floorLanes(resolved(), [NEVER])[2].red).toBe(false);
  });

  it("harness: one lane per profile with its entry, a dashed no-profile lane last, a red no-entry lane", () => {
    const lanes = harnessLanes(resolved(), "claude");
    expect(lanes.map((l) => [l.title, l.model, l.effort, l.dash])).toEqual([["strong", "sonnet", "high", false], ["no profile", "sonnet", undefined, true]]);
    const codex = harnessLanes(resolved(), "codex");
    expect(codex[0]).toMatchObject({ title: "fast", model: "no entry", red: true, note: "fast has no codex entry" });
  });

  it("profile: one lane per provider entry, and a red lane for a provider a task needs", () => {
    const lanes = profileLanes(resolved(), "fast");
    expect(lanes.map((l) => [l.title, l.red])).toEqual([["claude", false], ["no codex entry", true]]);
    expect(lanes[0].note).toBe("No task runs fast on claude yet.");
    expect(lanes[1].tasks.map((t) => t.path)).toEqual([TASKS.summary.path]);
    expect(profileLanes(resolved(), "deep")[0].note).toBe("No task runs deep on claude yet.");
  });
});

describe("the canvas", () => {
  it("draws the floor's lanes, and opens a harness from one", async () => {
    serve(view(resolved()));
    renderPage();
    const lane = await screen.findByLabelText("claude-sandbox");
    expect(within(lane).getByText("Override")).toBeInTheDocument();
    await userEvent.click(within(lane).getByRole("button"));
    expect(await screen.findByRole("complementary", { name: "claude-sandbox pane" })).toBeInTheDocument();
  });

  it("a harness's canvas has the profile lanes, the dashed lane, and the missing-entry lane", async () => {
    serve(view(resolved(), { problems: [NO_ENTRY] }));
    renderPage("?harness=codex");
    const lane = await screen.findByLabelText("fast");
    expect(lane).toHaveClass("is-red");
    expect(within(lane).getByText("fast has no codex entry")).toBeInTheDocument();
  });

  it("a task glyph names its chain and path, says when it has a problem, and links to Chains", async () => {
    serve(view(resolved(), { problems: [NEVER] }));
    renderPage();
    const glyphs = await screen.findAllByRole("link", { name: /default › implement\.main\.implementer.*has a problem/ });
    expect(glyphs[0]).toHaveAttribute("href", "/templates/chains/default/nodes/implement");
    expect(screen.getAllByRole("link", { name: /default › spec\.main\.spec_author. Open in Chains/ })[0]).not.toHaveTextContent("problem");
  });

  it("goes up one level on a background click: lane, then selection", async () => {
    serve(view(resolved()));
    renderPage("?harness=claude&lane=strong");
    await screen.findByRole("complementary", { name: "strong pane" });
    await userEvent.click(document.querySelector(".hn-canvas")!);
    expect(await screen.findByRole("complementary", { name: "claude pane" })).toBeInTheDocument();
    await userEvent.click(document.querySelector(".hn-canvas")!);
    expect(await screen.findByRole("complementary", { name: "harnesses pane" })).toBeInTheDocument();
  });

  it("an empty harness and an empty file have their words", async () => {
    serve(view(resolved()));
    renderPage("?harness=cursor");
    expect(await screen.findByText("No task selects this harness.")).toBeInTheDocument();
  });

  it("lanes are 520px wide", () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "harnesses.css"), "utf-8");
    expect(css).toMatch(/\.hn-lane \{[^}]*width: 520px/);
    expect(css).toMatch(/\.hn-lane \{[^}]*flex: none/);
  });
});
