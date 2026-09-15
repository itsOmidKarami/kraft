import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
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
  default_model: null,
  deny_tools: [],
  steering: [],
  default_root_merge_policy: "bump" as const,
  managed: true,
};

function renderModal(onClose: () => void = () => {}) {
  return render(
    <MemoryRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
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

describe("IntakeModal", () => {
  it("says where to get one when there are no repos connected", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    renderModal();
    expect(
      await screen.findByText(/connect one in Settings/i),
    ).toBeInTheDocument();
  });

  it("lays the dialog out in two columns with an overrides column", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
    renderModal();
    const dlg = await screen.findByRole("dialog");
    expect(dlg.querySelector(".intake-grid")).toBeTruthy();
    expect(within(dlg).getByText(/OVERRIDES FOR THIS ITEM/i)).toBeTruthy();
    expect(within(dlg).getByText(/Will happen on start/i)).toBeTruthy();
  });

  it("shows a disabled repo as a disabled option, not omitted", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
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
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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

  it("rejects a non-numeric budget instead of silently sending no cap", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
    const create = vi
      .spyOn(api, "createWorkItem")
      .mockResolvedValue({ id: "w9" });
    renderModal();
    await fillBasics();
    await userEvent.type(screen.getByLabelText("budget"), "$20");
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    expect(
      await screen.findByText(/budget must be a plain number/),
    ).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("submits the description with the new work item", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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

  it("rejects a non-numeric fix attempts value loudly instead of sending NaN", async () => {
    const create = vi.spyOn(api, "createWorkItem");
    renderModal();
    await fillBasics();
    await userEvent.type(await screen.findByLabelText(/^fix attempts$/i), "lots");
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    expect(await screen.findByText(/fix attempts must be a plain number/i)).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
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
        nodes: [{ id: "plan", tasks: [], gate_after: "plan_approval" }],
      },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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

  it("offers the cross-repo disclosure only when the repo actually has submodules", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
    const probe = vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/a",
      name: "repo-a",
      branch: "main",
      submodules: [],
      has_beads: true,
      beads_export_auto: true,
      beads_export_git_add: true,
      has_engineering: true,
      test_command: null,
      test_scopes: null,
      forge: null,
      project: null,
    });
    renderModal();
    await selectRepo();
    await waitFor(() => expect(probe).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /cross-repo/ })).toBeNull();
  });

  it("sends the picked submodules and the root merge policy", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
    vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/a",
      name: "repo-a",
      branch: "main",
      submodules: ["libs/a", "libs/b"],
      has_beads: true,
      beads_export_auto: true,
      beads_export_git_add: true,
      has_engineering: true,
      test_command: null,
      test_scopes: null,
      forge: null,
      project: null,
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
    await userEvent.click(screen.getByRole("radio", { name: "Skip" }));
    await userEvent.click(
      screen.getByRole("button", { name: /create and start/i }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "/a",
          title: "bump pointers",
          submodules: ["libs/a"],
          root_merge_policy: "skip",
        }),
      ),
    );
  });

  it("attaches a spec through one search field that becomes a chip", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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
    expect(
      screen.getByText(/spec attached → spec_approval satisfied/),
    ).toBeInTheDocument();
  });

  it("searches the chosen repo's artifacts and submits the picked plan", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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

  it("shows a chain preview with the nodes an attachment removes struck through", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      {
        id: "default",
        gates: 2,
        nodes: [
          {
            id: "spec",
            tasks: ["on.spec.requested"],
            gate_after: "spec_approval",
          },
          {
            id: "plan",
            tasks: ["on.plan.requested"],
            gate_after: "plan_approval",
          },
          {
            id: "implementation",
            tasks: ["on.implementation.start"],
            gate_after: null,
          },
        ],
      },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
    renderModal();

    await fillBasics();
    await userEvent.type(
      screen.getByLabelText("spec"),
      ".engineering/specs/x.md{Enter}",
    );

    const struckSpec = await screen.findByText("spec", { selector: "s" });
    expect(struckSpec).toBeInTheDocument();
    expect(screen.getByText("plan").tagName).not.toBe("S");
    expect(screen.getByText("implementation").tagName).not.toBe("S");
  });

  it("accepts a path typed by hand for a document that is not indexed", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([
      { id: "default", nodes: [], gates: 0 },
    ]);
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [REPO_A] });
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
