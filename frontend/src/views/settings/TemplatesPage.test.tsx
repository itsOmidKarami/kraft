import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { TemplateNode } from "../../types";
import { reparseSerializedNodes, serializeFragment, serializeNodes } from "./TemplatesPage";
import { renderAt, setupSettingsMocks } from "./testing";

function setPhoneWidth(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

const DEFAULT_NODES: TemplateNode[] = [
  { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" },
  { id: "human_review", tasks: ["on.human_review.requested"], gate_after: "human_review_approval", reject_to: "verify" },
];

const QUICK = { id: "quick-task", gates: 0, nodes: [{ id: "implement", tasks: ["on.implementation.start"], gate_after: null }] };

beforeEach(() => {
  setupSettingsMocks();
  vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", gates: 3, nodes: DEFAULT_NODES }]);
});

describe("serializeNodes (task 8b)", () => {
  it("serializes a node list to match write_yaml's own formatting for quick-task.yaml", () => {
    const nodes: TemplateNode[] = [
      { id: "env_setup", tasks: ["on.env.prepare"], gate_after: null },
      { id: "implementation", tasks: ["on.implementation.start"], gate_after: null },
      { id: "verify", tasks: ["on.test.run"], gate_after: null },
    ];
    expect(serializeNodes("quick-task", nodes)).toBe(
      "id: quick-task\n" +
        "nodes:\n" +
        "  - id: env_setup\n" +
        "    tasks: [on.env.prepare]\n" +
        "    gate_after: null\n" +
        "  - id: implementation\n" +
        "    tasks: [on.implementation.start]\n" +
        "    gate_after: null\n" +
        "  - id: verify\n" +
        "    tasks: [on.test.run]\n" +
        "    gate_after: null\n",
    );
  });

  it("a node's fragment is its own block of the file, without the id and nodes lines (W11 · D.3)", () => {
    expect(serializeFragment(DEFAULT_NODES[2])).toBe(
      "  - id: verify\n    tasks: [on.test.run]\n    gate_after: null\n    fix_loop: verify_fix_loop\n",
    );
  });
});

describe("serializeNodes round trip (task 8c)", () => {
  it("round-trips every node key of a node list through the serializer, including keys the form doesn't render", () => {
    const nodes: TemplateNode[] = [
      { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
      { id: "pre_mr_rebase", tasks: ["on.mr.rebase"], gate_after: null, rebase_bounce_to: "verify" },
      { id: "mr_checks", tasks: ["on.ci.poll", "on.review.mr.run"], gate_after: null, on_failure: ["on.mr_checks.repair"] },
      { id: "human_review", tasks: ["on.human_review.requested"], gate_after: "human_review_approval", reject_to: "implementation" },
    ];
    expect(reparseSerializedNodes(serializeNodes("default", nodes))).toEqual(nodes);
  });
});

describe("Settings · chains editor (W11 · D)", () => {
  it("renders a pill per node with its task count and a gate flag after a gated node", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByText("spec")).toBeInTheDocument();
    expect(screen.getByTestId("chain-flag-spec")).toBeInTheDocument();
  });

  it("heads the page with the template's name, its counts, the template dropdown, YAML, Revert and Save", async () => {
    renderAt("/settings/chains");
    const head = (await screen.findByRole("heading", { name: "default" })).closest(".chain-head") as HTMLElement;
    expect(head).toHaveTextContent("4 nodes · 3 gates");
    for (const name of ["template", "YAML", "Revert", "Save"]) {
      expect(within(head).getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("renders no templates column", async () => {
    const { container } = renderAt("/settings/chains");
    await screen.findByText("spec");
    expect(container.querySelector(".template-list, .templates-list")).toBeNull();
  });

  it("with nothing selected, the card shows what the template is", async () => {
    renderAt("/settings/chains");
    const summary = await screen.findByTestId("chain-summary");
    expect(summary).toHaveTextContent("4 nodes · 3 gates · used by 0 items · ~/.kraft/templates/default.yaml");
    expect(screen.queryByLabelText("fix_loop")).toBeNull();
  });

  it("selecting a pill opens the card on that node: its form, and its YAML beside it", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    const card = screen.getByTestId("chain-card");
    expect(within(card).getByText(/verify · node 3 of 4/)).toBeInTheDocument();
    expect(within(card).getByLabelText("fix_loop")).toHaveValue("verify_fix_loop");
    expect((within(card).getByLabelText("node yaml") as HTMLTextAreaElement).value).toBe(serializeFragment(DEFAULT_NODES[2]));
  });

  it("gate_after, fix_loop and reject_to use the settings select Appearance uses; text inputs are the short kind (W12.3)", async () => {
    renderAt("/settings/chains?tpl=default&node=human_review");
    await screen.findByLabelText("gate_after");
    for (const label of ["gate_after", "fix_loop", "reject_to"]) {
      const el = screen.getByLabelText(label);
      expect(el.tagName).toBe("SELECT");
      expect(el).toHaveClass("input");
    }
    expect(screen.getByLabelText("id")).toHaveClass("input");
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "templates.css"), "utf-8");
    expect(css).toMatch(/\.chain-node-form input\.input\s*\{\s*max-width:\s*320px;/);
  });

  it("editing a form field updates the node's YAML and the whole file's", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.selectOptions(screen.getByLabelText("gate_after"), "human_review_approval");
    expect((screen.getByLabelText("node yaml") as HTMLTextAreaElement).value).toContain("gate_after: human_review_approval");
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect((screen.getByLabelText("chain yaml") as HTMLTextAreaElement).value).toContain("gate_after: human_review_approval");
  });

  it("editing the node's YAML replaces the node once it parses (edits either side)", async () => {
    const parse = vi.spyOn(api, "parseTemplateYaml").mockResolvedValue({
      nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: "human_review_approval", fix_loop: "verify_fix_loop" }],
      error: null,
    });
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.type(screen.getByLabelText("node yaml"), " ");
    await waitFor(() => expect(parse).toHaveBeenCalled());
    expect(parse.mock.calls.at(-1)![0]).toMatch(/^id: default\nnodes:\n {2}- id: verify/);
    await waitFor(() => expect(screen.getByLabelText("gate_after")).toHaveValue("human_review_approval"));
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("a node YAML that does not parse to one node says so, and leaves the form alone", async () => {
    vi.spyOn(api, "parseTemplateYaml").mockResolvedValue({ nodes: null, error: "bad indent" });
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.type(screen.getByLabelText("node yaml"), "  broken");
    expect(await screen.findByText(/bad indent/)).toBeInTheDocument();
    expect(screen.getByLabelText("fix_loop")).toHaveValue("verify_fix_loop");
  });

  it("toggling auto_escalate_stuck and setting a delay updates the YAML pane", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.click(screen.getByLabelText("auto_escalate_stuck"));
    const delay = screen.getByLabelText("auto_escalate_delay_s");
    await userEvent.clear(delay);
    await userEvent.type(delay, "30");
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    const yaml = screen.getByLabelText("chain yaml") as HTMLTextAreaElement;
    expect(yaml.value).toContain("auto_escalate_stuck: false");
    expect(yaml.value).toContain("auto_escalate_delay_s: 30");
  });

  it("a full-file YAML parse error shows inline and leaves the form untouched", async () => {
    vi.spyOn(api, "parseTemplateYaml").mockResolvedValue({ nodes: null, error: "bad indent" });
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    await userEvent.type(screen.getByLabelText("chain yaml"), "  broken");
    expect(await screen.findByText(/bad indent/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.getByLabelText("fix_loop")).toHaveValue("verify_fix_loop"); // unchanged
  });

  it("the YAML toggle swaps the card for the whole file, and keeps the pill strip (D.4)", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    const toggle = screen.getByRole("button", { name: "YAML" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("chain-card")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("chain yaml")).toBeInTheDocument();
    expect(screen.queryByTestId("chain-card")).toBeNull();
    expect(screen.getByText("spec")).toBeInTheDocument();
  });

  it("Add node inserts a node and selects it, back in the card", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    await userEvent.click((await screen.findAllByRole("button", { name: /add node/i }))[0]);
    expect(await screen.findByText(/node 1 of 5/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^node_5/ })).toHaveAttribute("data-selected", "true");
  });

  it("duplicate and remove in the card's footer act on the selected node", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.click(screen.getByRole("button", { name: "duplicate node" }));
    expect(screen.getByRole("button", { name: /^verify_copy/ })).toHaveAttribute("data-selected", "true");
    await userEvent.click(screen.getByRole("button", { name: "remove node" }));
    expect(screen.queryByRole("button", { name: /^verify_copy/ })).toBeNull();
  });

  it("Kraft-xhro: names the node and task an unresolved hook belongs to, not just a repo bit", async () => {
    vi.spyOn(api, "validateTemplate").mockResolvedValue({
      id: "default",
      valid: false,
      error: "unresolved hooks",
      unresolved: [{ node: "verify", task: "on.made.up" }],
    });
    renderAt("/settings/chains");
    expect(await screen.findByText(/verify: on\.made\.up has no plugin bound/)).toBeInTheDocument();
  });

  it("Kraft-xhro: + Add task opens an inline row instead of window.prompt", async () => {
    const prompt = vi.spyOn(window, "prompt");
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("spec"));
    await userEvent.click(await screen.findByRole("button", { name: "+ Add task" }));
    expect(prompt).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText("hook to add"), "on.env.prepare");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(within(screen.getByTestId("chain-card")).getAllByText("on.env.prepare").length).toBeGreaterThan(0);
  });

  it("insert, remove, and reorder nodes mark the template dirty", async () => {
    renderAt("/settings/chains");
    await userEvent.click((await screen.findAllByRole("button", { name: /add node/i }))[0]);
    expect(await screen.findByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("Save is disabled while the debounced validator reports invalid", async () => {
    vi.spyOn(api, "validateTemplate").mockResolvedValue({ id: "default", valid: false, error: "nope", unresolved: [] });
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.click((await screen.findAllByRole("button", { name: /add node/i }))[0]);
    await new Promise((r) => setTimeout(r, 450)); // past the 400ms validation debounce
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("template ▾ lists every template, then New…, Duplicate and Delete, and switches template (D.1)", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", gates: 3, nodes: DEFAULT_NODES }, QUICK]);
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "template" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["default", "quick-task", "New…", "Duplicate", "Delete"]);
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

  it("New… writes a one-node template under the new name", async () => {
    const put = vi.spyOn(api, "putTemplate").mockResolvedValue({ id: "fresh", nodes: [] });
    vi.spyOn(window, "prompt").mockReturnValue("fresh");
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "template" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "New…" }));
    expect(put).toHaveBeenCalledWith("fresh", [{ id: "node_1", tasks: [], gate_after: null }]);
  });

  it("Duplicate copies the current template under the new name", async () => {
    const put = vi.spyOn(api, "putTemplate").mockResolvedValue({ id: "default-2", nodes: [] });
    vi.spyOn(window, "prompt").mockReturnValue("default-2");
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "template" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(put).toHaveBeenCalledWith("default-2", DEFAULT_NODES);
  });

  it("New… and Duplicate refuse a name that already exists, without calling the API", async () => {
    const put = vi.spyOn(api, "putTemplate");
    vi.spyOn(window, "prompt").mockReturnValue("default");
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "template" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(put).not.toHaveBeenCalled();
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });

  it("shows the legend row for the node graph's glyphs", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByText(/gate after/)).toBeInTheDocument();
    expect(screen.getByText(/fix loop/)).toBeInTheDocument();
    expect(screen.getByText(/auto-escalate/)).toBeInTheDocument();
  });
});

