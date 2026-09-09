import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { Settings } from "./Settings";
import type { TemplateSummary, Theme } from "../types";

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

const theme: Theme = { palette: "nocturne", mode: "dark" };

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
  vi.spyOn(api, "getTheme").mockResolvedValue(theme);
  vi.spyOn(api, "getSteering").mockResolvedValue({
    files: [{ name: "house-style", bytes: 14 }],
    max_bytes: 8192,
  });
  vi.spyOn(api, "getSteeringFile").mockResolvedValue({
    name: "house-style",
    body: "prefer stdlib\n",
  });
  vi.spyOn(api, "getIntake").mockResolvedValue({
    enabled: false,
    interval_s: 300,
    max_concurrent: 1,
    priority_ceiling: 2,
    repos: [],
  });
  vi.spyOn(api, "getAccess").mockResolvedValue(access);
  vi.spyOn(api, "getAuthSessions").mockResolvedValue({ sessions: [] });
  vi.spyOn(api, "getNotify").mockResolvedValue({
    enabled: false,
    url_set: false,
    base_url: null,
    events: ["gate_requested", "work_item_needs_human"],
  });
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

  it("does not clobber a fresh edit with the re-fetch of the previous save", async () => {
    // A fake backend with a network-like gap: `putTemplate` commits quickly,
    // `getTemplates` (re-fetched by `reload`) reads it back more slowly. If
    // `busy` clears as soon as the PUT resolves, Save re-enables while the
    // page still holds pre-save state — the user types the next edit, and
    // then the late re-fetch lands and `setDraft(original)` wipes it.
    const backend: TemplateSummary[] = [
      {
        id: "quick-task",
        gates: 0,
        nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: null }],
      },
    ];
    vi.spyOn(api, "getTemplates").mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve(backend.map((t) => ({ ...t }))), 40),
        ),
    );
    const put = vi.spyOn(api, "putTemplate").mockImplementation(
      (_id, nodes) =>
        new Promise((resolve) =>
          setTimeout(() => {
            backend[0] = { ...backend[0], nodes };
            resolve(undefined as never);
          }, 5),
        ),
    );
    renderAt("/settings/templates");

    await screen.findByRole("button", { name: /quick-task/ });
    const box = screen.getByLabelText("template nodes") as HTMLTextAreaElement;
    // fireEvent, not userEvent.type: `[` and `{` are key-descriptor syntax
    // for userEvent's keyboard parser, and this draft is JSON.
    fireEvent.change(box, { target: { value: '[{"id":"one"}]' } });
    const save = screen.getByRole("button", { name: "Save" });
    await userEvent.click(save);
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));

    // As soon as Save is live again, make the next edit — in the broken
    // version this lands after the PUT but before the re-fetch.
    await waitFor(() => expect(save).not.toBeDisabled());
    fireEvent.change(box, { target: { value: '[{"id":"two"}]' } });

    // give the slow re-fetch every chance to land on top of the new edit
    await new Promise((r) => setTimeout(r, 80));
    expect(box.value).toBe('[{"id":"two"}]');
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

  it("clearing a budget field saves null, not zero", async () => {
    vi.spyOn(api, "getPolicy").mockResolvedValue({
      ...policy,
      budget: { work_item_usd: 20, daily_usd: null },
    });
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const field = await screen.findByLabelText("work item budget");
    expect(field).toHaveValue(20);
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({ budget: { work_item_usd: null, daily_usd: null } }),
    );
  });

  it("states the one-task overshoot", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByText(/overshoot/i)).toBeInTheDocument();
  });
});

