import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { ChainNode, TemplateSummary } from "../../types";
import { renderAt, setupSettingsMocks } from "./testing";
import { setPhoneWidth } from "../../testFixtures";

const exec = (id: string, tasks: string[], extra: Partial<ChainNode> = {}): ChainNode => ({
  id,
  kind: "exec",
  tasks,
  gate_after: null,
  ...extra,
});
const gate = (id: string): ChainNode => ({ id, kind: "gate", tasks: [], gate_after: id });

const DEFAULT: TemplateSummary = {
  id: "default",
  gates: 1,
  error: null,
  nodes: [
    exec("spec", ["spec.main.author"]),
    gate("spec_approval"),
    exec("verification", ["verification.tests.run"], { fix_loop: "verification.fix_loop" }),
  ],
};
const QUICK: TemplateSummary = { id: "quick-task", gates: 0, error: null, nodes: [exec("implement", ["i.main.t"])] };
const SAVED = "# the default chain\nid: default\nnodes: []\n";
const file = (id: string, text: string) => ({ id, file: `/home/templates/chains/${id}.yaml`, text, chain: {} });

beforeEach(() => {
  setupSettingsMocks();
  vi.spyOn(api, "getTemplates").mockResolvedValue([DEFAULT, QUICK]);
  vi.spyOn(api, "getTemplate").mockImplementation(async (id) => file(id, id === "default" ? SAVED : "id: quick-task\n"));
  vi.spyOn(api, "parseTemplateYaml").mockResolvedValue({ chain: { id: "default" }, error: null });
  vi.spyOn(api, "resolveTemplate").mockResolvedValue({ chains: [{ id: "default", nodes: [] }], issues: [] });
});

const yaml = async () => (await screen.findByLabelText("chain yaml")) as HTMLTextAreaElement;
const pastDebounce = () => new Promise((r) => setTimeout(r, 450));

