import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import { LIB_MODEL, libView } from "./fixture";
import { draftWith, mount, ok, setup, where } from "./testSupport";

beforeEach(setup);
afterEach(() => vi.unstubAllGlobals());

const opsAnswer = (ops: unknown[]) => ok({ ...libView({}, true), ops }) as never;
const select = async (ref: string) => {
  mount(`/templates/library/${ref}`);
  return screen.findByRole("heading", { name: ref.split(".")[1] });
};

describe("Library: rename", () => {
  it("renames from the title, tells how many references it rewrote, and follows the new name in the URL", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer([{ op: "rename", result: { updated: [{ file: "library.yaml", path: "tasks.code_review", field: "extends" }, { file: "chains/default.yaml", path: "implementation.main.implement", field: "extends" }] } }]));
    await select("tasks.implementer");
    await u.click(screen.getByRole("button", { name: "implementer" }));
    const field = await screen.findByRole("textbox", { name: "Rename task" });
    await waitFor(() => expect(field).toHaveFocus());
    await u.clear(field);
    await u.type(field, "builder{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "rename", path: "tasks.implementer", id: "builder" }], undefined));
    expect(await screen.findByText("Renamed implementer → builder · 2 references updated")).toBeInTheDocument();
    expect(where()).toBe("/templates/library/tasks.builder");
  });

  it("opens with F2, refuses a taken id before sending, and shows the server's refusal inline", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockResolvedValue({ status: 422, body: { detail: "builder is reserved" } } as never);
    await select("tasks.implementer");
    await u.keyboard("{F2}");
    const field = await screen.findByRole("textbox", { name: "Rename task" });
    await waitFor(() => expect(field).toHaveFocus());
    await u.clear(field);
    await u.type(field, "code_review");
    expect(await screen.findByText("code_review is taken.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await u.clear(field);
    await u.type(field, "builder{Enter}");
    expect(await screen.findByText("builder is reserved")).toBeInTheDocument();
    expect(where()).toBe("/templates/library/tasks.implementer");
  });
});

describe("Library: rename, then another pick", () => {
  it("a click on another component while renaming commits the rename and keeps the new pick", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer([{ op: "rename", result: { updated: [] } }]));
    await select("tasks.implementer");
    await u.click(screen.getByRole("button", { name: "implementer" }));
    await u.type(await screen.findByRole("textbox", { name: "Rename task" }), "x");
    await u.click(screen.getByRole("option", { name: /^code_review/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "rename", path: "tasks.implementer", id: "implementerx" }], undefined));
    expect(await screen.findByText(/Renamed implementer → implementerx/)).toBeInTheDocument();
    expect(where()).toBe("/templates/library/tasks.code_review");
  });
});

describe("Library: remove", () => {
  it("lists the chains that use it first, removes with one op, says how many uses it left broken, and opens the next row", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer([{ op: "remove", result: { broken: [{ file: "chains/default.yaml", path: "implementation.main.implement", field: "extends" }] } }]));
    await select("tasks.implementer");
    await u.click(await screen.findByRole("button", { name: "Remove task" }));
    const card = await screen.findByRole("dialog", { name: "Remove task" });
    expect(within(card).getByText("default · implementation.main.implement")).toBeInTheDocument();
    expect(within(card).getByText("quick-task · build.main.go")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await u.click(within(card).getByRole("button", { name: "Remove task" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "remove", path: "tasks.implementer" }], undefined));
    expect(await screen.findByText("Removed tasks.implementer · 1 use now broken · ⌘Z undoes it")).toBeInTheDocument();
    expect(where()).toBe("/templates/library/tasks.code_review");
  });

  it("removes a step of a node and goes back to the node", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer([{ op: "remove", result: { broken: [] } }]));
    await select("nodes.verification");
    await u.click(await screen.findByRole("button", { name: "review, step" }));
    await u.click(await screen.findByRole("button", { name: "Remove step" }));
    await u.click(within(await screen.findByRole("dialog", { name: "Remove step" })).getByRole("button", { name: "Remove step" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "remove", path: "nodes.verification.review" }], undefined));
    expect(await screen.findByRole("heading", { name: "verification" })).toBeInTheDocument();
  });

  it("removes a node's escalation at the escalation's own path, not at its task", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, verification: { ...LIB_MODEL.nodes.verification, escalation: { id: "escalate", extends: "implementer" } } } } } });
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer([{ op: "remove", result: { broken: [] } }]));
    await select("nodes.verification");
    await u.click(await screen.findByRole("tab", { name: "Escalation" }));
    // The tab picks the escalation, so the side pane shows it: its footer's Remove is the one under test.
    await screen.findByRole("heading", { name: "escalate" });
    await u.click(within(document.querySelector<HTMLElement>(".pane")!).getByRole("button", { name: "Remove escalation" }));
    await u.click(within(await screen.findByRole("dialog", { name: "Remove escalation" })).getByRole("button", { name: "Remove escalation" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "remove", path: "nodes.verification.escalation" }], undefined));
  });

  it("undoes the last request with ⌘Z", async () => {
    const u = userEvent.setup();
    const undo = vi.spyOn(d, "undo").mockImplementation(() => ok(libView({}, true)) as never);
    await select("tasks.implementer");
    await u.keyboard("{Meta>}z{/Meta}");
    await waitFor(() => expect(undo).toHaveBeenCalledTimes(1));
    expect(undo).toHaveBeenCalledWith("library", "library");
  });
});

