import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { sortRepos } from "./ReposPage";
import { renderAt, repo, setupSettingsMocks } from "./testing";

describe("sortRepos", () => {
  it("keeps a child directly beneath its parent, ahead of siblings whose name merely extends it", () => {
    const paths = ["/p/kraft", "/p/kraft-lite", "/p/kraft.bak", "/p/kraft/libs/a"];
    const sorted = sortRepos(paths.map((path) => repo({ path }))).map((r) => r.path);
    expect(sorted).toEqual(["/p/kraft", "/p/kraft/libs/a", "/p/kraft-lite", "/p/kraft.bak"]);
  });
});

const probeFixture = {
  path: "/repo-b",
  name: "repo-b",
  branch: "main",
  submodules: [] as string[],
  has_beads: true,
  beads_export_auto: true,
  beads_export_git_add: true,
  has_engineering: true,
  test_command: null as string | null,
  test_scopes: null as { paths: string[]; command: string }[] | null,
  forge: null as string | null,
  project: null as string | null,
};

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · repos (5a)", () => {
  it("lists connected repos and offers destructive actions only behind the overflow", async () => {
    const del = vi.spyOn(api, "deleteRepo").mockResolvedValue(undefined);
    renderAt("/settings/repos");
    expect(await screen.findByText("repo-a")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^disconnect/i })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    // disconnecting has no undo, so the first click arms a confirm rather than
    // firing; the menu names the repo it is about to drop
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText(/Disconnect repo-a\?/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("menuitem", { name: "Cancel" }));
    expect(del).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    expect(del).toHaveBeenCalledWith("/repo-a");
  });

  it("shows the forge column and a live enabled switch, not a text column", async () => {
    renderAt("/settings/repos");
    expect(await screen.findByText("github · acme/repo-a")).toBeInTheDocument();
    expect(screen.getAllByRole("switch").length).toBeGreaterThan(0);
  });

  it("shows a dev repo's fake forge as selected", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [repo({ forge: "fake" })] });
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByText("repo-a"));
    const forge = await screen.findByRole("radiogroup", { name: "forge" });
    expect(within(forge).getByRole("radio", { name: /fake \(dev only\)/ })).toBeChecked();

    // Switching away in the draft must not take the way back with it: the
    // option is keyed on the saved repo, not the draft (review finding 9).
    await userEvent.click(within(forge).getByRole("radio", { name: "github" }));
    expect(within(forge).getByRole("radio", { name: /fake \(dev only\)/ })).not.toBeChecked();
  });

  it("does not offer the dev-only fake forge to a real repo", async () => {
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByText("repo-a"));
    const forge = await screen.findByRole("radiogroup", { name: "forge" });
    expect(within(forge).queryByRole("radio", { name: /fake/ })).toBeNull();
    expect(within(forge).getByRole("radio", { name: "github" })).toBeChecked();
  });

  it("clicking a row selects it into the URL and opens the detail pane", async () => {
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByText("repo-a"));
    expect(await screen.findByRole("heading", { name: "repo-a" })).toBeInTheDocument();
  });

  it("probes a path as it is typed and only enables Connect once the probe lands", async () => {
    const probe = vi.spyOn(api, "probeRepo").mockResolvedValue({
      ...probeFixture,
      submodules: ["libs/a"],
      test_command: "npm test",
      forge: "github",
      project: "acme/repo-b",
      has_engineering: false,
    });
    const add = vi.spyOn(api, "addRepo").mockResolvedValue(repo({ path: "/repo-b" }));
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByRole("button", { name: /add repo/i }));

    const dialog = screen.getByRole("dialog", { name: "Add repo" });
    expect(within(dialog).getByRole("button", { name: /connect/i })).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText("Path"), "/repo-b");
    await waitFor(() => expect(probe).toHaveBeenCalled());
    expect(await within(dialog).findByText(/1 submodule \(libs\/a\)/)).toBeInTheDocument();
    expect(within(dialog).getByText(/no \.engineering\/ yet/)).toBeInTheDocument();
    expect(within(dialog).getByText("npm test")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: /connect/i }));
    expect(add).toHaveBeenCalledWith(
      expect.objectContaining({ path: "/repo-b", default_chain_template: "default" }),
    );
  });

  const renderProbe = async (probeFields: { forge: string | null; project: string | null }) => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...probeFixture, ...probeFields });
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByRole("button", { name: /add repo/i }));
    const dialog = screen.getByRole("dialog", { name: "Add repo" });
    await userEvent.type(within(dialog).getByLabelText("Path"), "/repo-b");
    return dialog;
  };

  it.each([
    ["the detected forge and project", { forge: "github", project: "owner/repo" }, "github · owner/repo"],
    ["that no forge was detected", { forge: null, project: null }, "no forge remote detected"],
    ["the forge alone when no project was recorded", { forge: "gitea", project: null }, "gitea"],
  ])("shows %s", async (_, probe, text) => {
    const dialog = await renderProbe(probe);
    expect(await within(dialog).findByText(text)).toBeInTheDocument();
  });

  it("/settings with no page is the Settings index, not a redirect to Repos (W7.9)", async () => {
    renderAt("/settings");
    expect(await screen.findByText("How work runs")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Repos" })).toBeNull();
  });

  it("sorts children directly beneath their parent", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [
        repo({ path: "/zz", name: "zz" }),
        repo({ path: "/ws/libs/a", name: "a" }),
        repo({ path: "/ws", name: "ws" }),
      ],
    });
    renderAt("/settings/repos");
    await screen.findByText("ws");
    const names = screen
      .getAllByRole("button")
      .map((el) => el.getAttribute("data-repo"))
      .filter(Boolean);
    expect(names).toEqual(["/ws", "/ws/libs/a", "/zz"]);
  });

  it("filters the list by name and by path", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [repo({ path: "/repo-a", name: "repo-a" }), repo({ path: "/repo-b", name: "repo-b" })],
    });
    renderAt("/settings/repos");
    await screen.findByText("repo-a");
    await userEvent.type(screen.getByRole("searchbox", { name: /filter repos/i }), "repo-b");
    expect(screen.queryByText("repo-a")).toBeNull();
    expect(screen.getByText("repo-b")).toBeInTheDocument();
  });

  it("marks a child row with the workspace it belongs to", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [repo({ path: "/ws", name: "ws" }), repo({ path: "/ws/libs/a", name: "a" })],
    });
    renderAt("/settings/repos");
    await screen.findByText("ws");
    const child = screen.getByText("a").closest("[data-repo]")!;
    expect(within(child as HTMLElement).getByText("in ws")).toBeInTheDocument();
  });

  it("collapses detected-but-untouched repos into their own section", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [
        repo({ path: "/ws", name: "ws", managed: true }),
        repo({ path: "/ws/libs/a", name: "a", managed: false, enabled: false }),
        repo({ path: "/ws/libs/b", name: "b", managed: false, enabled: false }),
      ],
    });
    renderAt("/settings/repos");
    expect(await screen.findByText("ws")).toBeInTheDocument();
    // the two detected children are behind one row, not two in the main list
    expect(screen.queryByText("a")).toBeNull();
    expect(screen.getByText(/Detected · 2 · not managed/)).toBeInTheDocument();

    await userEvent.click(screen.getByText(/Detected · 2 · not managed/));
    expect(screen.getByText("a")).toBeInTheDocument();
  });

  it("keeps a disabled but managed repo in the main list", async () => {
    // "a human turned this off" is a decision, and must not read as noise
    vi.spyOn(api, "getRepos").mockResolvedValue({
      repos: [repo({ path: "/ws", name: "ws", managed: true, enabled: false })],
    });
    renderAt("/settings/repos");
    expect(await screen.findByText("ws")).toBeInTheDocument();
    expect(screen.queryByText(/Detected/)).toBeNull();
  });
});

