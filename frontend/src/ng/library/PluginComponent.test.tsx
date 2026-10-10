import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "../templates/draft/draftApi";
import { LIB_FILE, LIB_MODEL, libView, PUBLISHED } from "./fixture";
import { mount, ok, setup, where } from "./testSupport";

const PLUGIN = { id: "release@acme", version: "1.0.0" };
const SHIPPED = { tasks: { "release:base": { kind: "agent", prompt: "Write the release notes.", skill: "release:notes" } } };
const LISTED = { ...PUBLISHED, components: [...PUBLISHED.components, { id: "tasks.release:base", kind: "tasks", name: "release:base", used_by: [], used_by_paths: [], plugin: PLUGIN }] };
const REF = "/templates/library/tasks.release%3Abase";

let answer: { status: number; body: unknown };
const posts = () => vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST").map(([url, init]) => [url, JSON.parse(String(init!.body))]);

beforeEach(() => {
  setup();
  vi.mocked(d.getDraft).mockImplementation(() => ok({ ...libView(), plugin_library: SHIPPED }));
  // The published list names the plugin; a POST is the copy.
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => (init?.method === "POST" ? new Response(JSON.stringify(answer.body), { status: answer.status }) : new Response(JSON.stringify(LISTED), { status: 200 }))));
});
afterEach(() => vi.unstubAllGlobals());

describe("A plugin's component in the Library", () => {
  it("is listed with its badge and read without edit controls, beside a local one that keeps them", async () => {
    mount(REF);
    // `tasks.release%3Abase` in the URL is the component `tasks.release:base`.
    expect(await screen.findByRole("heading", { name: "release:base" })).toBeInTheDocument();
    const list = screen.getByRole("listbox", { name: "Library components" });
    await waitFor(() => expect(within(list).getByRole("option", { name: /release:base/ })).toHaveTextContent("release@acme 1.0.0"));
    expect(within(list).getByRole("option", { name: /^implementer/ })).not.toHaveTextContent("release@acme");

    const pane = screen.getByRole("complementary", { name: "release:base pane" });
    expect(within(pane).getByText("Write the release notes.")).toBeInTheDocument();
    expect(within(pane).queryByRole("textbox")).toBeNull();
    expect(within(pane).queryByRole("button", { name: "release:base" })).toBeNull();
    expect(within(pane).queryByRole("button", { name: /Remove|Duplicate|Icon/ })).toBeNull();
    expect(within(pane).getAllByRole("button", { name: "Copy to my library" })).toHaveLength(1);
    // Its definition, as YAML, without asking the draft for a fragment it does not hold.
    await userEvent.click(within(pane).getByRole("tab", { name: "YAML" }));
    const yaml = within(pane).getByRole("textbox", { name: "tasks.release:base, YAML" });
    expect(yaml).toHaveValue("kind: agent\nprompt: \"Write the release notes.\"\nskill: release:notes");
    expect(yaml).toHaveAttribute("readonly");
    await userEvent.click(within(pane).getByRole("tab", { name: "Config" }));
    expect(within(pane).getByText("release:notes")).toBeInTheDocument();
    expect(within(pane).queryByRole("button", { name: /^(Edit|Reset) / })).toBeNull();

    await userEvent.click(within(list).getByRole("option", { name: /^implementer/ }));
    const local = await screen.findByRole("complementary", { name: "implementer pane" });
    expect(within(local).getByRole("button", { name: "Edit prompt" })).toBeInTheDocument();
    await userEvent.click(within(local).getByRole("tab", { name: "Overview" }));
    expect(within(local).getByRole("textbox", { name: "prompt" })).toBeInTheDocument();
    expect(within(local).getByRole("button", { name: "implementer" })).toBeInTheDocument();
    expect(within(local).getByRole("button", { name: "Remove task" })).toBeInTheDocument();
    expect(within(local).queryByRole("button", { name: "Copy to my library" })).toBeNull();
    expect(posts()).toEqual([]);
  });

  it("⌘Z over a plugin's component undoes nothing: the draft's last request is not on screen", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok({ ...libView({}, true), plugin_library: SHIPPED }));
    const undo = vi.spyOn(d, "undo").mockImplementation(() => ok({ ...libView({}, true), plugin_library: SHIPPED }) as never);
    mount(REF);
    expect(await screen.findByRole("heading", { name: "release:base" })).toBeInTheDocument();
    await userEvent.keyboard("{Meta>}z{/Meta}");
    expect(undo).not.toHaveBeenCalled();

    await userEvent.click(within(screen.getByRole("listbox", { name: "Library components" })).getByRole("option", { name: /^implementer/ }));
    await screen.findByRole("complementary", { name: "implementer pane" });
    await userEvent.keyboard("{Meta>}z{/Meta}");
    await waitFor(() => expect(undo).toHaveBeenCalled());
  });

  it("Copy to my library asks for a name, sends copy_component with the listed ref, and opens the copy", async () => {
    answer = { status: 200, body: { ...libView({ model: { [LIB_FILE]: { ...LIB_MODEL, tasks: { ...LIB_MODEL.tasks, notes: SHIPPED.tasks["release:base"] } } } }, true), plugin_library: SHIPPED, ops: [{ op: "copy_component", result: { path: "tasks.notes" } }] } };
    mount(REF);
    await userEvent.click(await screen.findByRole("button", { name: "Copy to my library" }));
    const name = await screen.findByRole("textbox", { name: "Copy release:base to my library as" });
    expect(name).toHaveValue("base");
    await waitFor(() => expect(name).toHaveFocus());
    await userEvent.clear(name);
    await userEvent.type(name, "implementer");
    expect(screen.getByText("implementer is taken.")).toBeInTheDocument();
    await userEvent.clear(name);
    await userEvent.type(name, "notes{Enter}");
    await waitFor(() => expect(posts()).toEqual([["/api/drafts/library/library/ops", { ops: [{ op: "copy_component", ref: "tasks.release:base", name: "notes" }] }]]));
    await waitFor(() => expect(where()).toBe("/templates/library/tasks.notes"));
    // The copy is the library's own: it has the controls the plugin's did not.
    expect(await screen.findByRole("button", { name: "Remove task" })).toBeInTheDocument();
  });

  it("shows the server's refusal on the name, and stays on the plugin's component", async () => {
    answer = { status: 422, body: { detail: "'tasks.release:base' is not a plugin's component; edit it where it is" } };
    mount(REF);
    await userEvent.click(await screen.findByRole("button", { name: "Copy to my library" }));
    const name = await screen.findByRole("textbox", { name: "Copy release:base to my library as" });
    await waitFor(() => expect(name).toHaveFocus());
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("'tasks.release:base' is not a plugin's component; edit it where it is")).toBeInTheDocument();
    expect(where()).toBe(REF);
  });
});
