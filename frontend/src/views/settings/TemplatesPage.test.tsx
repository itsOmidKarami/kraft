import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { TemplateNode } from "../../types";
import { reparseSerializedNodes, serializeNodes } from "./TemplatesPage";
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

beforeEach(() => {
  setupSettingsMocks();
  vi.spyOn(api, "getTemplates").mockResolvedValue([
    {
      id: "default",
      gates: 4,
      nodes: [
        { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
        { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
        {
          id: "verify",
          tasks: ["on.test.run"],
          gate_after: null,
          fix_loop: "verify_fix_loop",
        },
        {
          id: "human_review",
          tasks: ["on.human_review.requested"],
          gate_after: "human_review_approval",
          reject_to: "verify",
        },
      ],
    },
  ]);
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
});

describe("serializeNodes round trip (task 8c)", () => {
  it("round-trips every node key of a node list through the serializer, including keys the form doesn't render", () => {
    const nodes: TemplateNode[] = [
      { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
      {
        id: "pre_mr_rebase",
        tasks: ["on.mr.rebase"],
        gate_after: null,
        rebase_bounce_to: "verify",
      },
      {
        id: "mr_checks",
        tasks: ["on.ci.poll", "on.review.mr.run"],
        gate_after: null,
        on_failure: ["on.mr_checks.repair"],
      },
      {
        id: "human_review",
        tasks: ["on.human_review.requested"],
        gate_after: "human_review_approval",
        reject_to: "implementation",
      },
    ];
    const text = serializeNodes("default", nodes);
    const reparsed = reparseSerializedNodes(text);
    expect(reparsed).toEqual(nodes);
  });
});

describe("Settings · chains editor (task 8)", () => {
  it("renders a pill per node with its task count and a gate flag after a gated node", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByText("spec")).toBeInTheDocument();
    expect(screen.getByTestId("chain-flag-spec")).toBeInTheDocument();
  });

  it("selecting a pill opens its node form and highlights its YAML block", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    expect(await screen.findByText(/node \d+ of 4/)).toBeInTheDocument();
    expect(screen.getByLabelText("fix_loop")).toHaveValue("verify_fix_loop");
  });

  it("editing a form field updates the YAML pane", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.selectOptions(screen.getByLabelText("gate_after"), "human_review_approval");
    const yaml = screen.getByLabelText("chain yaml") as HTMLTextAreaElement;
    expect(yaml.value).toContain("gate_after: human_review_approval");
  });

  it("a YAML parse error shows inline and leaves the form untouched", async () => {
    vi.spyOn(api, "parseTemplateYaml").mockResolvedValue({ nodes: null, error: "bad indent" });
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.type(screen.getByLabelText("chain yaml"), "  broken");
    expect(await screen.findByText(/bad indent/)).toBeInTheDocument();
    expect(screen.getByLabelText("fix_loop")).toHaveValue("verify_fix_loop"); // unchanged
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
    expect(screen.getByText("on.env.prepare")).toBeInTheDocument();
  });

  it("insert, remove, and reorder nodes mark the template dirty", async () => {
    renderAt("/settings/chains");
    await userEvent.click((await screen.findAllByRole("button", { name: /insert node/i }))[0]);
    expect(await screen.findByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("Save is disabled while the debounced validator reports invalid", async () => {
    vi.spyOn(api, "validateTemplate").mockResolvedValue({
      id: "default",
      valid: false,
      error: "nope",
      unresolved: [],
    });
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("verify"));
    await userEvent.click((await screen.findAllByRole("button", { name: /insert node/i }))[0]);
    await new Promise((r) => setTimeout(r, 450)); // past the 400ms validation debounce
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("+ New creates a template from a copy of the selected one", async () => {
    const put = vi.spyOn(api, "putTemplate").mockResolvedValue({ id: "default-2", nodes: [] });
    vi.spyOn(window, "prompt").mockReturnValue("default-2");
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "New" }));
    expect(put).toHaveBeenCalledWith("default-2", expect.any(Array));
  });

  it("+ New refuses a name that already exists, without calling the API", async () => {
    const put = vi.spyOn(api, "putTemplate");
    vi.spyOn(window, "prompt").mockReturnValue("default");
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByRole("button", { name: "New" }));
    expect(put).not.toHaveBeenCalled();
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });

  it("renders template rows with the name and meta on separate lines", async () => {
    renderAt("/settings/chains");
    const row = await screen.findByRole("button", { name: /default/ });
    expect(row.querySelector(".template-row-name")?.textContent).toBe("default");
    expect(row.querySelector(".template-row-meta")?.textContent).toMatch(/gates · used by/);
  });

  it("shows the legend row for the node graph's glyphs", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByText(/gate after/)).toBeInTheDocument();
    expect(screen.getByText(/fix loop/)).toBeInTheDocument();
    expect(screen.getByText(/auto-escalate/)).toBeInTheDocument();
  });
});

describe("Settings · chains phone (task 9, m13)", () => {
  beforeEach(() => setPhoneWidth(true));
  afterEach(() => vi.unstubAllGlobals());

  it("phone: shows the template list with no template selected", async () => {
    renderAt("/settings/chains");
    expect(await screen.findByText("default")).toBeInTheDocument();
    expect(screen.queryByText(/node \d+ of/)).toBeNull();
  });

  it("phone: opening a template shows its node list, not the node form", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("default"));
    expect(await screen.findByText(/verify/)).toBeInTheDocument();
    expect(screen.queryByLabelText("gate_after")).toBeNull();
  });

  it("phone: the node list is a stage list with a trailing + node row, not desktop pills", async () => {
    renderAt("/settings/chains");
    await userEvent.click(await screen.findByText("default"));
    const row = (await screen.findByText("verify")).closest(".row");
    expect(row?.querySelector(".glyph")).toBeTruthy();
    expect(screen.getByRole("button", { name: "+ node" })).toBeInTheDocument();
  });

  it("phone: opening a node shows the form and a read-only YAML sheet", async () => {
    renderAt("/settings/chains?tpl=default&node=verify");
    expect(await screen.findByLabelText("fix_loop")).toBeInTheDocument();
    expect(screen.getByLabelText("chain yaml")).toHaveAttribute("readonly");
  });

  it("phone: the back link on the node page returns to the node list, not Settings", async () => {
    renderAt("/settings/chains?tpl=default&node=verify");
    await userEvent.click(await screen.findByText("default")); // the PhoneHeader back link
    expect(await screen.findByText(/verify/)).toBeInTheDocument();
    expect(screen.queryByLabelText("fix_loop")).toBeNull();
  });
});

describe("Settings · route rename (UI v2 · 01)", () => {
  it("redirects the old /settings/templates path to /settings/chains", async () => {
    renderAt("/settings/templates");
    expect(await screen.findByText("Templates")).toBeInTheDocument();
  });
});
