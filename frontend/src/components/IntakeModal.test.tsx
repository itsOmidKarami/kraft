import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { IntakeModal } from "./IntakeModal";

const REPO_A = {
  path: "/a",
  name: "repo-a",
  enabled: true,
  default_chain_template: "default",
  test_command: null,
  test_scopes: null,
  forge: null,
  project: null,
  models: {},
  deny_tools: [],
  steering: [],
  local_files: [],
  managed: true,
};

function workspace(
  mounts: Record<string, string>,
  root_pointer_default: "ignore" | "bump" = "ignore",
) {
  return {
    id: "ws",
    root: "a",
    root_pointer_default,
    members: Object.fromEntries(
      Object.entries(mounts).map(([id, path]) => [id, { repository: id, path }]),
    ),
  };
}

function renderModal(onClose: () => void = () => {}) {
  return render(
    <MemoryRouter
    >
      <Routes>
        <Route path="/" element={<IntakeModal onClose={onClose} />} />
        <Route path="/work-items/:id" element={<p>detail for w9</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

// The repo <select> renders before getRepos resolves, so selecting straight
// after findByLabelText races the fetch and flakes on a loaded CI box.
async function selectRepo(repoName = "repo-a") {
  const select = await screen.findByLabelText("repo");
  await screen.findByRole("option", { name: new RegExp(`^${repoName}`) });
  await userEvent.selectOptions(select, repoName);
  return select as HTMLSelectElement;
}

async function fillBasics(repoName = "repo-a", title = "t") {
  await selectRepo(repoName);
  await userEvent.type(screen.getByLabelText("title"), title);
}

// Every test starts from one repo and the default chain; a test that needs
// something else overrides the spy.
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", nodes: [], gates: 0 }]);
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
});

describe("IntakeModal", () => {
  it("says where to get one when there are no repos connected", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    renderModal();
    expect(
      await screen.findByText(/connect one in Settings/i),
    ).toBeInTheDocument();
  });

  it("lays the dialog out in two columns with an overrides column", async () => {
    renderModal();
    const dlg = await screen.findByRole("dialog");
    expect(dlg.querySelector(".intake-grid")).toBeTruthy();
    expect(within(dlg).getByText(/OVERRIDES FOR THIS ITEM/i)).toBeTruthy();
    expect(within(dlg).getByText(/Will happen on start/i)).toBeTruthy();
  });

  it("shows a disabled repo as a disabled option, not omitted", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [
        REPO_A,
        { ...REPO_A, path: "/c", name: "repo-c", enabled: false },
      ],
    });
    renderModal();
    const select = (await screen.findByLabelText("repo")) as HTMLSelectElement;
    await screen.findByRole("option", { name: /repo-c.*disabled/i });
    const opt = within(select).getByText(/repo-c.*disabled/i)
      .closest("option") as HTMLOptionElement;
    expect(opt.disabled).toBe(true);
  });

  it("preselects the chosen repo's default chain template", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      {
        id: "default",
        nodes: [{ id: "spec", tasks: [], gate_after: null }],
        gates: 0,
      },
      {
        id: "quick-task",
        nodes: [{ id: "implementation", tasks: [], gate_after: null }],
        gates: 0,
      },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [{ ...REPO_A, default_chain_template: "quick-task" }],
    });
    renderModal();
    await selectRepo();
    expect(screen.getByRole("radio", { name: /quick-task/i })).toBeChecked();
  });

  it("submits and shows an inline error on failure", async () => {
    vi.spyOn(api, "createWorkItem").mockRejectedValue(
      new Error("repo path does not exist"),
    );
    renderModal();
    await fillBasics();
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    expect(
      await screen.findByText(/repo path does not exist/),
    ).toBeInTheDocument();
  });

  it("closes and navigates on success, omitting chain_template for the default chain", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
      { id: "quick-task", nodes: [], gates: 0 },
    ]);
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    const onClose = vi.fn();
    renderModal(onClose);
    await fillBasics("repo-a", "do a thing");
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "/a",
          title: "do a thing",
          skip_nodes: [],
          auto_gate: true,
          autostart: true,
        }),
      ),
    );
    expect(onClose).toHaveBeenCalled();
    expect(await screen.findByText("detail for w9")).toBeInTheDocument();
  });

  it.each([
    ["budget", /^budget$/, "$20", /budget must be a plain number/],
    ["fix attempts", /^fix attempts$/i, "lots", /fix attempts must be a plain number/i],
    ["wall clock", /wall clock, minutes/i, "soon", /wall clock must be a plain number/i],
  ])("rejects a non-numeric %s instead of sending no cap or NaN", async (_, label, typed, error) => {
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics();
    await userEvent.type(await screen.findByLabelText(label), typed);
    await userEvent.click(screen.getByRole("button", { name: /create and start/i }));
    expect(await screen.findByText(error)).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("submits the description with the new work item", async () => {
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics("repo-a", "short label");
    await userEvent.type(screen.getByLabelText("description"), "the brief");
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "/a",
          title: "short label",
          description: "the brief",
        }),
      ),
    );
  });

  it("sends chain_template when a non-default template is picked", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
      { id: "quick-task", nodes: [], gates: 0 },
    ]);
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics();
    await userEvent.click(
      await screen.findByRole("radio", { name: /quick-task/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "/a",
          title: "t",
          chain_template: "quick-task",
        }),
      ),
    );
  });

  it("clicking a chain-preview node toggles skip and sends it as skip_nodes", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      {
        id: "default",
        gates: 0,
        nodes: [
          { id: "spec", tasks: [], gate_after: null },
          { id: "plan", tasks: [], gate_after: null },
          { id: "verify", tasks: [], gate_after: null },
        ],
      },
    ]);
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics();
    await userEvent.click(await screen.findByRole("button", { name: /^plan/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ skip_nodes: ["plan"], autostart: true }),
      ),
    );
  });

  it("placeholders fix attempts and wall clock with the Policy default, not a raw auto_gate identifier", async () => {
    vi.spyOn(api, "getPolicy").mockResolvedValue({
      loops: {},
      default: { attempts: 4, wall_clock_s: 1800 },
      max_concurrent: 3,
    });
    renderModal();
    await fillBasics();
    expect(await screen.findByLabelText(/^fix attempts$/i)).toHaveAttribute(
      "placeholder",
      "4 (policy default)",
    );
    expect(screen.getByLabelText(/wall clock, minutes/i)).toHaveAttribute(
      "placeholder",
      "30 (policy default)",
    );
    expect(screen.queryByText("auto_gate", { exact: true })).toBeNull();
    expect(screen.getByText(/let an agent review those escalations first/i)).toBeInTheDocument();
  });

  it("sends fix attempts and wall clock overrides for every fix_loop node on submit", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      {
        id: "default",
        nodes: [
          { id: "spec", tasks: [], gate_after: "spec_approval" },
          { id: "implementation", tasks: [], gate_after: null, fix_loop: "verify_fix_loop" },
        ],
        gates: 1,
      },
    ]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w1" });
    renderModal();
    await fillBasics();
    await userEvent.type(await screen.findByLabelText(/^fix attempts$/i), "2");
    await userEvent.type(screen.getByLabelText(/wall clock, minutes/i), "10");
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          node_overrides: expect.objectContaining({
            implementation: expect.objectContaining({
              attempts: 2,
              wall_clock_s: 600,
            }),
          }),
        }),
      ),
    );
  });

  it("puts the $ in the budget placeholder, not the label (never-wrap rule)", async () => {
    renderModal();
    await fillBasics();
    expect(screen.getByText("budget", { exact: true })).toBeInTheDocument();
    expect(screen.getByLabelText("budget")).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/^\$/),
    );
  });

  it("sends auto_gate and node_overrides from the Overrides switches", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      {
        id: "default",
        gates: 1,
        // Only a gate declaring a reviewer (`auto_escalate: true`) can be
        // armed; the server refuses the switch on one that declares none.
        nodes: [
          { id: "plan", tasks: [], gate_after: "plan_approval", auto_escalate: true },
          { id: "hold", tasks: [], gate_after: "hold", auto_escalate: false },
        ],
      },
    ]);
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics();
    await userEvent.click(screen.getByLabelText(/auto-escalate every gate/i));
    await userEvent.click(
      screen.getByRole("button", { name: /create paused/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          auto_gate: true,
          autostart: false,
          node_overrides: { plan: { auto_escalate: true } },
        }),
      ),
    );
  });

  it("Create paused sends autostart: false; Create and start sends true", async () => {
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics();
    await userEvent.click(
      screen.getByRole("button", { name: /create paused/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ autostart: false }),
      ),
    );
  });

  it("offers the cross-repo disclosure only when the repo roots a workspace", async () => {
    // A nested, enabled repo is not a member: membership is declared, never
    // inferred from where a path happens to sit.
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [REPO_A, { ...REPO_A, path: "/a/libs/a", name: "libs-a" }],
      workspaces: {},
    });
    renderModal();
    await selectRepo();
    expect(screen.queryByRole("button", { name: /cross-repo/ })).toBeNull();
  });

  it("does not offer a member whose repository is disabled", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [
        { ...REPO_A, id: "a" },
        { ...REPO_A, id: "lib-a", path: "/a/libs/a", name: "libs-a", enabled: false },
      ],
      workspaces: { ws: workspace({ "lib-a": "libs/a" }) },
    });
    renderModal();
    await selectRepo();
    expect(screen.queryByRole("button", { name: /cross-repo/ })).toBeNull();
  });

  // Both defaults: a fixture of only one lets a hardcoded default of the
  // same value pass (Kraft-3f4kb).
  it.each(["bump", "ignore"] as const)("sends the workspace with its picked members and the workspace's root pointer policy by default: %s", async (pointer) => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [
        { ...REPO_A, id: "a" },
        { ...REPO_A, id: "lib-a", path: "/a/libs/a", name: "libs-a" },
        { ...REPO_A, id: "lib-b", path: "/a/libs/b", name: "libs-b" },
      ],
      workspaces: {
        ws: workspace({ "lib-a": "libs/a", "lib-b": "libs/b" }, pointer),
      },
    });
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics("repo-a", "bump pointers");

    // collapsed by default
    const disclosure = await screen.findByRole("button", {
      name: /cross-repo/,
    });
    expect(screen.queryByRole("button", { name: "libs/a" })).toBeNull();
    await userEvent.click(disclosure);

    await userEvent.click(screen.getByRole("button", { name: "libs/a" }));
    // Ruling 165: the default is the workspace's `root_pointer_default`, not
    // a hardcoded "bump".
    const other = pointer === "bump" ? "ignore" : "bump";
    expect(screen.getByRole("radio", { name: new RegExp(pointer, "i") })).toBeChecked();
    expect(screen.getByRole("radio", { name: new RegExp(other, "i") })).not.toBeChecked();
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "/a",
          title: "bump pointers",
          workspace: "ws",
          members: ["lib-a"],
          root_pointer_policy: pointer,
        }),
      ),
    );
  });

  it("attaches a spec through one search field that becomes a chip", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      query: "submodule",
      mode: "hybrid",
      results: [
        {
          id: "d1",
          repo: "/a",
          kind: "specs",
          source_kind: "artifact",
          title: "submodule-pointers",
          path: ".engineering/specs/submodule-pointers.md",
          snippet: "",
          score: 1,
          links: [],
        },
      ],
    });
    renderModal();
    await fillBasics();
    await userEvent.type(screen.getByLabelText("spec"), "submodule");
    await userEvent.click(
      await screen.findByRole("option", { name: /submodule-pointers/ }),
    );
    expect(screen.getByText(/spec attached →/)).toBeInTheDocument();
  });

  it("searches the chosen repo's artifacts and submits the picked plan", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      query: "auth",
      mode: "hybrid",
      results: [
        {
          id: "d1",
          repo: "/a",
          kind: "plans",
          source_kind: "artifact",
          title: "Auth plan",
          path: ".engineering/plans/auth.md",
          snippet: "",
          score: 1,
          links: [],
        },
      ],
    });
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w1" });
    renderModal();

    await fillBasics();
    await userEvent.type(screen.getByLabelText("plan"), "auth");
    await userEvent.click(await screen.findByText("Auth plan"));
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );

    await waitFor(() =>
      expect(api.search).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "/a",
          kind: "plans",
          source_kind: "artifact",
        }),
      ),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          attachments: [{ kind: "plan", path: ".engineering/plans/auth.md" }],
        }),
      ),
    );
  });

  // Kraft-ene04. A V1 chain has no `gate_after`: its gate is a node of its
  // own, and each node names the attachment kind that drops it
  // (`covered_by`, from `ResolvedNode.covered_by`) -- the gate that decides the
  // document and the node that would have written it, both.
  it("V1 chain: an attached spec strikes through its gate and the node that writes it", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      {
        id: "default",
        gates: 2,
        nodes: [
          { id: "spec", kind: "exec", tasks: ["spec.main.author"], gate_after: null, covered_by: "spec" },
          { id: "spec_approval", kind: "gate", tasks: [], gate_after: "spec_approval", covered_by: "spec" },
          { id: "plan", kind: "exec", tasks: ["plan.main.author"], gate_after: null, covered_by: "plan" },
          { id: "plan_approval", kind: "gate", tasks: [], gate_after: "plan_approval", covered_by: "plan" },
          { id: "implementation", kind: "exec", tasks: ["implementation.main.implement"], gate_after: null, covered_by: null },
        ],
      },
    ]);
    renderModal();
    await fillBasics();
    await userEvent.type(screen.getByLabelText("spec"), ".engineering/specs/x.md{Enter}");
    expect(await screen.findByText("spec_approval", { selector: "s" })).toBeInTheDocument();
    expect(screen.getByText("spec", { selector: "s" })).toBeInTheDocument();
    expect(screen.getByText("plan_approval").tagName).not.toBe("S");
    expect(screen.getByText("plan").tagName).not.toBe("S");
    expect(screen.getByText("implementation").tagName).not.toBe("S");
  });

  it("accepts a path typed by hand for a document that is not indexed", async () => {
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w1" });
    renderModal();

    await fillBasics();
    await userEvent.type(
      screen.getByLabelText("spec"),
      ".engineering/specs/x.md{Enter}",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          attachments: [{ kind: "spec", path: ".engineering/specs/x.md" }],
        }),
      ),
    );
  });
});
