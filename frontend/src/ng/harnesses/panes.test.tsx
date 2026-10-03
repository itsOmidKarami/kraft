import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NO_ENTRY, TASKS, renderPage, resolved, serve, view } from "./testkit";

afterEach(() => vi.unstubAllGlobals());
const pane = (name: string) => screen.findByRole("complementary", { name: `${name} pane` });

describe("harness pane", () => {
  it("shows the file's fields as controls, whether the executable is found, and what the provider accepts", async () => {
    serve(view(resolved()));
    renderPage("?harness=gemini");
    const p = await pane("gemini");
    expect(within(p).getByText("not on PATH")).toBeInTheDocument();
    expect(within(p).getByRole("switch", { name: "Disable gemini" })).toBeChecked();
    expect(within(p).getByRole("textbox", { name: "executable" })).toHaveValue("");
    expect(within(p).getByRole("textbox", { name: "default model" })).toHaveValue("");
    expect(within(p).queryByRole("radiogroup", { name: "Default effort" })).toBeNull();
    expect(within(p).queryByText("permission mode")).toBeNull();
    expect(within(p).getByText(/effort: none/)).toBeInTheDocument();
  });

  it("claude: defaults, and the provider's efforts and models", async () => {
    serve(view(resolved()));
    renderPage("?harness=claude");
    const p = await pane("claude");
    expect(await within(p).findByText("effort: low, medium, high, xhigh, max")).toBeInTheDocument();
    expect(await within(p).findByText("models: sonnet, opus, haiku")).toBeInTheDocument();
    expect(within(p).getByRole("combobox", { name: "default model" })).toHaveValue("sonnet");
    expect(within(p).getByRole("radio", { name: "not set", checked: true })).toBeInTheDocument();
    expect(within(p).getByRole("radio", { name: "acceptEdits" })).toBeChecked();
  });

  it("each control sends one set_harness patch with its own field, and an empty one clears", async () => {
    const server = serve(view(resolved()));
    renderPage("?harness=claude-sandbox");
    const p = await pane("claude-sandbox");
    await userEvent.click(within(p).getByRole("switch", { name: "Disable claude-sandbox" }));
    const exe = within(p).getByRole("textbox", { name: "executable" });
    await userEvent.clear(exe);
    await userEvent.type(exe, "/opt/claude{Enter}");
    await userEvent.clear(exe);
    await userEvent.type(exe, "{Enter}");
    const model = within(p).getByRole("combobox", { name: "default model" });
    await userEvent.type(model, "opus{Enter}");
    await userEvent.click(within(p).getByRole("radio", { name: "max" }));
    await userEvent.click(within(p).getByRole("radio", { name: "plan" }));
    const id = "claude-sandbox";
    await waitFor(() => expect(server.ops).toHaveLength(6));
    expect(server.ops.flat()).toEqual([
      { op: "set_harness", id, patch: { enabled: false } },
      { op: "set_harness", id, patch: { executable: "/opt/claude" } },
      { op: "set_harness", id, patch: { executable: null } },
      { op: "set_harness", id, patch: { defaults: { model: "opus" } } },
      { op: "set_harness", id, patch: { defaults: { effort: "max" } } },
      { op: "set_harness", id, patch: { defaults: { permission_mode: "plan" } } },
    ]);
  });

  it("lists the provider's models under the default model: a pick commits it, and a model it does not list still does", async () => {
    const server = serve(view(resolved()));
    renderPage("?harness=claude");
    const p = await pane("claude");
    await within(p).findByText("models: sonnet, opus, haiku");
    const model = within(p).getByRole("combobox", { name: "default model" });
    await userEvent.clear(model);
    expect(within(screen.getByRole("listbox", { name: "Known models" })).getAllByRole("option").map((o) => o.textContent)).toEqual(["sonnet", "opus", "haiku"]);
    await userEvent.type(model, "hai");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(server.ops).toHaveLength(1));
    await userEvent.clear(model);
    await userEvent.type(model, "claude-next{Enter}");
    await waitFor(() => expect(server.ops).toHaveLength(2));
    expect(server.ops.flat()).toEqual([
      { op: "set_harness", id: "claude", patch: { defaults: { model: "haiku" } } },
      { op: "set_harness", id: "claude", patch: { defaults: { model: "claude-next" } } },
    ]);
  });

  it("clearing a default sends null, and not set is its own choice", async () => {
    const server = serve(view(resolved()));
    renderPage("?harness=claude");
    const p = await pane("claude");
    const model = within(p).getByRole("combobox", { name: "default model" });
    await userEvent.clear(model);
    await userEvent.type(model, "{Enter}");
    await userEvent.click(within(p).getByRole("radiogroup", { name: "Permission mode" }).querySelector('[role="radio"]') as HTMLElement);
    await waitFor(() => expect(server.ops).toHaveLength(2));
    expect(server.ops.flat()).toEqual([
      { op: "set_harness", id: "claude", patch: { defaults: { model: null } } },
      { op: "set_harness", id: "claude", patch: { defaults: { permission_mode: null } } },
    ]);
  });

  it("a refusal is shown in the pane, and a changed harness turns its fields amber", async () => {
    serve(view(resolved(), { changes: [{ path: "harnesses.claude", kind: "change", summary: "enabled" }] }), {
      opsAnswer: () => new Response(JSON.stringify({ detail: "executable is a command name or path, or null" }), { status: 422 }),
    });
    renderPage("?harness=claude");
    const p = await pane("claude");
    expect(within(p).getByRole("textbox", { name: "executable" })).toHaveClass("is-changed");
    await userEvent.click(within(p).getByRole("switch", { name: "Disable claude" }));
    expect(await within(p).findByRole("alert")).toHaveTextContent("executable is a command name");
  });

  it("the Access control sends set_access once per change with the state's own name", async () => {
    const server = serve(view(resolved()));
    renderPage("?harness=claude");
    const p = await pane("claude");
    await userEvent.click(within(p).getByRole("radio", { name: "Never" }));
    await userEvent.click(within(p).getByRole("radio", { name: "Override" }));
    await waitFor(() => expect(server.ops).toHaveLength(2));
    expect(server.ops).toEqual([[{ op: "set_access", harness: "claude", state: "never" }], [{ op: "set_access", harness: "claude", state: "override" }]]);
  });

  it("shows the access help for the current state and a note when escalation runs here", async () => {
    serve(view(resolved({ access: { cursor: "override" } })));
    renderPage("?harness=cursor");
    const p = await pane("cursor");
    expect(within(p).getByText(/an item or a retry override may name it/)).toBeInTheDocument();
  });

  it("amber help text while the access is changed in the draft", async () => {
    serve(view(resolved(), { changes: [{ path: "access.claude", kind: "change", summary: "x" }] }));
    renderPage("?harness=claude");
    const p = await pane("claude");
    expect(within(p).getByText(/Tasks may select it/)).toHaveClass("is-changed");
  });
});

