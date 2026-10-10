import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import * as d from "../templates/draft/draftApi";
import { PolicyPage } from "./PolicyPage";
import { policyView, problem, RESOLVED } from "./policy/fixture";

let where = "";
function Where() {
  where = useLocation().pathname;
  return null;
}
const ok = <T,>(body: T, status = 200) => Promise.resolve({ status, body });

const mount = (path = "/settings/policy/limits") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/settings/policy" element={<PolicyPage />} />
          <Route path="/settings/policy/:section" element={<PolicyPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

const sent = () => vi.mocked(d.postOps).mock.calls.map((c) => c[2][0]);

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(policyView()));
  vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...policyView({}, RESOLVED, true), ops: [] }));
  vi.spyOn(http, "request").mockImplementation(((path: string) => {
    if (path === "/policy") return ok({ active_count: 3 });
    if (path === "/templates/chains") return ok([{ id: "default" }]);
    return ok({});
  }) as never);
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

const edit = async (name: RegExp | string, text: string) => {
  await userEvent.click(screen.getByRole("button", { name }));
  const input = screen.getByRole("textbox");
  await userEvent.clear(input);
  await userEvent.type(input, `${text}{Enter}`);
};

describe("Policy page: Limits", () => {
  it("redirects the bare path to Limits and draws the instance card and a card per scope", async () => {
    mount("/settings/policy");
    await screen.findByRole("region", { name: "work item" });
    expect(where).toBe("/settings/policy/limits");
    for (const name of ["instance", "work item", "nodes", "steps", "tasks"]) expect(screen.getByRole("region", { name })).toBeInTheDocument();
  });

  it("reads an unset maximum as no bound and an unset default as not set, never a number", async () => {
    mount();
    const wall = await screen.findByRole("group", { name: "work item wall clock" });
    expect(within(wall).getByRole("button", { name: /default, not set/ })).toBeInTheDocument();
    expect(within(wall).getByRole("button", { name: /maximum, no bound/ })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "work item running" })).getByRole("button", { name: /default, 480 min/ })).toBeInTheDocument();
  });

  it("names the level a maximum comes from when its own level sets none", async () => {
    mount();
    const nodes = await screen.findByRole("group", { name: "nodes running" });
    expect(within(nodes).getByText("from work item")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "work item running" })).queryByText(/^from /)).toBeNull();
  });

  it("sends set_value for a cell, writing its own level's key; blank sends null", async () => {
    mount();
    await screen.findByRole("region", { name: "tasks" });
    await edit(/^tasks running maximum, 240 min/, "200");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "limits", key: "maxima.tasks.time_cap_minutes", value: 200 }));
    await edit(/^tasks running default, 90 min/, "");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "limits", key: "defaults.tasks.time_cap_minutes", value: null }));
    await edit(/^per day, \$50/, "$60");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "limits", key: "budget.daily_usd", value: 60 }));
    await edit(/^outer ceiling, \$10/, "12");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "limits", key: "budget.work_item_usd", value: 12 }));
  });

  it("names the dollar cap that binds, from the server's answer", async () => {
    mount();
    expect(await screen.findByText(/the lower one wins: budget.work_item_usd \$10/)).toBeInTheDocument();
  });

  it("refuses a value that is not a number without sending it", async () => {
    mount();
    await screen.findByRole("region", { name: "tasks" });
    await edit(/^tasks running default, 90 min/, "lots");
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number above 0.");
    expect(d.postOps).not.toHaveBeenCalled();
  });

  it("draws the set-below-policy column from the server's lists, and marks one past the maximum", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({ problems: [problem({ message: "implement 120 is above the maximum 90", scope: "limits", level: "tasks", field: "maxima.tasks.time_cap_minutes" })] }, { ...RESOLVED, limits: { ...RESOLVED.limits, caps: { ...RESOLVED.limits.caps, tasks: { ...RESOLVED.limits.caps.tasks, time_cap_minutes: { ...RESOLVED.limits.caps.tasks.time_cap_minutes, below: [{ layer: "library", chain: "default", path: "i", via: "library:tasks.implementer", value: 120, exceeds: true }] } } } } }, true)));
    mount();
    const row = await screen.findByRole("group", { name: "tasks running" });
    expect(within(row).getByText("implementer 120 min · library !")).toHaveClass("is-bad");
    expect(screen.getByRole("region", { name: "tasks" })).toHaveTextContent("1 problem");
    expect(within(row).getByRole("button", { name: /maximum, 240 min/ })).toHaveClass("is-bad");
  });

  it("nests each scope inside the one it can't exceed, under a Limits heading with Preview beside it (ST-1)", async () => {
    mount();
    const tasks = await screen.findByRole("region", { name: "tasks" });
    const chain = ["tasks", "steps", "nodes", "work item", "instance"].map((name) => screen.getByRole("region", { name }));
    chain.slice(1).forEach((outer, i) => expect(outer).toContainElement(chain[i]));
    expect(tasks).toBeInTheDocument();
    const head = screen.getByRole("heading", { name: "Limits" }).parentElement!;
    expect(within(head).getByRole("button", { name: "Preview on a chain" })).toBeInTheDocument();
  });

  it("draws no below column and no Preview button when the server sends no below lists", async () => {
    const bare = JSON.parse(JSON.stringify(RESOLVED));
    for (const l of Object.values(bare.limits.caps) as Record<string, { below?: unknown }>[]) for (const c of Object.values(l)) delete c.below;
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({}, bare)));
    mount();
    await screen.findByRole("region", { name: "tasks" });
    expect(screen.queryByText("set below policy")).toBeNull();
    expect(screen.queryByRole("button", { name: "Preview on a chain" })).toBeNull();
  });

  it("shows the published value struck through beside a changed one", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({ changes: [{ path: "maxima.tasks.time_cap_minutes", kind: "change", summary: "240 → 200", file: "policy.yaml" }] }, RESOLVED, true)));
    mount();
    const row = await screen.findByRole("group", { name: "tasks running" });
    expect(within(row).getByText("240", { selector: "s" })).toBeInTheDocument();
  });
});

