import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import { ChainsPage } from "./ChainsPage";
import * as d from "./draft/draftApi";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { DraftView, Result } from "./draft/types";
import { nextStepId, uniq } from "./NodeView";
import { resetLibrary } from "./useLibrary";

const ok = <T,>(body: T) => Promise.resolve({ status: 200, body });
const withNodes = (nodes: Record<string, unknown>[], extra: Partial<Result> = {}): DraftView => ({
  ...DEFAULT_VIEW,
  draft: true,
  result: { ...DEFAULT_VIEW.result, model: { "chains/default.yaml": { id: "default", nodes } }, resolved: null, sources: {}, ...extra },
});

const mount = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
          <Route path="/templates/chains/:chain/nodes/:node" element={<ChainsPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.restoreAllMocks();
  resetLibrary();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(api, "getLibrary").mockResolvedValue({ file: "", text: "", components: [
    { id: "tasks.implementer", kind: "tasks", name: "implementer", definition: { kind: "agent", prompt: "Implement the approved plan." }, used_by: [], issues: [] },
    { id: "tasks.release:base", kind: "tasks", name: "release:base", definition: { kind: "agent", prompt: "Ship it." }, used_by: [], issues: [], plugin: { id: "release@acme", version: "1.0.0" } },
    { id: "tasks.verify_changed_scopes", kind: "tasks", name: "verify_changed_scopes", definition: { kind: "subprocess", command: "true" }, used_by: [], issues: [] },
    { id: "nodes.verification", kind: "nodes", name: "verification", definition: { kind: "exec", steps: [{ id: "tests", tasks: [] }, { id: "review", tasks: [] }] }, used_by: [], issues: [] },
  ] });
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(DEFAULT_VIEW));
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

