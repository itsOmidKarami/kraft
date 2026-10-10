import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ownRows as desktopOwnRows } from "../../library/config";
import { LibraryComponentView, LibraryList, ownRows } from "./Library";
import { mountAt, posts, problem, view, where } from "./testkit";

const MODEL = {
  "library.yaml": {
    nodes: { verification: { kind: "exec", steps: [{ id: "checks", tasks: ["lint"] }] } },
    steps: { checks: { tasks: [] } },
    tasks: {
      implementer: { kind: "agent", profile: "strong", auto: true, policy: { time_cap_minutes: 120 }, tags: ["a", "b"] },
      "never-signal-processes-you-didnt-start": { kind: "agent" },
    },
    steering: { house: { instructions: "Be careful with ports." } },
  },
};
const PUBLISHED = {
  file: "library.yaml", text: "",
  components: [
    { id: "tasks.implementer", kind: "tasks", name: "implementer", used_by: ["default", "docs_only"], used_by_paths: [{ chain: "default", path: "implementation.main.implementer", overrides: false }, { chain: "docs_only", path: "implementation.main.implementer", overrides: false, via: "nodes.implementation" }] },
    { id: "nodes.verification", kind: "nodes", name: "verification", used_by: [], used_by_paths: [] },
  ],
};
const lib = (over = {}, r = {}): Record<string, [number, unknown]> => ({ "GET /drafts/library/library": [200, view("library", "library", { files: { "library.yaml": "x" }, ...over }, { model: MODEL as never, ...r })] as [number, unknown], "GET /templates/library": [200, PUBLISHED] as [number, unknown] });
afterEach(() => vi.unstubAllGlobals());

describe("Library list (L.2)", () => {
  const list = (path = "/templates/library", answers = lib()) => mountAt(<LibraryList />, path, "/templates/library", answers);

  it("has the four tabs, Steps among them, and opens on Nodes", async () => {
    list();
    const tabs = await screen.findByRole("tablist", { name: "Library" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Nodes", "Steps", "Tasks", "Steering"]);
    expect(within(tabs).getByRole("tab", { name: "Nodes", selected: true })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /verification/ })).toHaveAttribute("href", "/templates/library/nodes.verification");
  });

  it("keeps the kind in the URL and lists that kind's components with who uses them", async () => {
    list();
    await userEvent.click(await screen.findByRole("tab", { name: "Tasks" }));
    expect(where()).toBe("/templates/library?kind=tasks");
    const row = await screen.findByRole("link", { name: /^implementer/ });
    await waitFor(() => expect(row).toHaveTextContent("used in 2 chains"));
    expect(screen.queryByRole("link", { name: /verification/ })).toBeNull();
  });

  it("shows a long id whole, wrapping, never cut (R10)", async () => {
    list("/templates/library?kind=tasks");
    expect(await screen.findByText("never-signal-processes-you-didnt-start")).toBeInTheDocument();
  });

  it("filters by name and says when nothing matches", async () => {
    list("/templates/library?kind=tasks");
    await screen.findByRole("link", { name: /^implementer/ });
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the library" }), "zzz");
    expect(await screen.findByText("Nothing matches.")).toBeInTheDocument();
  });

  it("marks a component with a problem, and one the draft changed", async () => {
    list("/templates/library?kind=tasks", lib({ draft: true }, { problems: [problem("tasks.implementer", "bad", { component: "tasks.implementer" })], changes: [{ path: "tasks.implementer", kind: "change" as const, summary: "x" }] }));
    expect(await screen.findByRole("link", { name: /^implementer/ })).toHaveTextContent("problem");
  });
});