describe("Policy page: Loops", () => {
  it("sends the default: block's keys, a per-loop entry through set_loop in seconds, and removes an entry", async () => {
    mount("/settings/policy/loops");
    await screen.findByRole("region", { name: "Every fix loop" });
    await edit(/^default loop attempts, 3/, "4");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "loops", key: "default.attempts", value: 4 }));
    await edit(/^default loop wall clock, 60 min/, "90");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "loops", key: "default.wall_clock_s", value: 5400 }));
    await edit(/^verification attempts, 2/, "5");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_loop", key: "verification.fix_loop", max_attempts: 5 }));
    await edit(/^verification wall clock, 60 min/, "30");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_loop", key: "verification.fix_loop", wall_clock_s: 1800 }));
    await userEvent.click(screen.getByRole("button", { name: "Remove the loops entry for verification" }));
    expect(sent()).toContainEqual({ op: "remove_loop", key: "verification.fix_loop" });
  });

  it("lists an entry that names no loop under its own heading with the server's problem and Remove", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({ problems: [problem({ field: "loops.typo.fix_loop", scope: "loops", message: "loops.typo.fix_loop names no fix loop in the library" })] }, RESOLVED, true)));
    mount("/settings/policy/loops");
    const stray = await screen.findByRole("group", { name: "Entry typo.fix_loop" });
    expect(stray).toHaveTextContent("names no fix loop in the library");
    await userEvent.click(within(stray).getByRole("button", { name: /Remove/ }));
    expect(sent()).toContainEqual({ op: "remove_loop", key: "typo.fix_loop" });
  });

  it("toggles the three finding severities and sends the whole list", async () => {
    mount("/settings/policy/loops");
    const chips = await screen.findByRole("group", { name: "Findings that burn a fix cycle" });
    expect(within(chips).getAllByRole("button").map((b) => b.textContent)).toEqual(["critical", "important", "minor"]);
    await userEvent.click(within(chips).getByRole("button", { name: "minor" }));
    expect(sent()).toContainEqual({ op: "set_value", scope: "findings", key: "findings.loop_severities", value: ["critical", "important", "minor"] });
    await userEvent.click(within(chips).getByRole("button", { name: "critical" }));
    expect(sent().at(-1)).toMatchObject({ value: ["important"] });
  });

  it("sends the auto-escalate keys, and hides their rows while it is off", async () => {
    mount("/settings/policy/loops");
    await screen.findByRole("region", { name: "When an item is stuck" });
    await edit(/^max auto-escalations per item, 3/, "4");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "escalation", key: "auto_escalate_stuck_cap", value: 4 }));
    await edit(/^delay before escalating, 0 s/, "30");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "escalation", key: "auto_escalate_delay_s", value: 30 }));
    await userEvent.click(screen.getByRole("button", { name: "off" }));
    expect(sent()).toContainEqual({ op: "set_value", scope: "escalation", key: "auto_escalate_stuck", value: false });
    document.body.innerHTML = "";
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({}, { ...RESOLVED, escalation: { ...RESOLVED.escalation, auto_escalate_stuck: { value: false, source: "policy" } } })));
    mount("/settings/policy/loops");
    await screen.findByRole("region", { name: "When an item is stuck" });
    expect(screen.queryByText("max per item")).toBeNull();
    expect(screen.getByText(/Off: a stuck item waits/)).toBeInTheDocument();
  });

  it("sets the loop attempts and wall clock bounds through limits keys", async () => {
    mount("/settings/policy/loops");
    await screen.findByRole("group", { name: "Attempts" });
    await edit(/^attempts maximum, no bound/, "6");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "limits", key: "maxima.max_attempts", value: 6 }));
    await edit(/^wall clock default, 60 min/, "45");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "limits", key: "defaults.timeout_minutes", value: 45 }));
  });
});