describe("Chains page: the node view", () => {
  it("draws a node's resolved steps, its parallel tasks and their seams, under the strip", async () => {
    mount("/templates/chains/default/nodes/verification");
    // The file's first mount pays for the shell's and the chain page's imports: under a loaded CPU that ran past 1s.
    const g = await screen.findByRole("group", { name: "verification" }, { timeout: 5000 });
    expect(within(g).getAllByRole("button", { name: /, step$/ }).map((b) => b.textContent)).toEqual(["tests", "review"]);
    expect(within(g).getByRole("button", { name: "code_review, agent task" })).toBeInTheDocument();
    expect(within(g).getByRole("button", { name: "test_changed_scopes, subprocess task" })).toBeInTheDocument();
    expect(within(g).getAllByRole("button", { name: "Add a step here" })).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Back to the chain" })).toBeInTheDocument();
  });

  it("an empty node's two phrases are buttons: the first adds step_1, names it, then opens the task menu", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(withNodes([{ id: "lint", kind: "exec", steps: [] }])));
    const post = vi.spyOn(d, "postOps").mockImplementation((_a, _k, ops) =>
      ok({ ...withNodes([{ id: "lint", kind: "exec", steps: [{ id: ops[0].op === "rename" ? "checks" : "step_1", tasks: [] }] }], {
        resolved: { ...DEFAULT_VIEW.result.resolved!, chain: { id: "default", nodes: [{ id: "lint", kind: "exec", steps: [{ id: ops[0].op === "rename" ? "checks" : "step_1", tasks: [] }] }] } },
      }), ops: [] }));
    mount("/templates/chains/default/nodes/lint");
    expect(await screen.findByText(/This node is empty/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "extend a node from the library" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "add your first step" }));
    expect(post.mock.calls[0][2]).toEqual([{ op: "add_step", container: "lint", at: 0, id: "step_1" }]);
    const name = await screen.findByRole("textbox", { name: "Name the first step" });
    expect(name).toHaveValue("step_1");
    await waitFor(() => expect(name).toHaveFocus());
    await userEvent.clear(name);
    await userEvent.type(name, "checks{Enter}");
    await waitFor(() => expect(post.mock.calls[1][2]).toEqual([{ op: "rename", path: "lint.step_1", id: "checks" }]));
    const lib = await screen.findByRole("menuitem", { name: "From the library…" });
    await waitFor(() => expect(lib).toHaveFocus());
  });

  it("adds a blank agent task into a new step in one request, then opens it with the prompt focused", async () => {
    // As the server answers: the new task in the model; a blank agent task has no prompt, so nothing resolves.
    const nodes = (DEFAULT_VIEW.result.model["chains/default.yaml"].nodes as Record<string, unknown>[]).map((n) =>
      n.id === "verification" ? { id: "verification", extends: "verification", steps: [{ id: "tests", tasks: [] }, { id: "review", tasks: [] }, { id: "step_3", tasks: [{ id: "agent", kind: "agent" }] }] } : n);
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...withNodes(nodes), ops: [] }));
    mount("/templates/chains/default/nodes/verification");
    // The file's first mount pays for the shell's and the chain page's imports: under a loaded CPU that ran past 1s.
    const g = await screen.findByRole("group", { name: "verification" }, { timeout: 5000 });
    await userEvent.click(within(g).getAllByRole("button", { name: "Add a step here" })[2]);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Blank agent task" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][2]).toEqual([
      { op: "add_step", container: "verification", at: 2, id: "step_3" },
      { op: "add_task", container: "verification", step: "step_3", id: "agent", kind: "agent" },
    ]);
    await waitFor(() => expect(screen.getByRole("complementary", { name: "agent pane" })).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "prompt" })).toHaveFocus();
  });

  // A plugin's task is `release:base`; the id it gets is one an id may be.
  it.each([["impl", "implementer", "implementer"], ["release", "base", "release:base"]])("adds a library task beside a step's tasks from its parallel seam (%s)", async (typed, id, name) => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...DEFAULT_VIEW, ops: [] }));
    mount("/templates/chains/default/nodes/verification");
    // The file's first mount pays for the shell's and the chain page's imports: under a loaded CPU that ran past 1s.
    const g = await screen.findByRole("group", { name: "verification" }, { timeout: 5000 });
    await userEvent.click(within(g).getAllByRole("button", { name: "Add a parallel task" })[0]);
    await userEvent.click(await screen.findByRole("menuitem", { name: "From the library…" }));
    await userEvent.type(await screen.findByRole("textbox", { name: "Search the library" }), `${typed}{Enter}`);
    await waitFor(() => expect(post.mock.calls[0][2]).toEqual([{ op: "add_task", container: "verification", step: "tests", id, extends: name }]));
  });

  it("extends a library node from the empty node, and says what the base is", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(withNodes([{ id: "lint", kind: "exec", steps: [] }])));
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...withNodes([{ id: "lint", extends: "verification" }]), ops: [{ op: "extend", result: { dropped: [] } }] }));
    mount("/templates/chains/default/nodes/lint");
    await userEvent.click(await screen.findByRole("button", { name: "extend a node from the library" }));
    await userEvent.click(await screen.findByRole("option", { name: /verification/ }));
    await waitFor(() => expect(post.mock.calls[0][2]).toEqual([{ op: "extend", node: "lint", base: "verification" }]));
    expect(await screen.findByText(/Extends/)).toBeInTheDocument();
  });

  it("draws a gate's view with the add-a-reviewer slot, which adds an agent reviewer", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...DEFAULT_VIEW, ops: [] }));
    mount("/templates/chains/default/nodes/spec_approval");
    await userEvent.click(await screen.findByRole("button", { name: /add a reviewer/ }));
    const menu = await screen.findByRole("dialog", { name: "Add a reviewer" });
    expect(within(menu).queryByRole("menuitem", { name: "Blank builtin task" })).toBeNull();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Blank agent task" }));
    await waitFor(() => expect(post.mock.calls[0][2]).toEqual([{ op: "add_task", slot: "auto_review", node: "spec_approval", kind: "agent" }]));
  });

  it("draws the last resolved steps of a node while the draft does not resolve", async () => {
    mount("/templates/chains/default/nodes/verification");
    await screen.findByRole("group", { name: "verification" });
    vi.mocked(d.getDraft).mockImplementation(() => ok(withNodes(DEFAULT_VIEW.result.model["chains/default.yaml"].nodes as Record<string, unknown>[])));
    await act(async () => void window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(screen.getByText("DRAFT · 0 CHANGES")).toBeInTheDocument());
    expect(within(screen.getByRole("group", { name: "verification" })).getAllByRole("button", { name: /, step$/ })).toHaveLength(2);
  });

  it("names a new step as the server would", () => {
    expect(nextStepId([])).toBe("step_1");
    expect(nextStepId(["main"])).toBe("step_2");
    expect(nextStepId(["tests", "review"])).toBe("step_3");
    expect(nextStepId(["step_1", "step_3"])).toBe("step_4");
    expect(nextStepId(["a", "step_2"])).toBe("step_3");
    expect(uniq("agent", [])).toBe("agent");
    expect(uniq("agent", ["agent", "agent_2"])).toBe("agent_3");
  });

  it("opens the bottom pane collapsed on entering a node, and open on the tab of a handler item you came to", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok({ ...DEFAULT_VIEW, draft: true, result: { ...DEFAULT_VIEW.result, problems: [{ path: "verification.fix_loop.judge", field: "prompt", message: "Field required", file: "f", line: 1, col: 1 }] } }));
    mount("/templates/chains/default/nodes/verification");
    await screen.findByRole("group", { name: "verification" });
    expect(screen.getByRole("button", { name: "Expand the bottom pane" })).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(screen.getByRole("button", { name: "Back to the chain" }));
    await userEvent.click(await screen.findByRole("button", { name: "1 PROBLEM" }));
    expect(await screen.findByRole("button", { name: "Collapse the bottom pane" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tab", { name: "Fix loop" })).toHaveAttribute("aria-selected", "true");
  });
});
