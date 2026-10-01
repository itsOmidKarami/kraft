import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import { LIB_MODEL, libView } from "./fixture";
import { draftWith, mount, ok, setup, where } from "./testSupport";

beforeEach(setup);
afterEach(() => vi.unstubAllGlobals());

const canvas = (name: string) => screen.findByRole("group", { name });

describe("Library canvas: a node", () => {
  it("draws its steps and tasks, a task's kind coming from the task it extends", async () => {
    mount("/templates/library/nodes.verification");
    const g = await canvas("nodes.verification");
    expect(within(g).getByRole("button", { name: "review, step" })).toBeInTheDocument();
    // `code_review` writes only `extends: code_review`, whose own base is the agent task `implementer`.
    expect(within(g).getByRole("button", { name: "code_review, agent task" })).toBeInTheDocument();
    expect(await screen.findByText("used by default")).toBeInTheDocument();
    // A node can take another step before, between and after (the seams a one-step component lacks).
    expect(within(g).getAllByRole("button", { name: "Add a step here" }).length).toBeGreaterThan(0);
  });

  it("opens the failure handler of the task picked, at the task's own path", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, verification: { kind: "exec", steps: [{ id: "review", tasks: [{ id: "code_review", extends: "implementer", on_failure: { steps: [{ id: "main", tasks: [{ id: "cleanup", extends: "verify" }] }] } }] }] } } } } });
    mount("/templates/library/nodes.verification");
    await u.click(await screen.findByRole("button", { name: "code_review, agent task" }));
    await u.click(screen.getByRole("button", { name: "Expand the bottom pane" }));
    expect(await screen.findByRole("button", { name: "cleanup, subprocess task" })).toBeInTheDocument();
  });

  it("opens the node it extends when it has no steps of its own", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, fast: { extends: "verification" } } } } });
    mount("/templates/library/nodes.fast");
    await u.click(await screen.findByRole("button", { name: "Open it" }));
    expect(where()).toBe("/templates/library/nodes.verification");
  });

  it("draws a gate as a gate, with its message", async () => {
    mount("/templates/library/nodes.approval");
    // The gate view and the pane's message field both carry it.
    expect((await screen.findAllByText("Approve.")).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("message")).toHaveValue("Approve.");
  });

  it("adds a task beside one in a step with one add_task at the library path", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [{ op: "add_task" }] }) as never);
    mount("/templates/library/nodes.verification");
    await canvas("nodes.verification");
    await u.click(screen.getByRole("button", { name: "Add a parallel task" }));
    await u.click(await screen.findByRole("menuitem", { name: "Blank forge task" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "add_task", container: "nodes.verification", step: "review", id: "forge", kind: "forge" }], undefined));
  });

  it("opens the failure handlers in the bottom pane, drawn from the node as written", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, verification: { ...LIB_MODEL.nodes.verification, fix_loop: { steps: [{ id: "main", tasks: [{ id: "repair", extends: "implementer" }] }] } } } } } });
    mount("/templates/library/nodes.verification");
    await canvas("nodes.verification");
    await u.click(screen.getByRole("tab", { name: "Fix loop" }));
    expect(await screen.findByRole("button", { name: "repair, agent task" })).toBeInTheDocument();
  });

  it("moves the selected step with ⌥→, after a preview, and says why when it is refused", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, verification: { kind: "exec", steps: [{ id: "tests", tasks: [{ id: "t", extends: "verify" }] }, { id: "review", tasks: [{ id: "r", extends: "implementer" }] }] } } } } });
    const post = vi.spyOn(d, "postOps").mockImplementation((_a, _k, _o, preview) => (preview ? Promise.resolve({ status: 200, body: libView() }) : ok({ ...libView(), ops: [{ op: "move" }] })) as never);
    mount("/templates/library/nodes.verification");
    await u.click(await screen.findByRole("button", { name: "tests, step" }));
    await u.keyboard("{Alt>}{ArrowRight}{/Alt}");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[0]).toEqual(["library", "library", [{ op: "move", path: "nodes.verification.tests", to: 1 }], true]);
    expect(post.mock.calls[1]).toEqual(["library", "library", [{ op: "move", path: "nodes.verification.tests", to: 1 }], undefined]);
    // The last step has nowhere to go: nothing is sent.
    await u.click(screen.getByRole("button", { name: "review, step" }));
    await u.keyboard("{Alt>}{ArrowRight}{/Alt}");
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("refuses a move the server's preview refuses, and saves nothing", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, verification: { kind: "exec", steps: [{ id: "tests", tasks: [{ id: "t", extends: "verify" }] }, { id: "review", tasks: [{ id: "r", extends: "implementer" }] }] } } } } });
    const post = vi.spyOn(d, "postOps").mockResolvedValue({ status: 422, body: { detail: "step order is fixed" } } as never);
    mount("/templates/library/nodes.verification");
    await u.click(await screen.findByRole("button", { name: "tests, step" }));
    await u.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(await screen.findByText("Can't move tests: step order is fixed")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("Library canvas: a step and a task", () => {
  it("draws a step as its one step, with no seam to add another", async () => {
    mount("/templates/library/steps.checks");
    const g = await canvas("steps");
    expect(within(g).getByRole("button", { name: "checks, step" })).toBeInTheDocument();
    expect(within(g).getByRole("button", { name: "lint, subprocess task" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a step here" })).toBeNull();
  });

  it("adds a task to a step at the step's own path", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [{ op: "add_task" }] }) as never);
    mount("/templates/library/steps.checks");
    await canvas("steps");
    await u.click(screen.getByRole("button", { name: "Add a parallel task" }));
    await u.click(await screen.findByRole("menuitem", { name: "Blank agent task" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "add_task", container: "steps", step: "checks", id: "agent", kind: "agent" }], undefined));
  });

  it("draws a task as its one glyph, and opens the task it extends", async () => {
    const u = userEvent.setup();
    mount("/templates/library/tasks.code_review");
    expect(await screen.findByRole("button", { name: "code_review, agent task" })).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "extends implementer" }));
    expect(where()).toBe("/templates/library/tasks.implementer");
  });
});
