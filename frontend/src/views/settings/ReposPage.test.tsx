import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, repo, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · repos (5a)", () => {
  it("lists connected repos and offers destructive actions only behind the overflow", async () => {
    const del = vi.spyOn(api, "deleteRepo").mockResolvedValue(undefined);
    renderAt("/settings/repos");
    expect(await screen.findByText("repo-a")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /disconnect/i })).toBeNull();

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

  it("probes a path as it is typed and only enables Connect once the probe lands", async () => {
    const probe = vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/repo-b",
      name: "repo-b",
      branch: "main",
      submodules: ["libs/a"],
      has_beads: true,
      beads_export_auto: true,
      beads_export_git_add: true,
      has_engineering: false,
      test_command: "npm test",
      forge: "github",
      project: "acme/repo-b",
    });
    const add = vi.spyOn(api, "addRepo").mockResolvedValue({ ...repo, path: "/repo-b" });
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
    expect(add).toHaveBeenCalledWith({ path: "/repo-b", default_chain_template: "default" });
  });

  const renderProbe = async (probeFields: { forge: string | null; project: string | null }) => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({
      path: "/repo-b",
      name: "repo-b",
      branch: "main",
      submodules: [],
      has_beads: true,
      beads_export_auto: true,
      beads_export_git_add: true,
      has_engineering: true,
      test_command: null,
      ...probeFields,
    });
    renderAt("/settings/repos");
    await userEvent.click(await screen.findByRole("button", { name: /add repo/i }));
    const dialog = screen.getByRole("dialog", { name: "Add repo" });
    await userEvent.type(within(dialog).getByLabelText("Path"), "/repo-b");
    return dialog;
  };

  it("shows the detected forge and project", async () => {
    const dialog = await renderProbe({ forge: "github", project: "owner/repo" });
    expect(await within(dialog).findByText("github · owner/repo")).toBeInTheDocument();
  });

  it("says so when no forge was detected", async () => {
    const dialog = await renderProbe({ forge: null, project: null });
    expect(await within(dialog).findByText("no forge remote detected")).toBeInTheDocument();
  });

  it("shows the forge alone when no project was recorded", async () => {
    const dialog = await renderProbe({ forge: "gitea", project: null });
    expect(await within(dialog).findByText("gitea")).toBeInTheDocument();
  });

  it("lands on Repos when /settings is opened with no page", async () => {
    // The router's `<Route index>` does this. A `useEffect` reading
    // window.location.pathname used to do it as well; this pins the surviving
    // half so the deletion of the other one stays honest.
    renderAt("/settings");
    expect(await screen.findByRole("heading", { name: "Repos" })).toBeInTheDocument();
  });
});
