import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { Settings } from "./Settings";

const repo = {
  path: "/repo-a",
  name: "repo-a",
  default_chain_template: "default",
  test_command: "uv run pytest -q",
  forge: "github",
  project: "acme/repo-a",
  enabled: true,
};

const hooks = {
  "on.env.prepare": { kind: "builtin" as const, handler: "env_setup" },
  "on.implementation.start": { kind: "agent" as const, command: "claude" },
};

const policy = {
  loops: { verify_fix_loop: { attempts: 3, wall_clock_s: 3600 } },
  default: { attempts: 3, wall_clock_s: 3600 },
};

const access = {
  bind: "127.0.0.1",
  port: 8765,
  session_expiry_days: 7,
  password_set: false,
  auth_required: false,
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [repo] });
  vi.spyOn(api, "getTemplates").mockResolvedValue([
    {
      id: "quick-task",
      gates: 0,
      nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: null }],
    },
  ]);
  vi.spyOn(api, "getRegistry").mockResolvedValue({ hooks });
  vi.spyOn(api, "getPolicy").mockResolvedValue(policy);
  vi.spyOn(api, "getAccess").mockResolvedValue(access);
  vi.spyOn(api, "getAuthSessions").mockResolvedValue({ sessions: [] });
});

const renderAt = (path: string) =>
  render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/settings/*" element={<Settings />} />
      </Routes>
    </MemoryRouter>,
  );

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
});

describe("Settings · templates (5b)", () => {
  it("validates a draft without saving it", async () => {
    const validate = vi.spyOn(api, "validateTemplate").mockResolvedValue({
      id: "quick-task",
      valid: false,
      error: "hook(s) ['on.nope'] are not in the registry",
      by_repo: [{ repo: "/repo-a", resolvable: false }],
    });
    const put = vi.spyOn(api, "putTemplate");
    renderAt("/settings/templates");
    await screen.findByRole("button", { name: /quick-task/ });

    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(validate).toHaveBeenCalled();
    expect(await screen.findByText(/not in the registry/)).toBeInTheDocument();
    expect(screen.getByText("/repo-a: unresolvable")).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  it("refuses to save a draft that is not even JSON", async () => {
    renderAt("/settings/templates");
    const box = await screen.findByLabelText("template nodes");
    await userEvent.clear(box);
    await userEvent.type(box, "{{ broken");
    expect(screen.getByText(/not valid JSON/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("Settings · plugins (5c)", () => {
  it("toggles the steerable flag and reports what the save broke", async () => {
    const put = vi.spyOn(api, "putRegistry").mockResolvedValue({
      hooks,
      invalid_templates: { hotfix: "hook(s) missing" },
    });
    renderAt("/settings/plugins");
    const toggle = await screen.findByRole("switch", {
      name: "steerable: on.implementation.start",
    });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith({
      ...hooks,
      "on.implementation.start": { ...hooks["on.implementation.start"], interactive: true },
    });
    expect(await screen.findByText(/now unresolvable: hotfix/)).toBeInTheDocument();
  });
});

describe("Settings · policy (5d)", () => {
  it("edits a cap and saves every counter together", async () => {
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const attempts = await screen.findByLabelText("verify_fix_loop attempts");
    await userEvent.clear(attempts);
    await userEvent.type(attempts, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith({
      loops: { verify_fix_loop: { attempts: 5, wall_clock_s: 3600 } },
      default: { attempts: 3, wall_clock_s: 3600 },
    });
  });
});

describe("Settings · access (5e)", () => {
  it("switches the bind and sets a password", async () => {
    const put = vi.spyOn(api, "putAccess").mockResolvedValue({ ...access, bind: "0.0.0.0" });
    renderAt("/settings/access");
    await screen.findByRole("heading", { name: "Access" });

    await userEvent.click(screen.getByRole("radio", { name: /0\.0\.0\.0/ }));
    expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0" });

    await userEvent.type(screen.getByLabelText("Set a password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenLastCalledWith({ password: "hunter2" });
  });

  it("lists sessions and revokes one", async () => {
    vi.spyOn(api, "getAuthSessions").mockResolvedValue({
      sessions: [
        {
          id: "abc",
          label: "Firefox",
          ip: "192.168.1.9",
          created_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
          expires_at: new Date().toISOString(),
          current: true,
        },
      ],
    });
    const revoke = vi.spyOn(api, "revokeSession").mockResolvedValue(undefined);
    renderAt("/settings/access");
    expect(await screen.findByText("Firefox")).toBeInTheDocument();
    expect(screen.getByText("current")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^session /i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Revoke" }));
    // this is the session doing the asking — confirm says so before it lands
    expect(revoke).not.toHaveBeenCalled();
    expect(screen.getByText(/signs you out here/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: "Revoke" }));
    expect(revoke).toHaveBeenCalledWith("abc");
  });
});