describe("a profile's lane under a harness", () => {
  it("edits the entry: model on Enter, effort limited to the provider's, one set_profile each", async () => {
    const server = serve(view(resolved()));
    renderPage("?harness=claude&lane=strong");
    const p = await pane("strong");
    const model = within(p).getByRole("combobox", { name: "model" });
    await userEvent.clear(model);
    await userEvent.type(model, "opus{Enter}");
    await userEvent.click(within(p).getByRole("radio", { name: "max" }));
    await waitFor(() => expect(server.ops).toHaveLength(2));
    expect(server.ops[0]).toEqual([{ op: "set_profile", name: "strong", patch: { providers: { claude: { model: "opus", effort: "high" } } } }]);
    expect(server.ops[1]).toEqual([{ op: "set_profile", name: "strong", patch: { providers: { claude: { model: "sonnet", effort: "max" } } } }]);
    expect(within(p).getAllByRole("radio").map((r) => r.textContent)).toEqual(["default", "low", "medium", "high", "xhigh", "max"]);
  });

  it("hides effort for a provider with none, and says so", async () => {
    serve(view(resolved({ profiles: { x: { providers: { cursor: { model: "auto" } }, effort: null, tasks: [], used_by_fallback: [] } } })));
    renderPage("?profile=x&lane=cursor");
    const p = await pane("cursor");
    expect(within(p).queryByRole("radiogroup")).toBeNull();
    expect(within(p).getByText("cursor has no effort levels.")).toBeInTheDocument();
  });

  it("a missing entry offers Add, which asks for the model first and sends the entry", async () => {
    const server = serve(view(resolved(), { problems: [NO_ENTRY] }));
    renderPage("?harness=codex&lane=fast");
    const p = await pane("fast");
    expect(within(p).getByText("fast has no codex entry.")).toBeInTheDocument();
    const add = within(p).getByRole("button", { name: "Add codex entry" });
    expect(add).toBeDisabled();
    await userEvent.type(within(p).getByRole("textbox", { name: "codex model" }), "gpt-5{Enter}");
    await userEvent.click(add);
    await waitFor(() => expect(server.ops).toHaveLength(1));
    expect(server.ops[0]).toEqual([{ op: "set_profile", name: "fast", patch: { providers: { codex: { model: "gpt-5" } } } }]);
  });

  it("says the entry is shared, and opens the profile", async () => {
    serve(view(resolved()));
    renderPage("?harness=claude&lane=strong");
    const p = await pane("strong");
    expect(within(p).getByText(/Shared by every claude harness/)).toBeInTheDocument();
    await userEvent.click(within(p).getByRole("button", { name: "Open profile strong →" }));
    expect(await screen.findByRole("complementary", { name: "strong pane" })).toHaveTextContent("agent profile");
  });
});