describe("Settings · appearance", () => {
  it("lists all 5 palettes and the light/dark/system control", async () => {
    renderAt("/settings/appearance");
    expect(await screen.findByText("Nocturne")).toBeInTheDocument();
    for (const name of ["Rose", "Forest", "Amber", "Slate"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getByRole("radiogroup", { name: /mode/i })).toBeInTheDocument();
  });

  it("previews live on click and saves on Save", async () => {
    const put = vi.spyOn(api, "putTheme").mockResolvedValue({ palette: "forest", mode: "dark" });
    renderAt("/settings/appearance");
    await screen.findByText("Nocturne");

    fireEvent.click(screen.getByText("Forest"));
    expect(document.documentElement.dataset.palette).toBe("forest");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith({ palette: "forest", mode: "dark" });
  });

  it("Discard reverts the live preview back to the loaded value", async () => {
    renderAt("/settings/appearance");
    await screen.findByText("Nocturne");

    fireEvent.click(screen.getByText("Rose"));
    expect(document.documentElement.dataset.palette).toBe("rose");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(document.documentElement.dataset.palette).toBe("nocturne");
  });
});

describe("Settings · steering", () => {
  it("loads a file's body only when it is picked, and saves it back", async () => {
    const get = vi.spyOn(api, "getSteeringFile");
    const put = vi
      .spyOn(api, "putSteeringFile")
      .mockImplementation(async (name, body) => ({ name, body }));
    renderAt("/settings/steering");
    await screen.findByRole("heading", { name: "Steering" });

    // the list is a picker: every body at once would be the whole injection
    // budget over the wire on every page load
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByText("14 B")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /house-style/ }));
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));

    // unchanged body: nothing to save
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(box, { target: { value: "prefer stdlib, then native\n" } });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("house-style", "prefer stdlib, then native\n");
    expect(await screen.findByText("saved")).toBeInTheDocument();
  });

  it("meters the open file, not the whole directory", async () => {
    // MAX_BYTES is the assembled budget of one repo or hook's steering list.
    // Summing every file in the directory against it reads as over budget when
    // nothing is, and under it when something is.
    vi.spyOn(api, "getSteering").mockResolvedValue({
      files: [
        { name: "house-style", bytes: 14 },
        { name: "other", bytes: 9000 },
      ],
      max_bytes: 8192,
    });
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByRole("button", { name: /house-style/ }));
    const box = (await screen.findByLabelText("steering body")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("prefer stdlib\n"));

    // 14 B open, 9014 B on disk in total — the meter must say 14. Scoped to the
    // hint because the list row legitimately shows this file's size too.
    const hint = screen.getByText(/counts toward/);
    expect(hint).toHaveTextContent("14 B");
    expect(hint).not.toHaveTextContent("9014");

    fireEvent.change(box, { target: { value: "12345" } });
    await waitFor(() => expect(screen.getByText(/counts toward/)).toHaveTextContent("5 B"));
  });

  it("surfaces a refused delete instead of dropping the file from the list", async () => {
    // A hook still naming the file is exactly when the server says no, and it
    // is the case the operator most needs to read.
    vi.spyOn(api, "deleteSteeringFile").mockRejectedValue(
      new Error("registry.yaml: steering 'house-style' does not resolve"),
    );
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByRole("button", { name: /house-style/ }));
    await screen.findByLabelText("steering body");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(/does not resolve/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /house-style/ })).toBeInTheDocument();
  });
});