describe("Library: duplicate", () => {
  it("copies a component's YAML into a new one of the same kind in one request, and opens it", async () => {
    const u = userEvent.setup();
    const frag = vi.spyOn(d, "fragment").mockImplementation(() => ok({ path: "tasks.implementer", text: "kind: agent\nprompt: Implement the plan.\n" }) as never);
    const post = vi.spyOn(d, "postOps").mockImplementation(() => opsAnswer([{ op: "add_component" }, { op: "set_fragment" }]));
    await select("tasks.implementer");
    await u.click(await screen.findByRole("button", { name: "Duplicate" }));
    const field = await screen.findByRole("textbox", { name: "Duplicate implementer as" });
    await waitFor(() => expect(field).toHaveFocus());
    expect(field).toHaveValue("implementer_copy");
    await u.keyboard("{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(frag).toHaveBeenCalledWith("library", "library", "tasks.implementer");
    expect(post).toHaveBeenCalledWith("library", "library", [
      { op: "add_component", section: "tasks", name: "implementer_copy", kind: "agent" },
      { op: "set_fragment", path: "tasks.implementer_copy", yaml: "kind: agent\nprompt: Implement the plan.\n" },
    ], undefined);
    expect(where()).toBe("/templates/library/tasks.implementer_copy");
  });

  it("refuses a name the section already has", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps");
    await select("tasks.implementer");
    await u.click(await screen.findByRole("button", { name: "Duplicate" }));
    const field = await screen.findByRole("textbox", { name: "Duplicate implementer as" });
    await waitFor(() => expect(field).toHaveFocus());
    await u.clear(field);
    await u.type(field, "verify");
    expect(await screen.findByText("verify is taken.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
});

describe("Library: change base", () => {
  it("previews what a new base keeps and drops, then applies it", async () => {
    const u = userEvent.setup();
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, nodes: { ...LIB_MODEL.nodes, fast: { extends: "verification", read_only: true } } } } });
    const post = vi.spyOn(d, "postOps").mockImplementation((_a, _k, _o, preview) => opsAnswer([{ op: "change_base", ...(preview ? { result: { kept: ["read_only"], dropped: [{ key: "fix_loop", why: "approval has none" }] } } : {}) }]));
    await select("nodes.fast");
    await u.click(await screen.findByRole("button", { name: "Change base…" }));
    const menu = await screen.findByRole("dialog", { name: "Change base" });
    // The node being edited is not offered as its own base.
    expect(within(menu).queryByText("fast")).toBeNull();
    await u.click(within(menu).getByText("approval"));
    const card = await screen.findByRole("dialog", { name: "Change base of nodes.fast" });
    expect(card).toHaveTextContent("read_only");
    expect(card).toHaveTextContent("fix_loop · approval has none");
    await u.click(within(card).getByRole("button", { name: "Change base" }));
    await waitFor(() => expect(post).toHaveBeenLastCalledWith("library", "library", [{ op: "change_base", node: "nodes.fast", base: "approval" }], undefined));
    expect(post.mock.calls[0][3]).toBe(true);
  });
});
