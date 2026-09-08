import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import { IntakeModal } from "./IntakeModal";

describe("IntakeModal", () => {
  it("offers the connected repos, and says where to get one when there are none", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    const repos = vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] } as never);
    useStore.setState({ workItems: {} } as never);
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    // a fresh install has no work items either, so the field would otherwise be
    // an empty box with no hint that a repo has to be connected first
    expect(await screen.findByText(/connect one in Settings/i)).toBeInTheDocument();

    repos.mockResolvedValue({
      repos: [{ path: "/connected", enabled: true }],
    } as never);
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(document.querySelector('#intake-repos option[value="/connected"]')).not.toBeNull(),
    );
  });

  it("submits and shows an inline error on failure", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    vi.spyOn(api, "createWorkItem").mockRejectedValue(new Error("repo path does not exist"));
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/nope");
    await userEvent.type(screen.getByLabelText("title"), "do a thing");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(await screen.findByText(/repo path does not exist/)).toBeInTheDocument();
  });

  it("closes and navigates on success, omitting chain_template for quick-task", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }, { id: "default", nodes: [], gates: 0 }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    const onClose = vi.fn();
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<IntakeModal onClose={onClose} />} />
          <Route path="/work-items/:id" element={<p>detail for w9</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "do a thing");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({ repo: "/r", title: "do a thing" }));
    expect(onClose).toHaveBeenCalled();
    expect(await screen.findByText("detail for w9")).toBeInTheDocument();
  });

  it("submits the description with the new work item", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<IntakeModal onClose={() => {}} />} />
          <Route path="/work-items/:id" element={<p>detail for w9</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "short label");
    await userEvent.type(screen.getByLabelText("description"), "the brief");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        repo: "/r",
        title: "short label",
        description: "the brief",
      }),
    );
  });

  it("sends chain_template when a non-default template is picked", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }, { id: "default", nodes: [], gates: 0 }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.click(await screen.findByRole("radio", { name: "default" }));
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ repo: "/r", title: "t", chain_template: "default" }),
    );
  });

  it("does not submit a template the server never offered", async () => {
    // The control defaults to "quick-task" before /templates answers. If the
    // server does not offer it, nothing is checked while state still says
    // quick-task — and we would submit a chain the server never listed.
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "default", nodes: [], gates: 0 }, { id: "release", nodes: [], gates: 0 }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    const picked = () =>
      (screen.getAllByRole("radio") as HTMLInputElement[]).find((r) => r.checked)?.value;
    await waitFor(() => expect(picked()).toBe("default"));
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ repo: "/r", title: "t", chain_template: "default" }),
    );
  });

  it("offers the cross-repo disclosure only when the repo actually has submodules", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    const probe = vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/r", name: "r", branch: "main", submodules: [], has_beads: true,
      beads_export_auto: true, beads_export_git_add: true, has_engineering: true,
      test_command: null, forge: null, project: null,
    });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await waitFor(() => expect(probe).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /cross-repo/ })).toBeNull();
  });

  it("sends the picked submodules and the root merge policy", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/r", name: "r", branch: "main", submodules: ["libs/a", "libs/b"],
      has_beads: true, beads_export_auto: true, beads_export_git_add: true,
      has_engineering: true, test_command: null, forge: null, project: null,
    });
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w9" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/r");
    await userEvent.type(screen.getByLabelText("title"), "bump pointers");

    // collapsed by default
    const disclosure = await screen.findByRole("button", { name: /cross-repo/ });
    expect(screen.queryByRole("button", { name: "libs/a" })).toBeNull();
    await userEvent.click(disclosure);

    await userEvent.click(screen.getByRole("button", { name: "libs/a" }));
    await userEvent.click(screen.getByRole("radio", { name: "Skip" }));
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        repo: "/r",
        title: "bump pointers",
        submodules: ["libs/a"],
        root_merge_policy: "skip",
      }),
    );
  });

  it("searches the chosen repo's artifacts and submits the picked plan", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    vi.spyOn(api, "search").mockResolvedValue({
      query: "auth",
      mode: "hybrid",
      results: [
        {
          id: "d1",
          repo: "/repo",
          kind: "plans",
          source_kind: "artifact",
          title: "Auth plan",
          path: ".engineering/plans/auth.md",
          snippet: "",
          score: 1,
          links: [],
        },
      ],
    } as never);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w1" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("repo"), "/repo");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.type(screen.getByLabelText("existing plan"), "auth");
    await userEvent.click(await screen.findByText("Auth plan"));
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(api.search).toHaveBeenCalledWith(
        expect.objectContaining({ repo: "/repo", kind: "plans", source_kind: "artifact" }),
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
          { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
          { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
          { id: "implementation", tasks: ["on.implementation.start"], gate_after: null },
        ],
      },
    ]);
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("repo"), "/repo");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.type(screen.getByLabelText("spec path"), ".engineering/specs/x.md");

    const struckSpec = await screen.findByText("spec", { selector: "s" });
    expect(struckSpec).toBeInTheDocument();
    expect(screen.getByText("plan").tagName).not.toBe("S");
    expect(screen.getByText("implementation").tagName).not.toBe("S");
  });

  it("accepts a path typed by hand for a document that is not indexed", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task", nodes: [], gates: 0 }]);
    const create = vi.spyOn(api, "createWorkItem").mockResolvedValue({ id: "w1" });
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("repo"), "/repo");
    await userEvent.type(screen.getByLabelText("title"), "t");
    await userEvent.type(screen.getByLabelText("spec path"), ".engineering/specs/x.md");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          attachments: [{ kind: "spec", path: ".engineering/specs/x.md" }],
        }),
      ),
    );
  });
});
