import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { policyView, problem, RESOLVED } from "../../settings/policy/fixture";
import type { PolicyResolved } from "../../settings/policy/types";
import { PolicyScreen } from "./Policy";
import { mountAt, posts, where } from "./testkit";

const OPS: [number, unknown] = [200, { ...policyView({}, RESOLVED, true), ops: [] }];
const ans = (v = policyView(), more: Record<string, [number, unknown]> = {}) => ({ "GET /drafts/policy/policy": [200, v] as [number, unknown], "GET /policy": [200, { active_count: 2 }] as [number, unknown], "POST /drafts/policy/policy/ops": OPS, ...more });
const ops = (calls: ReturnType<typeof mountAt>["calls"]) => posts(calls).map((c) => (c.body as { ops: unknown[] }).ops[0]);
const open = (section = "limits", a = ans()) => mountAt(<PolicyScreen />, `/settings/policy/${section}`, "/settings/policy/:section", a);
const resolved = (over: Record<string, unknown>) => ({ ...RESOLVED, ...over }) as unknown as PolicyResolved;
afterEach(() => vi.unstubAllGlobals());

describe("Policy limits (N.1)", () => {
  it("reads every number from the server's resolve: Instance, then a group per scope with default and max", async () => {
    open();
    expect(await screen.findByRole("tab", { name: "Limits", selected: true })).toBeInTheDocument();
    const instance = screen.getByRole("region", { name: "Instance" });
    expect(within(instance).getByRole("button", { name: /^per day/ })).toHaveTextContent("$50");
    expect(within(instance).getByRole("button", { name: /^per item/ })).toHaveTextContent("$10");
    const work = screen.getByRole("region", { name: "work item" });
    expect(within(work).getByRole("button", { name: /^running/ })).toHaveTextContent("default 480 min max 1440 min");
    expect(within(work).getByRole("button", { name: /^tokens/ })).toHaveTextContent("max 2,000,000");
    expect(within(screen.getByRole("region", { name: "nodes" })).getByRole("button", { name: /^running/ })).toHaveTextContent("max from work item");
  });

  it("says no bound for a null maximum, and does not draw 'set below policy'", async () => {
    open();
    const work = await screen.findByRole("region", { name: "work item" });
    expect(within(work).getByRole("button", { name: /^wall clock/ })).toHaveTextContent("default not set max no bound");
    expect(screen.queryByText(/below policy/i)).toBeNull();
  });

  it("edits a default through the choice sheet, then sends set_value as the desktop's cell does", async () => {
    const { calls } = open();
    await userEvent.click(within(await screen.findByRole("region", { name: "work item" })).getByRole("button", { name: /^running/ }));
    await userEvent.click(screen.getByRole("button", { name: /Edit default/ }));
    const box = screen.getByLabelText("running default", { selector: "input" });
    expect(box).toHaveValue("480");
    await userEvent.clear(box);
    await userEvent.type(box, "600{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "limits", key: "defaults.work_item.time_cap_minutes", value: 600 }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("edits a maximum, and Clear maximum (no bound) sends null", async () => {
    const { calls } = open();
    const work = await screen.findByRole("region", { name: "work item" });
    await userEvent.click(within(work).getByRole("button", { name: /^dollars/ }));
    await userEvent.click(screen.getByRole("button", { name: /Edit maximum/ }));
    await userEvent.type(screen.getByLabelText("dollars maximum", { selector: "input" }), "{Control>}a{/Control}30{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "limits", key: "maxima.work_item.budget_usd", value: 30 }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(within(work).getByRole("button", { name: /^tokens/ }));
    await userEvent.click(screen.getByRole("button", { name: /Clear maximum/ }));
    await waitFor(() => expect(ops(calls)[1]).toEqual({ op: "set_value", scope: "limits", key: "maxima.work_item.token_budget", value: null }));
  });

  it("a maximum that is not set offers no Clear", async () => {
    open();
    await userEvent.click(within(await screen.findByRole("region", { name: "work item" })).getByRole("button", { name: /^wall clock/ }));
    expect(screen.getByRole("button", { name: /Edit maximum/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Clear maximum/ })).toBeNull();
  });

  it("a number that is not a number is refused in the sheet before any call", async () => {
    const { calls } = open();
    await userEvent.click(within(await screen.findByRole("region", { name: "Instance" })).getByRole("button", { name: /^per day/ }));
    await userEvent.type(screen.getByLabelText("per day", { selector: "input" }), "abc{Enter}");
    // A dollar cell reads as the budget editor does, and says how to type an amount.
    expect(await screen.findByRole("alert")).toHaveTextContent("Type the amount plainly, like 1000 or 1.5.");
    expect(posts(calls)).toEqual([]);
  });

  it("a value the draft holds a problem on shows it on the row and blocks Publish", async () => {
    const v = policyView({ problems: [problem({ scope: "limits", field: "budget.daily_usd", message: "per day is below the work item cap" })], changes: [{ path: "budget.daily_usd", kind: "change", summary: "$50 → $5", file: "policy.yaml" }] }, RESOLVED, true);
    open("limits", ans(v));
    const row = within(await screen.findByRole("region", { name: "Instance" })).getByRole("button", { name: /^per day/ });
    expect(row).toHaveTextContent("per day is below the work item cap");
    expect(row).toHaveTextContent("problem");
    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
    expect(within(await screen.findByRole("dialog")).getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("a changed value says what it was", async () => {
    open("limits", ans(policyView({ changes: [{ path: "budget.daily_usd", kind: "change", summary: "$40 → $50", file: "policy.yaml" }] }, RESOLVED, true)));
    expect(await screen.findByRole("button", { name: /^per day/ })).toHaveTextContent("was $40");
  });

  it("the tab puts its section in the address", async () => {
    open();
    await userEvent.click(await screen.findByRole("tab", { name: "Housekeeping" }));
    expect(where()).toBe("/settings/policy/housekeeping");
  });
});

describe("Policy loops (N.1)", () => {
  it("lists the live loops with where each number comes from, and the stuck settings", async () => {
    open("loops");
    expect(await screen.findByRole("button", { name: /^verification · default/ })).toHaveTextContent("2 · 60 min");
    expect(screen.getByRole("button", { name: /^verification · default/ })).toHaveTextContent("loops:");
    expect(screen.getByRole("button", { name: /^merge_request_feedback · default/ })).toHaveTextContent("default:");
    expect(screen.getByRole("button", { name: /^escalations per item/ })).toHaveTextContent("3");
    expect(screen.getByRole("switch", { name: /auto-escalate/ })).toBeChecked();
  });

  it("a loop's own attempts are written with set_loop; a blank is refused", async () => {
    const { calls } = open("loops");
    await userEvent.click(await screen.findByRole("button", { name: /^merge_request_feedback · default/ }));
    await userEvent.click(screen.getByRole("button", { name: /Edit attempts/ }));
    const box = screen.getByLabelText("merge_request_feedback attempts", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot be blank");
    expect(posts(calls)).toEqual([]);
    await userEvent.type(box, "4{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_loop", key: "merge_request_feedback.fix_loop", max_attempts: 4 }]));
  });

  it("a loop's wall clock is typed in minutes and sent in seconds", async () => {
    const { calls } = open("loops");
    await userEvent.click(await screen.findByRole("button", { name: /^verification · default/ }));
    await userEvent.click(screen.getByRole("button", { name: /Edit wall clock/ }));
    const box = screen.getByLabelText("verification wall clock", { selector: "input" });
    expect(box).toHaveValue("60");
    await userEvent.clear(box);
    await userEvent.type(box, "5{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_loop", key: "verification.fix_loop", wall_clock_s: 300 }]));
  });

  it("removes a loop's entry with remove_loop, and a stray entry too", async () => {
    const { calls } = open("loops");
    await userEvent.click(await screen.findByRole("button", { name: /^verification · default/ }));
    await userEvent.click(screen.getByRole("button", { name: /Remove its entry/ }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "remove_loop", key: "verification.fix_loop" }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /^typo\.fix_loop/ }));
    await userEvent.click(screen.getByRole("button", { name: /Remove the entry/ }));
    await waitFor(() => expect(ops(calls)[1]).toEqual({ op: "remove_loop", key: "typo.fix_loop" }));
  });

  it("an entry with no loop of its own offers no Remove", async () => {
    open("loops");
    await userEvent.click(await screen.findByRole("button", { name: /^merge_request_feedback · default/ }));
    expect(screen.queryByRole("button", { name: /Remove its entry/ })).toBeNull();
  });

  it("a severity is a switch that sends the whole list, in order", async () => {
    const { calls } = open("loops");
    await userEvent.click(await screen.findByRole("switch", { name: "minor" }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "findings", key: "findings.loop_severities", value: ["critical", "important", "minor"] }]));
  });

  it("switching a severity that is on takes it out of the list", async () => {
    const { calls } = open("loops");
    await userEvent.click(await screen.findByRole("switch", { name: "critical" }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "findings", key: "findings.loop_severities", value: ["important"] }]));
  });

  it("turning auto-escalate off hides what only applies when it is on", async () => {
    open("loops", ans(policyView({}, resolved({ escalation: { auto_escalate_stuck: { value: false, source: "policy" } } }))));
    expect(await screen.findByRole("switch", { name: /auto-escalate/ })).not.toBeChecked();
    expect(screen.queryByRole("button", { name: /^escalations per item/ })).toBeNull();
  });
});