describe("Settings · chains phone (W11 · D.6)", () => {
  beforeEach(() => setPhoneWidth(true));
  afterEach(() => vi.unstubAllGlobals());

  it("phone: one page -- a full-width template select, the pill strip and the card, no dropdown menu", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByLabelText("template")).toHaveValue("default");
    expect(screen.getByText("spec")).toBeInTheDocument();
    expect(screen.getByTestId("chain-summary")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "template" })).toBeNull();
  });

  it("phone: the select switches template", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", gates: 3, nodes: DEFAULT_NODES }, QUICK]);
    renderAt("/settings/chains");
    await userEvent.selectOptions(await screen.findByLabelText("template"), "quick-task");
    expect(await screen.findByText("implement")).toBeInTheDocument();
  });

  it("phone: a picked node shows its form and its YAML, editable", async () => {
    renderAt("/settings/chains?tpl=default&node=verify");
    expect(await screen.findByLabelText("fix_loop")).toBeInTheDocument();
    expect(screen.getByLabelText("node yaml")).not.toHaveAttribute("readonly");
  });

  it("phone: the header goes back to Settings", async () => {
    renderAt("/settings/chains?tpl=default&node=verify");
    expect(await screen.findByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });
});

describe("Settings · route rename (UI v2 · 01)", () => {
  it("redirects the old /settings/templates path to /settings/chains", async () => {
    renderAt("/settings/templates");
    expect(await screen.findByText("spec")).toBeInTheDocument();
  });
});

describe("Settings · chains editor · task ordering (Kraft-7ifcj)", () => {
  it("does not tell the operator a node's tasks run in order", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/hook points, in order/i);
    expect(text).toMatch(/concurrent/i);
  });
});