describe("Library component (L.2)", () => {
  const open = (ref: string, answers = lib()) => mountAt(<LibraryComponentView />, `/templates/library/${ref}`, "/templates/library/:ref", answers);

  it("shows a task's settings, and what uses it, linking to those chains", async () => {
    open("tasks.implementer");
    expect(await screen.findByRole("heading", { level: 1, name: "implementer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^profile/ })).toHaveTextContent("strong");
    expect(screen.getByRole("button", { name: /^policy.time_cap_minutes/ })).toHaveTextContent("120");
    expect(screen.getByText("a, b")).toBeInTheDocument();
    const used = await screen.findByRole("region", { name: "Used in" });
    expect(within(used).getByRole("link", { name: /default/ })).toHaveAttribute("href", "/templates/chains/default");
    expect(within(used).getByRole("link", { name: /docs_only/ })).toHaveTextContent("via nodes.implementation");
  });

  it("a plugin's task is listed with its plugin and read with nothing to tap, beside a local one that edits", async () => {
    const shipped = { plugin_library: { tasks: { "release:base": { kind: "agent", profile: "strong", auto: true } } } };
    const answers = { ...lib(shipped), "GET /templates/library": [200, { ...PUBLISHED, components: [...PUBLISHED.components, { id: "tasks.release:base", kind: "tasks", name: "release:base", used_by: [], used_by_paths: [], plugin: { id: "release@acme", version: "1.0.0" } }] }] as [number, unknown] };
    mountAt(<LibraryList />, "/templates/library?kind=tasks", "/templates/library", answers);
    await waitFor(() => expect(screen.getByRole("link", { name: /^release:base/ })).toHaveTextContent("release@acme 1.0.0"));
    expect(screen.getByRole("link", { name: /^release:base/ })).toHaveAttribute("href", "/templates/library/tasks.release%3Abase");
    expect(screen.getByRole("link", { name: /^implementer/ })).not.toHaveTextContent("release@acme");
    cleanup();
    const { calls } = open("tasks.release%3Abase", answers);
    expect(await screen.findByRole("heading", { level: 1, name: "release:base" })).toBeInTheDocument();
    expect(await screen.findByText("Task · release@acme 1.0.0")).toBeInTheDocument();
    expect(screen.getByText("strong")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^profile/ })).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(posts(calls)).toEqual([]);
  });

  it("an edit sends the desktop's set_field op for that component and field", async () => {
    const { calls } = open("tasks.implementer", { ...lib(), "POST /drafts/library/library/ops": [200, { ...view("library", "library", { draft: true }, { model: MODEL as never }), ops: [] }] });
    await userEvent.click(await screen.findByRole("button", { name: /^profile/ }));
    const input = screen.getByLabelText("profile", { selector: "input" });
    await userEvent.clear(input);
    await userEvent.type(input, "fast{Enter}");
    await waitFor(() => expect(posts(calls).map((c) => c.body)).toEqual([{ ops: [{ op: "set_field", path: "tasks.implementer", field: "profile", value: "fast" }] }]));
  });

  it("a number is sent as a number, and a non-number is refused before the call", async () => {
    const { calls } = open("tasks.implementer", { ...lib(), "POST /drafts/library/library/ops": [200, { ...view("library", "library", { draft: true }, { model: MODEL as never }), ops: [] }] });
    await userEvent.click(await screen.findByRole("button", { name: /^policy.time_cap_minutes/ }));
    const input = screen.getByLabelText("policy.time_cap_minutes", { selector: "input" });
    await userEvent.clear(input);
    await userEvent.type(input, "abc{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number.");
    expect(posts(calls)).toEqual([]);
    await userEvent.clear(input);
    await userEvent.type(input, "90{Enter}");
    await waitFor(() => expect(posts(calls)[0].body).toEqual({ ops: [{ op: "set_field", path: "tasks.implementer", field: "policy.time_cap_minutes", value: 90 }] }));
  });

  it("a switch sets a boolean setting", async () => {
    const { calls } = open("tasks.implementer", { ...lib(), "POST /drafts/library/library/ops": [200, { ...view("library", "library", { draft: true }, { model: MODEL as never }), ops: [] }] });
    await userEvent.click(await screen.findByRole("switch", { name: /^auto/ }));
    await waitFor(() => expect(posts(calls)[0].body).toEqual({ ops: [{ op: "set_field", path: "tasks.implementer", field: "auto", value: false }] }));
  });

  it("a steering profile's instructions are editable and cannot be emptied", async () => {
    const { calls } = open("steering.house", { ...lib(), "POST /drafts/library/library/ops": [200, { ...view("library", "library", { draft: true }, { model: MODEL as never }), ops: [] }] });
    await userEvent.click(await screen.findByRole("button", { name: /Be careful with ports/ }));
    const box = screen.getByLabelText("Instructions", { selector: "textarea,input" });
    await userEvent.clear(box);
    await userEvent.type(box, " {Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("can't be empty");
    expect(posts(calls)).toEqual([]);
  });

  it("names the chain a problem breaks", async () => {
    open("tasks.implementer", lib({ draft: true }, { problems: [problem("tasks.implementer", "Value error, unknown profile", { chain: "default", component: "tasks.implementer" })] }));
    const g = await screen.findByRole("region", { name: "Problems" });
    expect(within(g).getByText("unknown profile")).toBeInTheDocument();
    expect(within(g).getByText("breaks default")).toBeInTheDocument();
  });

  it("says there is no such component", async () => {
    open("tasks.nope");
    expect(await screen.findByText("There is no component tasks.nope.")).toBeInTheDocument();
  });
});

describe("ownRows is the desktop's", () => {
  it("returns the same fields and values for a nested definition", () => {
    const def = { kind: "agent", extends: "x", profile: "strong", policy: { time_cap_minutes: 120, wait: { a: 1 } }, on_base_changed: { restart_from: "plan" }, instructions: "i", tags: ["a"] };
    expect(ownRows(def)).toEqual(desktopOwnRows(def));
    expect(ownRows(null)).toEqual(desktopOwnRows(null));
  });
});
