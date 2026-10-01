import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { Toaster } from "../ui/Toast";
import { DEFAULT_VIEW } from "../templates/draft/fixture.default";
import type { DraftView, Result, StaleBody } from "../templates/draft/types";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { resolvedNode } from "../templates/draft/view";
import { ChainPane } from "../templates/panes/ChainPane";
import { resetLibrary } from "../templates/useLibrary";
import { ReviewPane } from "../templates/ReviewPane";
import { LIB_FILE } from "./fixture";

const SIZE = { width: 380, overlay: false, handle: undefined };
const CHAIN_FILE = "chains/default.yaml";

/** The default chain with a `lint` node of its own: one subprocess task `run`, nothing extended. */
const LINT = { id: "lint", kind: "exec", steps: [{ id: "main", tasks: [{ id: "run", kind: "subprocess", command: "make lint" }] }] };
const withLint = (): Partial<Result> => {
  const model = structuredClone(DEFAULT_VIEW.result.model);
  (model[CHAIN_FILE].nodes as unknown[]).push(LINT);
  return { model };
};

function mountPane(path: string, ops: ConfigDraft["ops"] = vi.fn(() => Promise.resolve({ status: 200, body: { ops: [{ op: "move_to_library" }] } })) as never) {
  const view: DraftView = { ...DEFAULT_VIEW, result: { ...DEFAULT_VIEW.result, ...withLint() } };
  const draft = { view, scope: { area: "chains", key: "default" }, field: vi.fn(), ops, flush: vi.fn(), resolvedNode: (id: string) => resolvedNode(view.result, id) } as unknown as ConfigDraft;
  render(<><ChainPane draft={draft} scope={draft.scope} path={path} open size={SIZE} onCollapse={() => {}} onExpand={() => {}} goTo={vi.fn()} /><Toaster /></>);
  return draft;
}

const lib = (names: string[]) => ({ file: "", text: "tasks: {}\n", components: names.map((id) => ({ id, kind: id.split(".")[0], name: id.split(".")[1], definition: {}, used_by: [], issues: [] })) });

beforeEach(() => {
  resetLibrary();
  vi.restoreAllMocks();
  vi.spyOn(api, "getLibrary").mockResolvedValue(lib(["tasks.implementer", "nodes.verification"]) as never);
  vi.spyOn(api, "getHarnesses").mockResolvedValue({ file: "", error: null, profiles: [], agent_profiles: [] });
  vi.spyOn(api, "getHarnessProviders").mockResolvedValue({ valid: {}, invalid: {} });
});

describe("Move to library", () => {
  it("is offered on a task and an exec node that extend nothing, not on a gate, an extending task or one in a handler", async () => {
    mountPane("lint.main.run");
    expect(screen.getByRole("button", { name: "Move to library…" })).toBeInTheDocument();
    for (const [path, kind] of [["lint", "node"], ["spec_approval", "gate"], ["spec.main.author", "extending task"], ["verification.fix_loop.main.repair", "handler task"], ["verification.review", "step"]] as const) {
      document.body.innerHTML = "";
      mountPane(path);
      if (kind === "node") expect(screen.getByRole("button", { name: "Move to library…" })).toBeInTheDocument();
      else expect(screen.queryByRole("button", { name: "Move to library…" }), kind).toBeNull();
    }
  });

  it("says what the library gains and what the chain keeps, follows the typed name, and refuses a taken one before sending", async () => {
    const u = userEvent.setup();
    const draft = mountPane("lint.main.run");
    await u.click(screen.getByRole("button", { name: "Move to library…" }));
    const field = await screen.findByRole("textbox", { name: "Move to library" });
    await waitFor(() => expect(field).toHaveFocus());
    expect(field).toHaveValue("run");
    expect(screen.getByText("The library gains tasks.run (subprocess task, 2 keys).")).toBeInTheDocument();
    expect(screen.getByText("default keeps { id: run, extends: run }.")).toBeInTheDocument();
    await u.clear(field);
    await u.type(field, "implementer");
    expect(await screen.findByText("implementer is taken.")).toBeInTheDocument();
    expect(screen.getByText("default keeps { id: run, extends: implementer }.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move" })).toBeDisabled();
    expect(draft.ops).not.toHaveBeenCalled();
  });

  it("sends move_to_library once, and tells the person it can be undone", async () => {
    const u = userEvent.setup();
    const draft = mountPane("lint.main.run");
    await u.click(screen.getByRole("button", { name: "Move to library…" }));
    const field = await screen.findByRole("textbox", { name: "Move to library" });
    await waitFor(() => expect(field).toHaveFocus());
    await u.clear(field);
    await u.type(field, "lint_task{Enter}");
    await waitFor(() => expect(draft.ops).toHaveBeenCalledTimes(1));
    expect(draft.ops).toHaveBeenCalledWith([{ op: "move_to_library", path: "lint.main.run", name: "lint_task" }], { quiet: true });
    expect(await screen.findByText("Moved to the library as tasks.lint_task · ⌘Z undoes it")).toBeInTheDocument();
  });

  it("shows the server's refusal on the card and stays", async () => {
    const u = userEvent.setup();
    const ops = vi.fn(() => Promise.resolve({ status: 422, body: { detail: "the library already has tasks.build" } })) as never;
    mountPane("lint.main.run", ops);
    await u.click(screen.getByRole("button", { name: "Move to library…" }));
    const field = await screen.findByRole("textbox", { name: "Move to library" });
    await waitFor(() => expect(field).toHaveFocus());
    await u.clear(field);
    await u.type(field, "build{Enter}");
    expect(await screen.findByText("the library already has tasks.build")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Move to library" })).toBeInTheDocument();
  });

  it("links a component that extends a library one to it", async () => {
    mountPane("spec.main.author");
    expect(screen.getByText("tasks.spec_author")).toHaveClass("lib-hint-link");
    document.body.innerHTML = "";
    mountPane("verification");
    expect(screen.getByText("nodes.verification")).toHaveClass("lib-hint-link");
    document.body.innerHTML = "";
    mountPane("lint.main.run");
    expect(screen.queryByText(/In the library:/)).toBeNull();
  });

  it("uses the nodes section for an exec node", async () => {
    const u = userEvent.setup();
    mountPane("lint");
    await u.click(screen.getByRole("button", { name: "Move to library…" }));
    expect(await screen.findByText("The library gains nodes.lint (exec node, 2 keys).")).toBeInTheDocument();
  });
});

