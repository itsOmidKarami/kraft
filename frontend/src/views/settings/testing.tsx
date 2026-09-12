import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import * as api from "../../api";
import type { Theme } from "../../types";
import { Settings } from "./index";

export const repo = {
  path: "/repo-a",
  name: "repo-a",
  default_chain_template: "default",
  test_command: "uv run pytest -q",
  test_scopes: null as { paths: string[]; command: string }[] | null,
  forge: "github",
  project: "acme/repo-a",
  enabled: true,
  default_model: null,
  deny_tools: [],
  steering: [],
  allow_cross_repo: false,
  default_root_merge_policy: "bump" as const,
  submodules: [],
};

export const hooks = {
  "on.env.prepare": { kind: "builtin" as const, handler: "env_setup" },
  "on.implementation.start": { kind: "agent" as const, command: "claude" },
  "on.test.run": { kind: "subprocess" as const, command: ["pytest"] },
  "on.mr.open": { kind: "forge" as const, handler: "open_mr" },
};

export const policy = {
  loops: { verify_fix_loop: { attempts: 3, wall_clock_s: 3600 } },
  default: { attempts: 3, wall_clock_s: 3600 },
  max_concurrent: 3,
  rate_limit_retries: 5,
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
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/settings/*" element={<Settings />} />
      </Routes>
    </MemoryRouter>,
  );
