import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Bug, Rocket } from "lucide-react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import { LIB_MODEL, libView } from "./fixture";
import { draftWith, mount, ok, setup, where } from "./testSupport";

vi.mock("../iconSetData", () => ({ ICONS: { bug: Bug, rocket: Rocket } }));

beforeEach(setup);
afterEach(() => vi.unstubAllGlobals());

const pane = async (ref: string) => {
  mount(`/templates/library/${ref}`);
  return screen.findByRole("heading", { name: ref.split(".")[1] });
};
const tab = (name: string) => userEvent.click(screen.getByRole("tab", { name }));

describe("Library pane: Overview", () => {
  it("names who uses a component: the chain as a link to its node, an override, a use through a library node", async () => {
    await pane("tasks.implementer");
    const a = await screen.findByRole("link", { name: "default" });
    expect(a).toHaveAttribute("href", "/templates/chains/default/nodes/implementation");
    expect(a.parentElement).toHaveTextContent("implementation.main.implementoverrides");
    expect(screen.getByRole("link", { name: "quick-task" }).parentElement).toHaveTextContent("via build");
  });

  it("says so when no chain uses it", async () => {
    await pane("tasks.verify");
    expect(await screen.findByText("Not used by any chain.")).toBeInTheDocument();
  });

  it("says how many chains the draft would break, from the problems that name the component", async () => {
    draftWith({ problems: [
      { path: "implementation.main.implement", field: "profile", message: "no profile", file: "chains/default.yaml", line: 1, col: 1, chain: "default", repo: null, component: "tasks.implementer" },
      { path: "build.main.go", field: "profile", message: "no profile", file: "chains/quick-task.yaml", line: 1, col: 1, chain: "quick-task", repo: null, component: "tasks.implementer" },
    ] }, true);
    await pane("tasks.implementer");
    expect(await screen.findByText(/2 chains would break/)).toBeInTheDocument();
    // The header's problem row names the chain, not a field of this component.
    expect(screen.getByText("breaks chain default")).toBeInTheDocument();
  });

  it("does not count a problem in library.yaml itself as a chain that would break", async () => {
    draftWith({ problems: [{ path: "tasks.implementer", field: "prompt", message: "Field required", file: "library.yaml", line: 3, col: 1, chain: null, repo: null, component: "tasks.implementer" }] }, true);
    await pane("tasks.implementer");
    await screen.findByText("Used by");
    expect(screen.queryByText(/would break/)).toBeNull();
  });

  it("shows a task's value from the component it extends, and does not call it missing", async () => {
    draftWith({ model: { "library.yaml": { ...LIB_MODEL, tasks: { ...LIB_MODEL.tasks, fast_review: { extends: "implementer" } } } } });
    await pane("tasks.fast_review");
    expect(await screen.findByLabelText("prompt")).toHaveValue("Implement the plan.");
    expect(screen.queryByText("Required.")).toBeNull();
  });

  it("lists a node's steps and walks into one, whose tasks open in turn", async () => {
    const u = userEvent.setup();
    await pane("nodes.verification");
    await u.click(await screen.findByRole("button", { name: "1 task" }));
    expect(await screen.findByRole("heading", { name: "review" })).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /agent · extends code_review/ }));
    expect(await screen.findByRole("heading", { name: "code_review" })).toBeInTheDocument();
    // Back up through the crumbs: the component, then the Library.
    await u.click(screen.getByRole("button", { name: "verification" }));
    expect(await screen.findByRole("heading", { name: "verification" })).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Library" }));
    expect(where()).toBe("/templates/library");
  });
});

describe("Library pane: Config and YAML", () => {
  it("lists the keys a component writes, and an edit sends one set_field at the library path", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [{ op: "set_field" }] }) as never);
    await pane("tasks.implementer");
    await tab("Config");
    expect([...document.querySelectorAll(".cfg-k")].map((e) => e.textContent)).toEqual(["prompt"]);
    await u.click(screen.getByRole("button", { name: "Edit prompt" }));
    const field = screen.getByRole("textbox", { name: "prompt" });
    await u.clear(field);
    await u.type(field, "Do it well.");
    await u.keyboard("{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "set_field", path: "tasks.implementer", field: "prompt", value: "Do it well." }], undefined));
    expect(within(screen.getByRole("tab", { name: "Config" }).closest("aside") ?? document.body).queryByLabelText("overridden here")).toBeNull();
  });

  it("reads the component's own YAML from the library draft", async () => {
    const frag = vi.spyOn(d, "fragment").mockImplementation(() => ok({ path: "tasks.implementer", text: "kind: agent\n" }) as never);
    await pane("tasks.implementer");
    await tab("YAML");
    await waitFor(() => expect(frag).toHaveBeenCalledWith("library", "library", "tasks.implementer"));
  });
});

describe("Library pane: icon", () => {
  it("picks an icon for the component and sends it as a set_field at its path", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [{ op: "set_field" }] }) as never);
    await pane("tasks.implementer");
    await u.click(screen.getByRole("button", { name: /Icon.*change/ }));
    await u.click(await screen.findByRole("option", { name: "rocket" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "set_field", path: "tasks.implementer", field: "icon", value: "rocket" }], undefined));
  });
});