describe("Review & publish after Move to library", () => {
  const joined = (): DraftView => ({
    ...DEFAULT_VIEW,
    draft: true,
    files: { ...DEFAULT_VIEW.files, [LIB_FILE]: "tasks:\n  run:\n    kind: subprocess\n" },
    base: { ...DEFAULT_VIEW.base, [LIB_FILE]: "0".repeat(64) },
    result: { ...DEFAULT_VIEW.result, model: { ...DEFAULT_VIEW.result.model, [LIB_FILE]: { tasks: { implementer: {}, run: { kind: "subprocess" } } } } },
  });
  const mountReview = (stale: StaleBody | null = null) => {
    const draft = { view: joined(), stale, publish: vi.fn(() => Promise.resolve({ status: 200, body: {} })), keepMine: vi.fn(), discard: vi.fn() } as unknown as ConfigDraft;
    render(<><ReviewPane draft={draft} scope={{ area: "chains", key: "default" }} published={"id: default\n"} libraryPublished={"tasks:\n  implementer: {}\n"} open size={SIZE} onCollapse={() => {}} onExpand={() => {}} onFix={() => {}} onHighlight={() => {}} onDone={() => {}} /><Toaster /></>);
    return draft;
  };

  it("lists the component the library gains, and says the publish writes library.yaml too", async () => {
    mountReview();
    expect(await screen.findByText("tasks.run")).toBeInTheDocument();
    expect(screen.getByText("added to the library")).toBeInTheDocument();
    expect(screen.getByText(/ready to publish · writes library.yaml too/)).toBeInTheDocument();
  });

  it("diffs both files under their names, the library against its published text", async () => {
    const u = userEvent.setup();
    mountReview();
    await u.click(screen.getByRole("tab", { name: "YAML diff" }));
    expect([...document.querySelectorAll(".tpl-rv-file")].map((e) => e.textContent)).toEqual([CHAIN_FILE, "library.yaml"]);
    const adds = [...document.querySelectorAll(".tpl-rv-line.is-add")].map((e) => e.textContent);
    expect(adds).toContain("+   run:\n");
  });

  it("publishes the draft once and says both files went", async () => {
    const u = userEvent.setup();
    const draft = mountReview();
    await u.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(draft.publish).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Published default and the library · new items use it from now on")).toBeInTheDocument();
  });

  it("shows no library section when the draft carries no library file", async () => {
    const draft = { view: { ...DEFAULT_VIEW, draft: true }, stale: null, publish: vi.fn(), keepMine: vi.fn(), discard: vi.fn() } as unknown as ConfigDraft;
    render(<ReviewPane draft={draft} scope={{ area: "chains", key: "default" }} published={"id: default\n"} open size={SIZE} onCollapse={() => {}} onExpand={() => {}} onFix={() => {}} onHighlight={() => {}} onDone={() => {}} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(screen.queryByText("added to the library")).toBeNull();
    expect(screen.queryByText(/writes library.yaml too/)).toBeNull();
  });
});
