import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Library, LibraryComponent } from "../../types";
import { renderAt, setupSettingsMocks } from "./testing";
import { setPhoneWidth } from "../../testFixtures";

const component = (id: string, extra: Partial<LibraryComponent> = {}): LibraryComponent => {
  const [kind, name] = id.split(".");
  return { id, kind, name, definition: { kind: "agent" }, used_by: [], issues: [], ...extra };
};

const TEXT = "# the library\ntasks:\n  implementer: {kind: agent}\n";
const LIBRARY: Library = {
  file: "/home/templates/library.yaml",
  text: TEXT,
  components: [
    component("steering.house", { definition: { instructions: "Be brief." } }),
    component("tasks.implementer", {
      definition: { kind: "agent", harness: "claude", prompt: "Implement the approved plan." },
      used_by: ["default", "quick-task"],
    }),
    component("tasks.lonely", {
      issues: [{ file: "/c/lonely.yaml", chain: "lonely", message: "selects skill 'kraft:nope'" }],
    }),
    component("nodes.verification", { definition: { kind: "exec" }, used_by: ["default"] }),
  ],
};

beforeEach(() => {
  setupSettingsMocks();
  vi.spyOn(api, "getLibrary").mockResolvedValue(LIBRARY);
});

const yaml = async () => (await screen.findByLabelText("library yaml")) as HTMLTextAreaElement;

describe("Settings · library", () => {
  it("lists the components grouped by kind, in the library's order", async () => {
    renderAt("/settings/library");
    const list = (await screen.findByRole("button", { name: /implementer/ })).closest(".template-list") as HTMLElement;
    expect(within(list).getAllByText(/^(steering|tasks|nodes)$/).map((l) => l.textContent)).toEqual([
      "steering",
      "tasks",
      "nodes",
    ]);
    expect(within(list).getByRole("button", { name: /implementer/ })).toHaveTextContent("2 chains");
    expect(within(list).getByRole("button", { name: /lonely/ })).toHaveTextContent("unused · 1 issue");
  });

  it("a selected component shows its definition and the chains using it as links", async () => {
    renderAt("/settings/library?c=tasks.implementer");
    expect(await screen.findByRole("heading", { name: "tasks.implementer" })).toBeInTheDocument();
    expect(screen.getByText(/Implement the approved plan\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "quick-task" })).toHaveAttribute("href", "/settings/chains?tpl=quick-task");
    expect(screen.getByRole("link", { name: "default" })).toHaveAttribute("href", "/settings/chains?tpl=default");
    expect(screen.getByRole("link", { name: "claude" })).toHaveAttribute("href", "/settings/harnesses?h=claude");
  });

  it("a component a lint issue names shows the issue", async () => {
    renderAt("/settings/library?c=tasks.lonely");
    expect(await screen.findByText(/lonely: selects skill 'kraft:nope'/)).toBeInTheDocument();
    expect(screen.getByText("used by no chain")).toBeInTheDocument();
  });

  it("clicking a component selects it", async () => {
    renderAt("/settings/library");
    await userEvent.click(await screen.findByRole("button", { name: /verification/ }));
    expect(await screen.findByRole("heading", { name: "nodes.verification" })).toBeInTheDocument();
  });

  it("edits library.yaml as its author wrote it and saves the text", async () => {
    const put = vi.spyOn(api, "putLibrary").mockResolvedValue({ ...LIBRARY, text: `${TEXT}# more` });
    renderAt("/settings/library");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(TEXT));
    expect(screen.getByText("/home/templates/library.yaml")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(box, "# more");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(`${TEXT}# more`);
    expect(await screen.findByText("saved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("a save the server refuses names why and keeps the draft", async () => {
    vi.spyOn(api, "putLibrary").mockRejectedValue(new Error("quick-task: extends no task named 'implementer'"));
    renderAt("/settings/library");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(TEXT));
    await userEvent.type(box, "x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/extends no task named 'implementer'/)).toBeInTheDocument();
    expect(box.value).toBe(`${TEXT}x`);
  });

  it("Revert drops the draft", async () => {
    renderAt("/settings/library");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(TEXT));
    await userEvent.type(box, "x");
    await userEvent.click(screen.getByRole("button", { name: "Revert" }));
    expect(box.value).toBe(TEXT);
  });

  it("a library that did not load says why", async () => {
    vi.spyOn(api, "getLibrary").mockRejectedValue(new Error("template library invalid, refusing work: library.yaml"));
    renderAt("/settings/library");
    expect(await screen.findByText(/template library invalid/)).toBeInTheDocument();
  });

  it("phone: the header goes back to Settings", async () => {
    setPhoneWidth(true);
    renderAt("/settings/library");
    expect(await screen.findByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });
  it("a steering profile shows its instructions as text and says tasks and repos select it", async () => {
    renderAt("/settings/library?c=steering.house");
    const text = await screen.findByText("Be brief.");
    expect(text.textContent).toBe("Be brief.");
    expect(screen.queryByText(/"instructions"/)).not.toBeInTheDocument();
    expect(screen.getByText(/frozen into an item at intake/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Repos" })).toHaveAttribute("href", "/settings/repos");
  });

  it("the old Steering page address lands on the Library, where steering is edited now", async () => {
    renderAt("/settings/steering");
    expect(await screen.findByRole("textbox", { name: "library yaml" })).toBeInTheDocument();
  });
});