describe("Settings · chains (Template Schema V1)", () => {
  it("renders the saved chain's resolved nodes as pills, a flag on each gate", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByText("spec_approval")).toBeInTheDocument();
    expect(screen.getByTestId("chain-flag-spec_approval")).toBeInTheDocument();
    expect(screen.queryByTestId("chain-flag-spec")).toBeNull();
  });

  it("links each node to the library components it is built from", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { ...DEFAULT, uses: { verification: ["nodes.verification", "tasks.code_review"] } },
    ]);
    renderAt("/settings/chains");
    const link = await screen.findByRole("link", { name: "tasks.code_review" });
    expect(link).toHaveAttribute("href", "/settings/library?c=tasks.code_review");
    expect(link.closest("[data-node]")).toHaveAttribute("data-node", "verification");
    expect(screen.getByRole("link", { name: "nodes.verification" })).toBeInTheDocument();
  });

  it("heads the page with the chain's name, its counts, the template menu, Revert and Save", async () => {
    renderAt("/settings/chains");
    const head = (await screen.findByRole("heading", { name: "default" })).closest(".chain-head") as HTMLElement;
    expect(head).toHaveTextContent("3 nodes · 1 gate");
    for (const name of ["template", "Revert", "Save"]) {
      expect(within(head).getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("edits the chain file as its author wrote it, comments included", async () => {
    renderAt("/settings/chains");
    await waitFor(async () => expect((await yaml()).value).toBe(SAVED));
    expect(screen.getByText("/home/templates/chains/default.yaml")).toBeInTheDocument();
  });

  it("a draft is resolved against the library, and Save writes the text once it resolves", async () => {
    const put = vi.spyOn(api, "putTemplate").mockResolvedValue({ id: "default", file: "f", text: "t" });
    renderAt("/settings/chains");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(SAVED));
    await userEvent.type(box, "# more");
    await pastDebounce();
    await waitFor(() => expect(api.resolveTemplate).toHaveBeenCalledWith({ id: "default" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("default", `${SAVED}# more`);
  });

  it("a draft that does not resolve names why and cannot be saved", async () => {
    vi.spyOn(api, "resolveTemplate").mockResolvedValue({
      chains: [],
      issues: [{ file: "<unsaved>", chain: "default", message: "nodes[0] extends 'nope', which is not declared" }],
    });
    renderAt("/settings/chains");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(SAVED));
    await userEvent.type(box, "x");
    await pastDebounce();
    expect(await screen.findByText(/extends 'nope'/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("a draft that is not YAML says so from the parse, never reaching the resolver", async () => {
    vi.spyOn(api, "parseTemplateYaml").mockResolvedValue({ chain: null, error: "mapping values are not allowed here" });
    renderAt("/settings/chains");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(SAVED));
    await userEvent.type(box, ": :");
    await pastDebounce();
    expect(await screen.findByText(/mapping values/)).toBeInTheDocument();
    expect(api.resolveTemplate).not.toHaveBeenCalled();
  });

  it("a saved chain that does not resolve shows its error", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ ...DEFAULT, nodes: [], error: "no such node 'x'" }]);
    renderAt("/settings/chains");
    expect(await screen.findByText("no such node 'x'")).toBeInTheDocument();
  });

  it("the diff tab shows the draft against the saved file", async () => {
    renderAt("/settings/chains");
    const box = await yaml();
    await waitFor(() => expect(box.value).toBe(SAVED));
    await userEvent.type(box, "# added");
    await userEvent.click(screen.getByRole("tab", { name: "diff vs saved" }));
    expect(await screen.findByText(/# added/)).toBeInTheDocument();
  });

  it("template ▾ lists every chain, then Duplicate and Delete, and switches chain", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "template" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["default", "quick-task", "Duplicate", "Delete"]);
    await userEvent.click(screen.getByRole("menuitem", { name: "quick-task" }));
    expect(await screen.findByRole("heading", { name: "quick-task" })).toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
  });

  it("Delete is there but disabled, naming the route it waits on", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "template" }));
    const del = screen.getByRole("menuitem", { name: "Delete" });
    expect(del).toHaveAttribute("aria-disabled", "true");
    expect(del.getAttribute("title")).toMatch(/Kraft-lwtco/);
  });

  it("Duplicate writes the saved file under the new name, its id rewritten", async () => {
    const put = vi.spyOn(api, "putTemplate").mockResolvedValue({ id: "default-2", file: "f", text: "t" });
    vi.spyOn(window, "prompt").mockReturnValue("default-2");
    renderAt("/settings/chains");
    await waitFor(async () => expect((await yaml()).value).toBe(SAVED));
    await userEvent.click(screen.getByRole("button", { name: "template" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(put).toHaveBeenCalledWith("default-2", "# the default chain\nid: default-2\nnodes: []\n");
  });

  it("Duplicate refuses a name that already exists, without calling the API", async () => {
    const put = vi.spyOn(api, "putTemplate");
    vi.spyOn(window, "prompt").mockReturnValue("quick-task");
    renderAt("/settings/chains");
    await waitFor(async () => expect((await yaml()).value).toBe(SAVED));
    await userEvent.click(screen.getByRole("button", { name: "template" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(put).not.toHaveBeenCalled();
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });
});

describe("Settings · chains phone (W11 · D.6)", () => {
  beforeEach(() => setPhoneWidth(true));

  it("phone: a full-width chain select, the pill strip and the file, no dropdown menu", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByLabelText("template")).toHaveValue("default");
    expect(screen.getByText("spec")).toBeInTheDocument();
    expect(await yaml()).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "template" })).toBeNull();
  });

  it("phone: the select switches chain", async () => {
    renderAt("/settings/chains");
    await userEvent.selectOptions(await screen.findByLabelText("template"), "quick-task");
    expect(await screen.findByText("implement")).toBeInTheDocument();
  });

  it("phone: the header goes back to Settings", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });
});

describe("Settings · route renames", () => {
  it("redirects the old /settings/templates path to /settings/chains", async () => {
    renderAt("/settings/templates");
    expect(await screen.findByText("spec")).toBeInTheDocument();
  });

  it("redirects the retired /settings/plugins page to /settings/chains", async () => {
    renderAt("/settings/plugins");
    expect(await screen.findByText("spec")).toBeInTheDocument();
  });
});
