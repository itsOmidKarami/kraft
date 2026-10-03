import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Policy, RepoProbe, TemplateSummary } from "../../types/settings";
import { FirstRun, PROBE_STEP_MS, savedFirstRun } from "./FirstRun";

const PROBE: RepoProbe = {
  path: "/code/acme", name: "acme", branch: "main", submodules: ["vendor/a", "vendor/b"], has_beads: true,
  beads_export_auto: false, beads_export_git_add: false, has_engineering: false,
  test_command: "uv run pytest -q", test_scopes: [{ paths: ["**"], command: "uv run pytest -q" }], setup_command: "uv sync", forge: "gitlab", project: "acme/acme",
};
const CHAIN = { id: "default", nodes: [...new Array(6).fill({}), { fix_loop: "verification.fix_loop" }], gates: 2 } as unknown as TemplateSummary;
const POLICY = { loops: { "verification.fix_loop": { attempts: 4, wall_clock_s: 60 } }, default: { attempts: 9, wall_clock_s: 60 }, max_concurrent: 1, budget: { work_item_usd: 25, daily_usd: null } } as Policy;

let calls: string[];
beforeEach(() => {
  // The probe reveals its rows on a clock; the tests play it out instead of waiting for it.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls = [];
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", bind: "127.0.0.1", port: 4317 } as never);
  vi.spyOn(api, "getTemplates").mockResolvedValue([CHAIN]);
  vi.spyOn(api, "getPolicy").mockResolvedValue(POLICY);
  vi.spyOn(api, "probeRepo").mockImplementation(async () => { calls.push("probe"); return PROBE; });
  vi.spyOn(api, "addRepo").mockImplementation(async () => { calls.push("add"); return {} as never; });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  localStorage.clear();
});

const mount = (onDone?: () => void) => render(<MemoryRouter><FirstRun onDone={onDone} /></MemoryRouter>);
const stepButton = (n: number) => screen.queryByRole("button", { name: new RegExp(`^Step ${n}:`) });
const setup = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

/** Waits for the probe's answer, then plays its reveal out, one row per step. */
async function reveal() {
  await screen.findByRole("list", { name: "Probe results" });
  for (let i = 0; i < 10 && screen.queryAllByText("checking…").length; i++) await tick(PROBE_STEP_MS);
  expect(screen.getByRole("button", { name: "Add repo" })).toBeEnabled();
}

/** Probe and add, staying on the step that says what was added. */
async function add(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
  await user.click(screen.getByRole("button", { name: "+ Add repo" }));
  await reveal();
  await user.click(screen.getByRole("button", { name: "Add repo" }));
  await screen.findByText("Added acme");
}

async function probeAndAdd(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
  await user.click(screen.getByRole("button", { name: "+ Add repo" }));
  await reveal();
  await user.click(screen.getByRole("button", { name: "Add repo" }));
  await user.click(await screen.findByRole("button", { name: "Continue" }));
}