describe("Policy page: Housekeeping", () => {
  it("sends max_concurrent, archive after, rate-limit relaunches and the forge timeout", async () => {
    mount("/settings/policy/housekeeping");
    await screen.findByRole("region", { name: "Runs" });
    await edit(/^max active items, 5/, "7");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "housekeeping", key: "max_concurrent", value: 7 }));
    await edit(/^rate-limit relaunches, 5/, "6");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "retries", key: "rate_limit_retries", value: 6 }));
    await edit(/^archive after, 30 days/, "");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "housekeeping", key: "archive.after_days", value: null }));
    await edit(/^forge call timeout, 120 s/, "60");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "retries", key: "forge_cli_timeout_s", value: 60 }));
  });

  it("sends the storage limit and quota as sizes, and refuses a bare number", async () => {
    mount("/settings/policy/housekeeping");
    await screen.findByRole("region", { name: "Storage" });
    await edit(/^limit, no limit/, "10g");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "housekeeping", key: "storage.worktrees.limit", value: "10G" }));
    await edit(/^quota, not set/, "5G");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "housekeeping", key: "storage.worktrees.quota", value: "5G" }));
  });

  it("reads automatic clean-up as off, or as its age and what it does, and sends an age", async () => {
    mount("/settings/policy/housekeeping");
    await screen.findByRole("region", { name: "Storage" });
    expect(screen.getByRole("button", { name: /^automatic clean-up, off/ })).toBeInTheDocument();
    await edit(/^automatic clean-up, off/, "2D");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_value", scope: "housekeeping", key: "storage.worktrees.auto_cleanup.min_age", value: "2d" }));
  });

  it("says what an age of 24h means", async () => {
    const on = { ...RESOLVED, housekeeping: { ...RESOLVED.housekeeping, storage_auto_cleanup: { value: "24h", source: "policy" } } };
    vi.spyOn(d, "getDraft").mockImplementation(() => ok(policyView({}, on as never)));
    mount("/settings/policy/housekeeping");
    expect(await screen.findByRole("button", { name: /^automatic clean-up, 24h/ })).toBeInTheDocument();
    expect(screen.getByText(/ended 24h ago or more/)).toBeInTheDocument();
  });

  it("says how many slots are in use, from the count the server gives", async () => {
    mount("/settings/policy/housekeeping");
    expect(await screen.findByText(/3 of 5 slots in use now/)).toBeInTheDocument();
  });
});