describe("Settings · repo detail (5b)", () => {
  it("opens the repo detail with GENERAL/TESTING/FORGE/AGENT sections", async () => {
    renderAt("/settings/repos?repo=/repo-a");
    expect(await screen.findByRole("heading", { name: "repo-a" })).toBeInTheDocument();
    for (const label of ["General", "Testing", "Forge", "Agent"]) {
      expect(screen.getByText(label, { exact: false })).toBeInTheDocument();
    }
    // Ruling 165: the root pointer default is a workspace's, not a repo's.
    expect(screen.queryByText("Cross-repo")).toBeNull();
    expect(screen.queryByText("root merge policy")).toBeNull();
  });

  it("models are set per harness profile and round-trip through patchRepo", async () => {
    const repoC = repo({ path: "/repo-c", name: "repo-c", models: { claude_review: "sonnet" } });
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [repo(), repoC] });
    const patch = vi.spyOn(api, "patchRepo").mockResolvedValue(repoC);
    renderAt("/settings/repos?repo=/repo-c");

    const models = await screen.findByLabelText("models");
    expect(models).toHaveValue("claude_review=sonnet");
    await userEvent.clear(models);
    await userEvent.type(models, "claude_review=opus{enter}codex_default = gpt-5{enter}half");
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));

    expect(patch).toHaveBeenCalledWith(
      "/repo-c",
      expect.objectContaining({ models: { claude_review: "opus", codex_default: "gpt-5" } }),
    );
  });

  it("no longer offers a per-submodule config table", async () => {
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByText("repo-a"));
    expect(screen.queryByText("CHAIN OVERRIDE")).toBeNull();
    expect(screen.queryByLabelText("allow cross-repo items")).toBeNull();
  });

  it("Save is disabled until something changes, and writes on click", async () => {
    const patch = vi.spyOn(api, "patchRepo").mockResolvedValue(repo());
    renderAt("/settings/repos?repo=/repo-a");
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(await screen.findByLabelText("name"), "x");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(patch).toHaveBeenCalled();
  });

  it("Disconnect asks before it fires", async () => {
    const del = vi.spyOn(api, "deleteRepo").mockResolvedValue(undefined);
    renderAt("/settings/repos?repo=/repo-a");
    await userEvent.click(await screen.findByRole("button", { name: /disconnect repo-a/i }));
    expect(del).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: /disconnect repo-a/i }));
    expect(del).toHaveBeenCalled();
  });

  it("test scopes are editable and round-trip through patchRepo", async () => {
    const repoC = repo({
      path: "/repo-c",
      name: "repo-c",
      test_scopes: [{ paths: ["frontend/**"], command: "just test-ui" }],
    });
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [repo(), repoC] });
    const patch = vi.spyOn(api, "patchRepo").mockResolvedValue(repoC);
    renderAt("/settings/repos?repo=/repo-c");

    const commandInput = await screen.findByDisplayValue("just test-ui");
    await userEvent.clear(commandInput);
    await userEvent.type(commandInput, "just ci-test");
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));

    expect(patch).toHaveBeenCalledWith(
      "/repo-c",
      expect.objectContaining({
        test_scopes: [{ paths: ["frontend/**"], command: "just ci-test" }],
      }),
    );
  });

  it("local files are addable, removable, and round-trip through patchRepo", async () => {
    const repoC = repo({
      path: "/repo-c",
      name: "repo-c",
      local_files: [".python-version"],
    });
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [repo(), repoC] });
    const patch = vi.spyOn(api, "patchRepo").mockResolvedValue(repoC);
    const prompt = vi.spyOn(window, "prompt").mockReturnValue(".env");
    renderAt("/settings/repos?repo=/repo-c");

    const tag = await screen.findByText(".python-version ✕");
    expect(tag).toBeInTheDocument();
    // "+ add" is not unique — steering and deny tools use the same label —
    // so scope to local files' own field container.
    const field = tag.closest(".field") as HTMLElement;

    await userEvent.click(within(field).getByRole("button", { name: "+ add" }));
    expect(prompt).toHaveBeenCalledWith("Relative file path, e.g. .python-version");
    expect(await screen.findByText(".env ✕")).toBeInTheDocument();

    await userEvent.click(screen.getByText(".python-version ✕"));
    expect(screen.queryByText(".python-version ✕")).toBeNull();

    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(patch).toHaveBeenCalledWith(
      "/repo-c",
      expect.objectContaining({ local_files: [".env"] }),
    );
  });

  it("re-probing offers the found scopes, and applying them stages a draft edit", async () => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/repo-a",
      name: "repo-a",
      branch: "main",
      submodules: [],
      has_beads: true,
      beads_export_auto: true,
      beads_export_git_add: true,
      has_engineering: true,
      test_command: "just ci-test",
      test_scopes: [
        { paths: ["frontend/**"], command: "just test-ui" },
        { paths: ["frontend/**"], command: "just e2e-ci" },
        { paths: ["src/**"], command: "just ci-test" },
      ],
      forge: "github",
      project: "acme/repo-a",
    });
    const patch = vi.spyOn(api, "patchRepo").mockResolvedValue(repo());
    renderAt("/settings/repos?repo=/repo-a");

    await userEvent.click(await screen.findByRole("button", { name: /re-probe/i }));
    await userEvent.click(await screen.findByRole("button", { name: /apply 3 probed scope/i }));
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));

    expect(patch).toHaveBeenCalledWith(
      "/repo-a",
      expect.objectContaining({
        test_scopes: [
          { paths: ["frontend/**"], command: "just test-ui" },
          { paths: ["frontend/**"], command: "just e2e-ci" },
          { paths: ["src/**"], command: "just ci-test" },
        ],
      }),
    );
  });
});