describe("Settings · auto-intake", () => {
  it("saves the whole poller config in one PUT and reports it applied", async () => {
    const put = vi.spyOn(api, "putIntake").mockImplementation(async (body) => body);
    renderAt("/settings/intake");
    await screen.findByRole("heading", { name: "Auto-intake" });

    // nothing is sent until Save: the poller restarts on write, so a
    // half-edited form must not bounce it once per keystroke.
    await userEvent.click(screen.getByRole("switch", { name: "Auto-intake" }));
    const interval = screen.getByLabelText("poll interval");
    await userEvent.clear(interval);
    await userEvent.type(interval, "60");
    await userEvent.click(await screen.findByRole("checkbox", { name: /repo-a/ }));
    expect(put).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][0]).toEqual({
      enabled: true,
      interval_s: 60,
      max_concurrent: 1,
      priority_ceiling: 2,
      repos: ["/repo-a"],
    });
    expect(await screen.findByText("saved")).toBeInTheDocument();
  });

  it("shows the server's rejection rather than pretending the save landed", async () => {
    vi.spyOn(api, "putIntake").mockRejectedValue(new Error("interval_s: too small"));
    renderAt("/settings/intake");
    await screen.findByRole("heading", { name: "Auto-intake" });
    await userEvent.click(screen.getByRole("switch", { name: "Auto-intake" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("interval_s: too small")).toBeInTheDocument();
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

  it("reports a save only once the new state has been read back", async () => {
    // Same network-like gap as the templates page: `putAccess` commits
    // quickly, `getAccess` reads it back more slowly. "saved" is the signal
    // that the page is settled, so it must not appear while the page still
    // renders pre-save state.
    const backend = { ...access };
    vi.spyOn(api, "getAccess").mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ ...backend }), 40)),
    );
    vi.spyOn(api, "putAccess").mockImplementation(
      (body) =>
        new Promise((resolve) =>
          setTimeout(() => {
            Object.assign(backend, body, { password_set: true });
            resolve({ ...backend });
          }, 5),
        ),
    );
    renderAt("/settings/access");

    await userEvent.type(await screen.findByLabelText("Set a password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("saved");
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });
});