describe("Policy page: the section menu and problems", () => {
  const open = async () => {
    await screen.findByRole("region", { name: "work item" });
    await userEvent.click(screen.getByRole("button", { name: /^Limits/ }));
  };

  it("marks a section with a change amber and one with a problem red, and switches by route", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({
      changes: [{ path: "max_concurrent", kind: "change", summary: "5 → 7", file: "policy.yaml" }],
      problems: [problem({ scope: "limits", level: "tasks", field: "defaults.tasks.time_cap_minutes", message: "too high" })],
    }, RESOLVED, true)));
    mount();
    await open();
    const menu = screen.getByRole("menu");
    expect(within(within(menu).getByRole("menuitemradio", { name: /Limits/ })).getByRole("img", { name: "has a problem" })).toBeInTheDocument();
    expect(within(within(menu).getByRole("menuitemradio", { name: /Housekeeping/ })).getByRole("img", { name: "unpublished changes" })).toBeInTheDocument();
    expect(within(within(menu).getByRole("menuitemradio", { name: /Loops/ })).queryByRole("img")).toBeNull();
    await userEvent.click(within(menu).getByRole("menuitemradio", { name: /Loops/ }));
    expect(where).toBe("/settings/policy/loops");
  });

  it("puts a problem on its cell and its card, blocks Publish, and Fix → flashes the card", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({
      changes: [{ path: "maxima.tasks.time_cap_minutes", kind: "change", summary: "240 → 60", file: "policy.yaml" }],
      problems: [problem({ scope: "limits", level: "tasks", field: "defaults.tasks.time_cap_minutes", path: "defaults.tasks.time_cap_minutes", message: "tasks default 90 is above its maximum 60" })],
    }, RESOLVED, true)));
    mount("/settings/policy/housekeeping");
    await screen.findByRole("region", { name: "Runs" });
    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
    expect(await screen.findByRole("button", { name: "Publish" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Fix →" }));
    expect(where).toBe("/settings/policy/limits");
    const row = await screen.findByRole("group", { name: "tasks running" });
    expect(within(row).getByRole("button", { name: /default, 90 min/ })).toHaveClass("is-bad");
    expect(screen.getByRole("region", { name: "tasks" })).toHaveTextContent("tasks default 90 is above its maximum 60");
  });

  it("shows the first problem and still offers YAML while the file does not load", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(policyView({ problems: [problem({ message: "policy.yaml: not a mapping" })] }, null, true)));
    mount();
    expect(await screen.findByRole("heading", { name: "policy.yaml does not load" })).toBeInTheDocument();
    expect(screen.getByText("policy.yaml: not a mapping")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "YAML" })).toBeInTheDocument();
  });
});

describe("Policy page: Preview on a chain", () => {
  it("reads the chain's scopes under the draft and marks one past its maximum", async () => {
    vi.mocked(http.request).mockImplementation(((path: string) => {
      if (path === "/policy") return ok({ active_count: 3 });
      if (path === "/templates/chains") return ok([{ id: "default" }, { id: "docs_only" }]);
      if (path.startsWith("/drafts/policy/policy/preview")) return ok({ chain: "default", scopes: [{ path: "default", kind: "chain", level: "work_item", caps: { time_cap_minutes: { value: null, source: "default", maximum: null, exceeds: false } } }, { path: "implementation.main.implement", kind: "task", level: "tasks", caps: { time_cap_minutes: { value: 120, source: "library:tasks.implementer", maximum: { value: 90, level: "tasks" }, exceeds: true } } }] });
      return ok({});
    }) as never);
    mount();
    await screen.findByRole("region", { name: "tasks" });
    await userEvent.click(screen.getByRole("button", { name: "Preview on a chain" }));
    const pane = await screen.findByRole("complementary", { name: "On a chain pane" });
    expect(await within(pane).findByText("running 120 min · library implementer · exceeds 90 min")).toHaveClass("is-bad");
    expect(http.request).toHaveBeenCalledWith("/drafts/policy/policy/preview?chain=default");
  });
});
