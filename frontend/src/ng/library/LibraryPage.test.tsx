import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import { libView } from "./fixture";
import { draftWith, mount, ok, setup, where } from "./testSupport";

const list = () => screen.findByRole("listbox", { name: "Library components" });

beforeEach(setup);
afterEach(() => vi.unstubAllGlobals());

describe("Library page: the list", () => {
  it("groups the components by kind with counts, each with its used-by words", async () => {
    mount();
    const box = await list();
    const groups = within(box).getAllByRole("group").map((g) => g.getAttribute("aria-label"));
    expect(groups).toEqual(["Nodes", "Steps", "Tasks", "Steering"]);
    const tasks = within(box).getByRole("group", { name: "Tasks" });
    expect(within(tasks).getByRole("heading")).toHaveTextContent("Tasks 4");
    await waitFor(() => expect(within(tasks).getByRole("option", { name: /implementer/ })).toHaveTextContent("2 chains"));
    expect(within(tasks).getByRole("option", { name: /verify/ })).toHaveTextContent("unused");
    expect(within(box).getByRole("option", { name: /approval/ })).toHaveTextContent("unpublished");
  });

  it("puts the whole name in the row's title and lets a long id cut", async () => {
    mount();
    const box = await list();
    const long = within(box).getByTitle("never-signal-processes-you-didnt-start");
    expect(long).toHaveAttribute("data-allow-ellipsis");
  });

  it("opens a component by its ref in the URL, and says so for one that isn't there", async () => {
    const u = userEvent.setup();
    mount();
    await u.click(within(await list()).getByRole("option", { name: /implementer/ }));
    expect(where()).toBe("/templates/library/tasks.implementer");
    expect(within(await list()).getByRole("option", { name: /implementer/ })).toHaveAttribute("aria-selected", "true");
  });

  it("says when the ref names nothing", async () => {
    mount("/templates/library/tasks.nope");
    expect(await screen.findByText(/There is no component called tasks.nope/)).toBeInTheDocument();
  });

  it("hides a kind the filter turns off, remembers it, and still renders when storage throws", async () => {
    const u = userEvent.setup();
    const { unmount } = mount();
    await list();
    await u.click(screen.getByRole("button", { name: "Filter kinds" }));
    await u.click(within(screen.getByRole("dialog", { name: "Show kinds" })).getByRole("checkbox", { name: /Steering/ }));
    expect(within(await list()).queryByRole("group", { name: "Steering" })).toBeNull();
    unmount();
    mount();
    await list();
    expect(screen.queryByRole("group", { name: "Steering" })).toBeNull();
    unmount();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    mount();
    expect(await screen.findByRole("group", { name: "Steering" })).toBeInTheDocument();
  });

  it("searches by name, and / focuses the search, ↓ walks the rows", async () => {
    const u = userEvent.setup();
    mount();
    await list();
    await u.keyboard("/");
    expect(screen.getByRole("textbox", { name: "Search the library" })).toHaveFocus();
    await u.keyboard("verif");
    expect(within(await list()).getAllByRole("option").map((o) => o.textContent)).toEqual([expect.stringContaining("verification"), expect.stringContaining("verify")]);
    await u.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[0]).toHaveFocus();
    await u.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[1]).toHaveFocus();
    await u.click(screen.getByRole("textbox", { name: "Search the library" }));
    await u.keyboard("zzz");
    expect(await screen.findByText("No component matches.")).toBeInTheDocument();
  });

  it("marks what the draft changes and what has a problem", async () => {
    draftWith({
      changes: [{ path: "tasks.implementer.prompt", kind: "change", summary: "prompt" }],
      problems: [{ path: "tasks.verify", field: "command", message: "Field required", file: "library.yaml", line: 3, col: 1 }],
    }, true);
    mount();
    const box = await list();
    expect(within(within(box).getByRole("option", { name: /implementer/ })).getByRole("img", { name: "changed in the draft" })).toBeInTheDocument();
    expect(within(within(box).getByRole("option", { name: /verify/ })).getByRole("img", { name: "has a problem" })).toBeInTheDocument();
  });
});

describe("Library page: + New", () => {
  const open = async (u: ReturnType<typeof userEvent.setup>, kind: string) => {
    await list();
    await u.click(screen.getByRole("button", { name: "New component" }));
    await u.click(await screen.findByRole("menuitem", { name: kind }));
    // The id field takes focus and selects its text a frame after it opens: typing waits for that.
    const field = await screen.findByRole("textbox", { name: new RegExp(`New ${kind.toLowerCase()}`) });
    await waitFor(() => expect(field).toHaveFocus());
    return field;
  };

  it("adds a component through its id step and opens it", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [{ op: "add_component" }] }) as never);
    mount();
    await u.type(await open(u, "Agent task"), "fixer{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "add_component", section: "tasks", name: "fixer", kind: "agent" }], undefined));
    expect(where()).toBe("/templates/library/tasks.fixer");
  });

  it("refuses a taken id before sending, and shows the server's refusal inline", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockResolvedValue({ status: 422, body: { detail: "name is reserved" } } as never);
    mount();
    const field = await open(u, "Steering profile");
    await u.type(field, "project-standards");
    expect(await screen.findByText("project-standards is taken.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await u.clear(field);
    await u.type(field, "other{Enter}");
    expect(await screen.findByText("name is reserved")).toBeInTheDocument();
    expect(where()).toBe("/templates/library");
  });

  it("sends the kind of a gate and none for a step", async () => {
    const u = userEvent.setup();
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...libView(), ops: [] }) as never);
    mount();
    await u.type(await open(u, "Step"), "build{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledWith("library", "library", [{ op: "add_component", section: "steps", name: "build" }], undefined));
  });
});

describe("Library page: header and YAML", () => {
  it("says published, or the draft's change count and problems, and steps through the components they name", async () => {
    const u = userEvent.setup();
    draftWith({
      changes: [{ path: "tasks.verify", kind: "change", summary: "command" }],
      problems: [{ path: "tasks.verify", field: "command", message: "Field required", file: "library.yaml", line: 3, col: 1 }],
    }, true);
    mount();
    expect(await screen.findByText("DRAFT · 1 CHANGE")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "1 PROBLEM" }));
    expect(where()).toBe("/templates/library/tasks.verify");
  });

  it("edits library.yaml in the YAML view, not a chain's file", async () => {
    const u = userEvent.setup();
    const put = vi.spyOn(d, "putFile").mockImplementation(() => ok(libView()) as never);
    mount();
    await list();
    await u.click(screen.getByRole("button", { name: "YAML" }));
    const area = await screen.findByRole("textbox", { name: "library.yaml, YAML" });
    await u.type(area, "#");
    await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
    expect(put).toHaveBeenCalledWith("library", "library", "library.yaml", expect.stringContaining("#"));
  });
});