describe("Settings → Notifications", () => {
  it("never renders the webhook URL back into the DOM", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: true,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    expect(await screen.findByText(/a webhook URL is set/i)).toBeInTheDocument();
    const field = screen.getByLabelText(/webhook url/i) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(field).toHaveAttribute("type", "password");
  });

  it("saves a URL without re-sending it on the next unrelated save", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    const put = vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");

    const field = (await screen.findByLabelText(/webhook url/i)) as HTMLInputElement;
    await userEvent.type(field, "https://ntfy.sh/my-topic");
    const channelSection = field.closest("section") as HTMLElement;
    await userEvent.click(within(channelSection).getByRole("button", { name: /^save$/i }));
    expect(put).toHaveBeenNthCalledWith(1, { url: "https://ntfy.sh/my-topic" });
    expect(field.value).toBe("");

    // unrelated save: toggling an event switch must not resend the (now
    // cleared) URL draft.
    await userEvent.click(await screen.findByRole("switch", { name: "gate_requested" }));
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls[1][0]).not.toHaveProperty("url");
    expect(put.mock.calls[1][0]).toEqual({
      events: ["work_item_needs_human"],
    });
  });

  it("offers clearing the URL as an explicit action", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    const put = vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    await userEvent.click(await screen.findByRole("button", { name: /clear/i }));
    expect(put).toHaveBeenCalledWith({ url: "" });
  });

  it("commits both changes when two different event switches are toggled back to back", async () => {
    // A fake backend with a network-like gap: `putNotify` commits quickly,
    // `getNotify` (re-fetched by `reload`) reads it back more slowly. That
    // gap is what the bug depended on — if `busy` clears as soon as the PUT
    // resolves, without waiting for the slower re-fetch to land, a second
    // click in that window reads the still-stale `notify.events` closure
    // and silently drops the first change. If `busy` only clears once
    // `reload()` has actually landed, that window cannot exist.
    const backend = {
      enabled: false,
      url_set: false,
      base_url: null as string | null,
      events: [] as string[],
    };
    vi.spyOn(api, "getNotify").mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ ...backend }), 40)),
    );
    const put = vi.spyOn(api, "putNotify").mockImplementation(
      (body) =>
        new Promise((resolve) =>
          setTimeout(() => {
            Object.assign(backend, body);
            resolve({ ...backend });
          }, 5),
        ),
    );
    renderAt("/settings/notify");

    const gateSwitch = await screen.findByRole("switch", { name: "gate_requested" });
    const humanSwitch = await screen.findByRole("switch", { name: "work_item_needs_human" });

    await userEvent.click(gateSwitch);
    // As soon as the switch is clickable again, click the other one — in
    // the broken version this lands after the PUT but before the re-fetch.
    await waitFor(() => expect(gateSwitch).not.toBeDisabled());
    await userEvent.click(humanSwitch);
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));

    const secondCall = put.mock.calls[1][0] as { events: string[] };
    expect(secondCall.events).toHaveLength(2);
    expect(secondCall.events).toEqual(
      expect.arrayContaining(["gate_requested", "work_item_needs_human"]),
    );
  });

  it("shows a failed enable next to the switch it belongs to, not next to an unrelated save row", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    vi.spyOn(api, "putNotify").mockRejectedValue(
      new Error("set a webhook URL before enabling notifications"),
    );
    renderAt("/settings/notify");

    const toggle = await screen.findByRole("switch", { name: /notifications/i });
    await userEvent.click(toggle);

    const channelRow = toggle.closest(".save-row") as HTMLElement;
    expect(
      await within(channelRow).findByText(/set a webhook url before enabling notifications/i),
    ).toBeInTheDocument();

    // The original bug report's actual claim: the message must not also
    // show up on the two unrelated SaveRows, which did nothing.
    const channelSection = toggle.closest("section") as HTMLElement;
    const urlRow = within(channelSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(within(urlRow).queryByText(/set a webhook url before enabling notifications/i)).toBeNull();

    const baseSection = screen.getByLabelText(/base url/i).closest("section") as HTMLElement;
    const baseRow = within(baseSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(within(baseRow).queryByText(/set a webhook url before enabling notifications/i)).toBeNull();
  });

  it("shows a failed event-switch save beside that switch, not beside the Channel switch", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: true,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    vi.spyOn(api, "putNotify").mockRejectedValue(new Error("could not save events"));
    renderAt("/settings/notify");

    const eventSwitch = await screen.findByRole("switch", { name: "gate_requested" });
    await userEvent.click(eventSwitch);

    const eventRow = eventSwitch.closest(".save-row") as HTMLElement;
    expect(await within(eventRow).findByText(/could not save events/i)).toBeInTheDocument();

    const channelToggle = screen.getByRole("switch", { name: /notifications/i });
    const channelRow = channelToggle.closest(".save-row") as HTMLElement;
    expect(within(channelRow).queryByText(/could not save events/i)).toBeNull();
    // the Channel switch's own on/off hint must be untouched by someone
    // else's error
    expect(within(channelRow).getByText(/on — Kraft will POST when a run stops/i)).toBeInTheDocument();
  });

  it("shows a successful save's confirmation only on the row that saved", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: "http://192.168.1.20:8765",
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");

    const baseField = (await screen.findByLabelText(/base url/i)) as HTMLInputElement;
    await userEvent.type(baseField, "http://192.168.1.20:8765");
    const baseSection = baseField.closest("section") as HTMLElement;
    await userEvent.click(within(baseSection).getByRole("button", { name: /^save$/i }));

    const baseRow = within(baseSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(await within(baseRow).findByText(/^saved$/i)).toBeInTheDocument();

    // the unrelated Channel hint and Webhook URL SaveRow must still show
    // their own defaults, not "saved" leaking over from the Base URL save.
    const channelToggle = screen.getByRole("switch", { name: /notifications/i });
    const channelRow = channelToggle.closest(".save-row") as HTMLElement;
    expect(within(channelRow).queryByText(/^saved$/i)).toBeNull();
    expect(within(channelRow).getByText(/off — no outbound traffic/i)).toBeInTheDocument();

    const channelSection = channelToggle.closest("section") as HTMLElement;
    const urlRow = within(channelSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(within(urlRow).queryByText(/^saved$/i)).toBeNull();
  });
});

describe("Settings · sidebar nav", () => {
  it("replaces the section in the URL rather than appending to it", async () => {
    renderAt("/settings/access");
    const link = await screen.findByRole("link", { name: "Auto-intake" });
    expect(link).toHaveAttribute("href", "/settings/intake");
  });
});
