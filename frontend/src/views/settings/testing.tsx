import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import * as api from "../../api";
import type { Repo, Theme } from "../../types";
import { Settings } from "./index";

/** A full `Repo`, defaults overridden per test. Called with no args it is the
 *  same fixture every earlier test relied on as a plain object. */
export const repo = (overrides: Partial<Repo> = {}): Repo => ({
  path: "/repo-a",
  name: "repo-a",
  default_chain_template: "default",
  test_command: "uv run pytest -q",
  test_scopes: null,
  forge: "github",
  project: "acme/repo-a",
  enabled: true,
  models: {},
  deny_tools: [],
  steering: [],
  local_files: [],
  managed: true,
  ...overrides,
});

export const policy = {
  loops: { "verify.fix_loop": { attempts: 3, wall_clock_s: 3600 } },
  default: { attempts: 3, wall_clock_s: 3600 },
  max_concurrent: 3,
  rate_limit_retries: 5,
  auto_escalate_stuck: true,
  auto_escalate_stuck_cap: 3,
  auto_escalate_delay_s: 0,
};

export const theme: Theme = {
  palette: "nocturne",
  mode: "dark",
  density: "compact",
  board: { group_by: "status", show_done: 5, open_in: "peek" },
};

export const access = {
  bind: "127.0.0.1",
  port: 8765,
  session_expiry_days: 7,
  password_set: false,
  auth_required: false,
  allowed_hosts: [],
};

/** Every mock a Settings page might need, at a state that renders cleanly.
 *  Call from each test file's own `beforeEach`; override individual spies
 *  per test as needed. */
export function setupSettingsMocks() {
  vi.restoreAllMocks();
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [repo()] });
  vi.spyOn(api, "getTemplates").mockResolvedValue([
    {
      id: "quick-task",
      gates: 0,
      error: null,
      nodes: [
        {
          id: "verify",
          kind: "exec",
          tasks: ["verify.main.test_changed_scopes"],
          gate_after: null,
          fix_loop: "verify.fix_loop",
        },
      ],
    },
  ]);
  vi.spyOn(api, "getPolicy").mockResolvedValue(policy);
  vi.spyOn(api, "getTheme").mockResolvedValue(theme);
  vi.spyOn(api, "getLibrary").mockResolvedValue({
    file: "~/.kraft/templates/library.yaml",
    text: "steering:\n  house-style:\n    instructions: prefer stdlib\n",
    components: [
      {
        id: "steering.house-style",
        kind: "steering",
        name: "house-style",
        definition: { instructions: "prefer stdlib\n" },
        used_by: [],
        issues: [],
      },
    ],
  });
  vi.spyOn(api, "getIntake").mockResolvedValue({
    enabled: false,
    interval_s: 300,
    max_concurrent: 1,
    priority_ceiling: 2,
    repos: [],
    repo_pickups: {},
    recent_pickups: [],
  });
  vi.spyOn(api, "getAccess").mockResolvedValue(access);
  vi.spyOn(api, "getAuthSessions").mockResolvedValue({ sessions: [] });
  vi.spyOn(api, "getNotify").mockResolvedValue({
    enabled: false,
    url_set: false,
    base_url: null,
    events: ["gate_requested", "work_item_needs_human"],
    last_test: null,
  });
}

export const renderAt = (path: string) =>
  render(
    <MemoryRouter
      initialEntries={[path]}
    >
      <Routes>
        <Route path="/settings/*" element={<Settings />} />
      </Routes>
    </MemoryRouter>,
  );