describe("Policy housekeeping (N.1)", () => {
  it("shows the live slot count with the concurrent items", async () => {
    open("housekeeping");
    expect(await screen.findByRole("button", { name: /^max active items/ })).toHaveTextContent("2 of 5 slots in use now");
    expect(screen.getByRole("button", { name: /^forge call timeout/ })).toHaveTextContent("120 s");
  });

  it("archive after: null reads never, zero is legal", async () => {
    const { calls } = open("housekeeping", ans(policyView({}, resolved({ housekeeping: { max_concurrent: { value: 5, source: "policy" }, archive_after_days: { value: null, source: null } } }))));
    const row = await screen.findByRole("button", { name: /^archive after/ });
    expect(within(row).getByText("never", { exact: true })).toBeInTheDocument();
    await userEvent.click(row);
    await userEvent.type(screen.getByLabelText("archive after", { selector: "input" }), "0{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "housekeeping", key: "archive.after_days", value: 0 }]));
  });

  it("storage: shows the limit and the 80% quota default, and sends a size", async () => {
    const { calls } = open("housekeeping", ans(policyView({}, resolved({ housekeeping: { max_concurrent: { value: 5, source: "policy" }, archive_after_days: { value: 30, source: "policy" }, storage_limit: { value: "10G", source: "policy" }, storage_quota: { value: null, source: "default" }, storage_quota_default: "8G" } }))));
    const limit = await screen.findByRole("button", { name: /^limit/ });
    expect(within(limit).getByText("10G", { exact: true })).toBeInTheDocument();
    const quota = screen.getByRole("button", { name: /^quota/ });
    expect(within(quota).getByText("8G", { exact: true })).toBeInTheDocument();
    await userEvent.click(quota);
    await userEvent.type(screen.getByLabelText("quota", { selector: "input" }), "5g{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "housekeeping", key: "storage.worktrees.quota", value: "5G" }]));
  });

  it("storage: automatic clean-up reads off, then its age, and sends an age", async () => {
    const housekeeping = (cleanup: unknown) => ({ max_concurrent: { value: 5, source: "policy" }, archive_after_days: { value: 30, source: "policy" }, storage_limit: { value: "10G", source: "policy" }, storage_quota: { value: null, source: "default" }, storage_quota_default: "8G", storage_auto_cleanup: cleanup });
    const { calls } = open("housekeeping", ans(policyView({}, resolved({ housekeeping: housekeeping({ value: null, source: "default" }) }))));
    const row = await screen.findByRole("button", { name: /^automatic clean-up/ });
    expect(within(row).getByText("off", { exact: true })).toBeInTheDocument();
    await userEvent.click(row);
    await userEvent.type(screen.getByLabelText("automatic clean-up", { selector: "input" }), "2d{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_value", scope: "housekeeping", key: "storage.worktrees.auto_cleanup.min_age", value: "2d" }]));
  });

  it("storage: a set clean-up age is the cell and says what it does", async () => {
    open("housekeeping", ans(policyView({}, resolved({ housekeeping: { max_concurrent: { value: 5, source: "policy" }, archive_after_days: { value: 30, source: "policy" }, storage_limit: { value: "10G", source: "policy" }, storage_quota: { value: null, source: "default" }, storage_quota_default: "8G", storage_auto_cleanup: { value: "24h", source: "policy" } } }))));
    const row = await screen.findByRole("button", { name: /^automatic clean-up/ });
    expect(within(row).getByText("24h", { exact: true })).toBeInTheDocument();
    expect(within(row).getByText(/ended 24h ago or more/)).toBeInTheDocument();
  });

  it("an unreadable policy says so and offers YAML", async () => {
    open("limits", ans(policyView({ problems: [problem({ message: "line 3: bad indent" })] }, null)));
    expect(await screen.findByRole("alert")).toHaveTextContent("policy.yaml does not load: line 3: bad indent");
  });

  it("an unknown section goes to Limits", async () => {
    open("nonsense");
    await screen.findByRole("tab", { name: "Limits", selected: true });
    expect(where()).toBe("/settings/policy/limits");
  });
});