describe("FirstRun", () => {
  it("names the address the server is on", async () => {
    mount();
    expect(await screen.findByText("127.0.0.1:4317")).toBeInTheDocument();
  });

  it("says a repo with no commit cannot be added yet", async () => {
    vi.mocked(api.probeRepo).mockResolvedValue({ ...PROBE, read_from: null });
    const user = userEvent.setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    expect(await screen.findByText(/the repo has no commit yet/, undefined, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add repo" })).toBeDisabled();
  });

  // R10a-01: the reason sits by the button it turns off, and Check again reads the
  // repo again (once it has a commit) without the path being edited.
  it("says by Add repo why a repo with no commit cannot be added, and checks it again in place", async () => {
    vi.mocked(api.probeRepo).mockResolvedValueOnce({ ...PROBE, read_from: null });
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    await screen.findByRole("list", { name: "Probe results" });
    for (let i = 0; i < 12 && screen.queryAllByText("checking…").length; i++) await tick(PROBE_STEP_MS);
    const addButton = screen.getByRole("button", { name: "Add repo" });
    expect(addButton).toBeDisabled();
    expect(addButton).toHaveAccessibleDescription("No commit yet: a work item's branch starts from one. Commit its files, then press Check again.");
    await user.click(screen.getByRole("button", { name: "Check again" }));
    await reveal();
    expect(api.probeRepo).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.probeRepo).mock.calls[1][0]).toBe("/code/acme");
    expect(screen.queryByText(/No commit yet/)).toBeNull();
    // Check again went with the re-probe: focus is back in the path field, not on the page (review L1).
    expect(screen.getByLabelText(/Path to a local git checkout/)).toHaveFocus();
  });

  it("reads a repo with no commit again on Enter in the field", async () => {
    vi.mocked(api.probeRepo).mockResolvedValueOnce({ ...PROBE, read_from: null });
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    await screen.findByRole("list", { name: "Probe results" });
    for (let i = 0; i < 12 && screen.queryAllByText("checking…").length; i++) await tick(PROBE_STEP_MS);
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "{Enter}");
    await reveal();
    expect(api.probeRepo).toHaveBeenCalledTimes(2);
    expect(api.addRepo).not.toHaveBeenCalled();
  });

  it("probes first, reveals one row at a time, then adds with the probed values", async () => {
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    expect(await screen.findAllByText("checking…")).toHaveLength(5);
    expect(api.addRepo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Add repo" })).toBeDisabled();
    await tick(PROBE_STEP_MS / 3);
    expect(screen.getAllByText("checking…")).toHaveLength(5);
    await tick(PROBE_STEP_MS);
    expect(screen.getAllByText("checking…")).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Add repo" })).toBeDisabled();
    await reveal();
    expect(screen.getByText("2 submodules")).toBeInTheDocument();
    expect(screen.getByText("uv run pytest -q")).toBeInTheDocument();
    expect(screen.getByText("uv sync")).toBeInTheDocument();
    expect(screen.getByText("gitlab · acme/acme")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add repo" }));
    await waitFor(() => expect(api.addRepo).toHaveBeenCalledTimes(1));
    expect(calls).toEqual(["probe", "add"]);
    expect(api.addRepo).toHaveBeenCalledWith({
      path: "/code/acme", name: "acme", default_chain: "default", test_command: "uv run pytest -q",
      forge: "gitlab", project: "acme/acme",
    });
    expect(await screen.findByText("Added acme")).toBeInTheDocument();
  });

  it("says where each proposed command came from and how many others it found", async () => {
    const cand = { dir: "", tier: "runner", marker: "justfile", detector: "just", family: null, corroborated: false } as const;
    vi.spyOn(api, "probeRepo").mockResolvedValue({
      ...PROBE,
      test_command: "just test",
      setup_command: "just setup",
      candidates: [
        { ...cand, role: "test", command: "just test", source: "justfile recipe `test`", chosen: true },
        { ...cand, role: "setup", command: "just setup", source: "justfile recipe `setup`", chosen: true },
        { ...cand, role: "test", command: "uv run pytest", source: "uv.lock", tier: "toolchain", chosen: false },
      ],
    });
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    await reveal();
    expect(screen.getByText("just test — from justfile recipe `test`")).toBeInTheDocument();
    expect(screen.getByText("just setup — from justfile recipe `setup`")).toBeInTheDocument();
    expect(screen.getByText("uv run pytest (uv.lock)")).toBeInTheDocument();
  });

  it("sends no root scope for a single-stack repo: it would shadow later test command edits", async () => {
    const user = setup();
    mount();
    await probeAndAdd(user);
    expect(vi.mocked(api.addRepo).mock.calls[0][0]).not.toHaveProperty("test_scopes");
  });

  it("shows a monorepo's nested scopes, and for its unprepared ones no 'No setup needed'", async () => {
    const scopes = [{ paths: ["pyproject.toml", "src/**"], command: "uv run pytest -q" }, { paths: ["frontend/**"], command: "sh -c 'cd frontend && npm test'" }];
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...PROBE, test_scopes: scopes, setup_command: null, missing_setup: ["frontend"] });
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: true, setup_command: null } as never);
    const user = setup();
    mount();
    await add(user);
    expect(screen.getByText("sh -c 'cd frontend && npm test'")).toBeInTheDocument();
    const said = await screen.findByText(/frontend\/ has tests and nothing found prepares it/);
    expect(said).not.toHaveTextContent("No setup needed");
  });

  it("reads the repo again when you come back, rather than keep a fixed problem", async () => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...PROBE, test_command: null, test_scopes: [], setup_command: null });
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: false, setup_command: null } as never);
    const user = setup();
    mount();
    await add(user);
    expect(screen.getByText(/set its test command and Enable it/)).toBeInTheDocument();
    expect(screen.getByText(/No setup command found/)).toBeInTheDocument();
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/code/acme", enabled: true, setup_command: "uv sync" }] } as never);
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(screen.queryByText(/No setup command found/)).not.toBeInTheDocument());
    expect(screen.queryByText(/set its test command and Enable it/)).not.toBeInTheDocument();
  });

  it("reads the repo again on arriving back by an in-app link, with no focus event", async () => {
    // Where the wizard got to before its Settings › Repos link: no setup command then.
    localStorage.setItem("kraft.firstRun", JSON.stringify({ step: 1, reached: 1, path: "/code/acme", name: "acme", disabled: false, noSetup: true, repoPath: "/code/acme", missing: [] }));
    const repos = vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/code/acme", enabled: true, setup_command: "" }] } as never);
    mount();
    await waitFor(() => expect(repos).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/No setup command found/)).not.toBeInTheDocument());
    expect(screen.getByText("Added acme")).toBeInTheDocument();
  });

  it("sends the probe's scopes when it found a nested one", async () => {
    const scopes = [{ paths: ["pyproject.toml", "src/**"], command: "uv run pytest -q" }, { paths: ["frontend/**"], command: "npm test" }];
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...PROBE, test_scopes: scopes });
    const user = setup();
    mount();
    await probeAndAdd(user);
    expect(api.addRepo).toHaveBeenCalledWith(expect.objectContaining({ test_scopes: scopes }));
  });

  it("adds a repo with no detected test command, and says it is disabled until one is set", async () => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...PROBE, test_command: null, test_scopes: [] });
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: false } as never);
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    await reveal();
    await user.click(screen.getByRole("button", { name: "Add repo" }));
    expect(await screen.findByText("Added acme")).toBeInTheDocument();
    expect(vi.mocked(api.addRepo).mock.calls[0][0]).not.toHaveProperty("test_scopes");
    // R8b-02: setting the command is not enough while the repo is disabled.
    expect(screen.getByText(/set its test command and Enable it, then publish/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings › Repos" })).toHaveAttribute("href", "/settings/repos");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(screen.getByText(/acme is disabled, so New work item cannot file to it yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings › Repos" })).toHaveAttribute("href", "/settings/repos");
  });

  it("says nothing about a disabled repo when the server enabled it", async () => {
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: true } as never);
    const user = setup();
    mount();
    await probeAndAdd(user);
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(screen.queryByText(/disabled/)).toBeNull();
  });

  it("keeps step 1 and says why when the probe fails", async () => {
    vi.spyOn(api, "probeRepo").mockRejectedValue(new Error("Not a git repository: /nope"));
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/nope");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not a git repository: /nope");
    expect(screen.getByRole("button", { name: "+ Add repo" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Connect a repo" })).toBeInTheDocument();
    expect(api.addRepo).not.toHaveBeenCalled();
  });

  it("step 2 shows the numbers the server has, and reads…/errors without inventing any", async () => {
    const user = setup();
    mount();
    await probeAndAdd(user);
    expect(await screen.findByText("default · 7 nodes, 2 gates")).toBeInTheDocument();
    expect(screen.getByText("4 fix attempts, $25 per item")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Chains" })).toHaveAttribute("href", "/templates/chains");
  });

  it("step 2 says reading… while loading and gives no number on failure", async () => {
    vi.spyOn(api, "getTemplates").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getPolicy").mockRejectedValue(new Error("down"));
    const user = setup();
    mount();
    await probeAndAdd(user);
    expect(await screen.findByText("reading…")).toBeInTheDocument();
    expect(await screen.findByText("Could not read the policy.")).toBeInTheDocument();
    expect(screen.queryByText(/fix attempts/)).toBeNull();
  });

  it("says when the probe found no setup command", async () => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...PROBE, setup_command: null });
    const user = setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    await reveal();
    expect(screen.getByText("Setup command").nextSibling).toHaveTextContent("none found");
  });

  it("says the first item stops until a setup command is set when the server saved none", async () => {
    vi.spyOn(api, "probeRepo").mockResolvedValue({ ...PROBE, setup_command: null });
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: true, setup_command: null } as never);
    const user = setup();
    mount();
    await probeAndAdd(user);
    await user.click(stepButton(1)!);
    expect(screen.getByText(/No setup command found: its first work item stops before it starts until you set one, or tick No setup needed/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings › Repos" })).toHaveAttribute("href", "/settings/repos");
  });

  it("says nothing about setup when the server saved a setup command", async () => {
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: true, setup_command: "uv sync" } as never);
    const user = setup();
    mount();
    await probeAndAdd(user);
    await user.click(stepButton(1)!);
    expect(screen.getByText("Added acme")).toBeInTheDocument();
    expect(screen.queryByText(/No setup command found/)).toBeNull();
  });

  it("step 2 names where Settings writes without assuming the default home", async () => {
    const user = setup();
    mount();
    await probeAndAdd(user);
    expect(screen.getByText(/YAML in \$KRAFT_HOME\/config/)).toBeInTheDocument();
  });

  it("step 3 opens the board's composer and says Claude workers need the plugin, or admin init", async () => {
    const user = setup();
    mount();
    await probeAndAdd(user);
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(screen.getByRole("link", { name: /New work item/ })).toHaveAttribute("href", "/?new=1");
    expect(screen.getByText(/Claude workers need Kraft's MCP server, or Kraft refuses to launch them/)).toBeInTheDocument();
    expect(screen.getByText(/without the plugin, run/)).toHaveTextContent("kraft admin init");
    expect(screen.getByText(/open a Claude Code session in your repo and run/)).toHaveTextContent("/kraft:onboard");
    await user.click(screen.getByRole("button", { name: /Copy commands/ }));
    expect(await screen.findByRole("button", { name: /Copied/ })).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toBe(
      "claude plugin marketplace add itsOmidKarami/kraft\nclaude plugin install kraft@kraft",
    );
  });

  it("comes back at the step it reached, with the repo it added, until its last step is done or skipped", async () => {
    vi.spyOn(api, "addRepo").mockResolvedValue({ enabled: false } as never);
    const user = setup();
    const first = mount();
    expect(savedFirstRun()).toBeNull();
    await probeAndAdd(user);
    first.unmount();
    // A reload, or a visit to Settings › Repos from step 1, mounts it again.
    const second = mount();
    expect(screen.getByRole("heading", { name: "Chain and policy" })).toBeInTheDocument();
    await user.click(stepButton(1)!);
    expect(screen.getByText("Added acme")).toBeInTheDocument();
    expect(screen.getByLabelText(/Path to a local git checkout/)).toHaveValue("/code/acme");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    second.unmount();
    const onDone = vi.fn();
    mount(onDone);
    expect(screen.getByRole("heading", { name: /register Kraft with Claude Code/ })).toBeInTheDocument();
    expect(screen.getByText(/acme is disabled, so New work item cannot file to it yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Go to the board" }));
    expect(onDone).toHaveBeenCalled();
    expect(savedFirstRun()).toBeNull();
  });

  it("is done once its last step opens the composer", async () => {
    const onDone = vi.fn();
    const user = setup();
    mount(onDone);
    await probeAndAdd(user);
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(savedFirstRun()).toMatchObject({ step: 3, name: "acme" });
    await user.click(screen.getByRole("link", { name: /New work item/ }));
    expect(onDone).toHaveBeenCalled();
    expect(savedFirstRun()).toBeNull();
  });

  it("makes only reached steps focusable", async () => {
    const user = setup();
    mount();
    expect(stepButton(1)).not.toBeNull();
    expect(stepButton(2)).toBeNull();
    expect(stepButton(3)).toBeNull();
    await probeAndAdd(user);
    expect(stepButton(2)).not.toBeNull();
    expect(stepButton(3)).toBeNull();
    await user.click(stepButton(1)!);
    expect(screen.getByRole("heading", { name: "Connect a repo" })).toBeInTheDocument();
  });
});