describe("profile and entry panes", () => {
  it("lists what uses the profile, with links to Chains", async () => {
    serve(view(resolved()));
    renderPage("?profile=strong");
    const p = await pane("strong");
    expect(within(p).getByRole("link", { name: `default › ${TASKS.implementer.path}` })).toHaveAttribute("href", "/templates/chains/default/nodes/implement");
    expect(p).toHaveTextContent("agent profile · 2 providers");
  });

  it("an entry pane lists the harnesses it runs on with their access words, and removing sends a null patch", async () => {
    const server = serve(view(resolved()));
    renderPage("?profile=strong&lane=claude");
    const p = await pane("claude");
    expect(within(p).getByRole("button", { name: "Available →" })).toBeInTheDocument();
    expect(within(p).getByRole("button", { name: "Override →" })).toBeInTheDocument();
    await userEvent.click(within(p).getByRole("button", { name: "Remove entry" }));
    await waitFor(() => expect(server.ops).toHaveLength(1));
    expect(server.ops[0]).toEqual([{ op: "set_profile", name: "strong", patch: { providers: { claude: null } } }]);
  });

  it("rename shows the tasks it broke; the refusal of remove is shown inline", async () => {
    const server = serve(view(resolved()), {
      opsAnswer: (ops) => {
        if (ops[0].op === "remove_profile") return new Response(JSON.stringify({ detail: "profile 'strong' is used by default implement.main.implementer" }), { status: 422 });
        if (ops[0].op === "rename_profile") return new Response(JSON.stringify({ ...view(resolved()), ops: [{ op: "rename_profile", result: { broken: [{ chain: "default", path: TASKS.implementer.path }] } }] }), { status: 200 });
        return null;
      },
    });
    renderPage("?profile=strong");
    const p = await pane("strong");
    await userEvent.click(within(p).getByRole("button", { name: "Remove" }));
    expect(await within(p).findByRole("alert")).toHaveTextContent("is used by default implement.main.implementer");
    await userEvent.click(within(p).getByRole("button", { name: "Rename" }));
    const field = within(p).getByRole("textbox", { name: "New name" });
    await userEvent.clear(field);
    await userEvent.type(field, "heavy");
    await userEvent.click(within(p).getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(server.ops.at(-1)).toEqual([{ op: "rename_profile", name: "strong", to: "heavy" }]));
    await act(async () => {});
  });

  it("Copy sends add_profile with copy_from, and + Profile adds an empty one", async () => {
    const server = serve(view(resolved()));
    renderPage("?profile=fast");
    const p = await pane("fast");
    await userEvent.click(within(p).getByRole("button", { name: "Copy" }));
    await userEvent.type(within(p).getByRole("textbox", { name: "Name of the copy" }), "fast2{Enter}");
    await waitFor(() => expect(server.ops).toHaveLength(1));
    expect(server.ops[0]).toEqual([{ op: "add_profile", name: "fast2", copy_from: "fast" }]);
    await userEvent.click(screen.getByRole("button", { name: "+ Profile" }));
    await waitFor(() => expect(server.ops).toHaveLength(2));
    expect(server.ops[1]).toEqual([{ op: "add_profile", name: "profile" }]);
  });
});

describe("the inspector's choices", () => {
  // jsdom lays nothing out: this reads the rule. A permission mode's seven options on one
  // line ran the claude inspector 127px past its 379px pane (R7b-14).
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "harnesses.css"), "utf-8");
  it("wrap a field's segmented choice inside the pane", () => {
    const at = css.indexOf("\n.hn-field .segmented {");
    expect(at).toBeGreaterThan(-1);
    const body = css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
    expect(body).toMatch(/flex-wrap:\s*wrap/);
    expect(body).toMatch(/max-width:\s*100%/);
  });
});
