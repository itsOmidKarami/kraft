import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Policy, RepoProbe, TemplateSummary } from "../../types/settings";
import { FirstRun, PROBE_STEP_MS } from "./FirstRun";

const PROBE: RepoProbe = {
  path: "/code/acme", name: "acme", branch: "main", submodules: ["vendor/a", "vendor/b"], has_beads: true,
  beads_export_auto: false, beads_export_git_add: false, has_engineering: false,
  test_command: "uv run pytest -q", test_scopes: null, forge: "gitlab", project: "acme/acme",
};
const CHAIN = { id: "default", nodes: [...new Array(6).fill({}), { fix_loop: "verification.fix_loop" }], gates: 2 } as unknown as TemplateSummary;
const POLICY = { loops: { "verification.fix_loop": { attempts: 4, wall_clock_s: 60 } }, default: { attempts: 9, wall_clock_s: 60 }, max_concurrent: 1, budget: { work_item_usd: 25, daily_usd: null } } as Policy;

let calls: string[];
beforeEach(() => {
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
});

const mount = () => render(<MemoryRouter><FirstRun /></MemoryRouter>);
const stepButton = (n: number) => screen.queryByRole("button", { name: new RegExp(`^Step ${n}:`) });

async function probeAndAdd(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
  await user.click(screen.getByRole("button", { name: "+ Add repo" }));
  await screen.findByRole("list", { name: "Probe results" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Add repo" })).toBeEnabled(), { timeout: 3000 });
  await user.click(screen.getByRole("button", { name: "Add repo" }));
  await user.click(await screen.findByRole("button", { name: "Continue" }));
}

describe("FirstRun", () => {
  it("names the address the server is on", async () => {
    mount();
    expect(await screen.findByText("127.0.0.1:4317")).toBeInTheDocument();
  });

  it("probes first, reveals one row at a time, then adds with the probed values", async () => {
    const user = userEvent.setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/code/acme");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    expect(await screen.findAllByText("checking…")).toHaveLength(4);
    expect(api.addRepo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Add repo" })).toBeDisabled();
    await waitFor(() => expect(screen.getAllByText("checking…")).toHaveLength(3), { timeout: PROBE_STEP_MS * 3 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Add repo" })).toBeEnabled(), { timeout: 3000 });
    expect(screen.getByText("2 submodules")).toBeInTheDocument();
    expect(screen.getByText("uv run pytest -q")).toBeInTheDocument();
    expect(screen.getByText("gitlab · acme/acme")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add repo" }));
    await waitFor(() => expect(api.addRepo).toHaveBeenCalledTimes(1));
    expect(calls).toEqual(["probe", "add"]);
    expect(api.addRepo).toHaveBeenCalledWith({
      path: "/code/acme", name: "acme", default_chain_template: "default", test_command: "uv run pytest -q",
      test_scopes: null, forge: "gitlab", project: "acme/acme",
    });
    expect(await screen.findByText("Added acme")).toBeInTheDocument();
  });

  it("keeps step 1 and says why when the probe fails", async () => {
    vi.spyOn(api, "probeRepo").mockRejectedValue(new Error("Not a git repository: /nope"));
    const user = userEvent.setup();
    mount();
    await user.type(screen.getByLabelText(/Path to a local git checkout/), "/nope");
    await user.click(screen.getByRole("button", { name: "+ Add repo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not a git repository: /nope");
    expect(screen.getByRole("button", { name: "+ Add repo" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Connect a repo" })).toBeInTheDocument();
    expect(api.addRepo).not.toHaveBeenCalled();
  });

  it("step 2 shows the numbers the server has, and reads…/errors without inventing any", async () => {
    const user = userEvent.setup();
    mount();
    await probeAndAdd(user);
    expect(await screen.findByText("default · 7 nodes, 2 gates")).toBeInTheDocument();
    expect(screen.getByText("4 fix attempts, $25 per item")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Chains" })).toHaveAttribute("href", "/templates/chains");
  });

  it("step 2 says reading… while loading and gives no number on failure", async () => {
    vi.spyOn(api, "getTemplates").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getPolicy").mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    mount();
    await probeAndAdd(user);
    expect(await screen.findByText("reading…")).toBeInTheDocument();
    expect(await screen.findByText("Could not read the policy.")).toBeInTheDocument();
    expect(screen.queryByText(/fix attempts/)).toBeNull();
  });

  it("step 3 opens the board's composer and copies the agent command", async () => {
    const user = userEvent.setup();
    mount();
    await probeAndAdd(user);
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(screen.getByRole("link", { name: /New work item/ })).toHaveAttribute("href", "/?new=1");
    await user.click(screen.getByRole("button", { name: /Copy command/ }));
    expect(await screen.findByRole("button", { name: /Copied/ })).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toBe("kraft admin init");
  });

  it("makes only reached steps focusable", async () => {
    const user = userEvent.setup();
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
