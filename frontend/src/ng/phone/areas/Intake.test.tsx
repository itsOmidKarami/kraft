import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { onLiveFrame } from "../../live";
import { INTAKE, intakeView } from "../../settings/intake/fixture";
import type { IntakeResolved } from "../../settings/intake/types";
import { IntakeScreen, ScheduleScreen, statusLine } from "./Intake";
import { mountAt, posts, problem, where } from "./testkit";

const CHECKS = [
  { id: 3, at: "2026-10-01T09:00:00Z", ready: 4, started: ["kraft-d71a"], skipped: [{ bead_id: "a", reason: "above_priority_ceiling" }, { bead_id: "b", reason: "above_priority_ceiling" }, { bead_id: "c", reason: "already_item" }] },
  { id: 2, at: "2026-10-01T08:55:00Z", ready: 3, started: [], skipped: [{ bead_id: "d", reason: "max_concurrent" }] },
];
const view = (resolved: Partial<IntakeResolved> = {}, r: Parameters<typeof intakeView>[0] = {}, draft = false) => intakeView(r, { ...INTAKE, ...resolved }, draft);
const OPS = (v = view()): [number, unknown] => [200, { ...v, ops: [] }];
const ans = (v = view(), more: Record<string, [number, unknown]> = {}) => ({
  "GET /drafts/intake/intake": [200, v] as [number, unknown],
  "GET /intake/checks": [200, CHECKS] as [number, unknown],
  "GET /repos": [200, { repos: [{ path: "/src/platform", name: "platform" }, { path: "/src/docs", name: "docs-site" }] }] as [number, unknown],
  "GET /templates/chains": [200, [{ id: "default" }, { id: "docs_only" }]] as [number, unknown],
  "POST /drafts/intake/intake/ops": OPS(),
  ...more,
});
const ops = (calls: ReturnType<typeof mountAt>["calls"]) => posts(calls).map((c) => (c.body as { ops: unknown[] }).ops[0]);
const main = (a = ans(), path = "/settings/auto-intake") => mountAt(<IntakeScreen />, path, "/settings/auto-intake", a);
const sched = (n = 0, a = ans()) => mountAt(<ScheduleScreen />, `/settings/auto-intake/schedules/${n}`, "/settings/auto-intake/schedules/:index", a);
afterEach(() => vi.unstubAllGlobals());

describe("Auto-intake (N.2)", () => {
  it("says what the rule does in one status line, On and Off", async () => {
    main();
    expect(await screen.findByText(/^On ·/)).toHaveTextContent("On · checking every 5 min, every enabled repo, P2 or above.");
    expect(statusLine({ ...INTAKE, enabled: false })).toMatch(/^Off · nothing is picked up automatically/);
    expect(statusLine({ ...INTAKE, repos: ["/a", "/b"], interval_s: 90 })).toContain("every 1.5 min, /a, /b");
  });

  it("an Off rule shows Off and no checks", async () => {
    main(ans(view({ enabled: false })));
    expect(await screen.findByText(/^Off ·/)).toBeInTheDocument();
    expect(screen.getByText("Auto-intake is off.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /enabled/ })).not.toBeChecked();
  });

  it("lists recent checks with each skip reason in its own words, and the empty state", async () => {
    main();
    const list = await screen.findByRole("region", { name: "Recent checks" });
    await waitFor(() => expect(list).toHaveTextContent("4 ready · started kraft-d71a · 2 above P2 · 1 already an item"));
    expect(list).toHaveTextContent("3 ready · none started · 1 left: max at a time reached");
  });

  it("says so when there are no checks yet", async () => {
    main(ans(view(), { "GET /intake/checks": [200, []] }));
    expect(await screen.findByText("No checks yet.")).toBeInTheDocument();
  });

  it("a live intake_checked frame is prepended, once", async () => {
    main();
    const list = await screen.findByRole("region", { name: "Recent checks" });
    await waitFor(() => expect(list).toHaveTextContent("3 ready"));
    const frame = { id: 4, at: "2026-10-01T10:10:00Z", ready: 2, started: ["kraft-new"], skipped: [] };
    act(() => onLiveFrame({ type: "intake_checked", payload: frame }));
    expect(list.querySelectorAll(".ph-row")).toHaveLength(3);
    expect(list.querySelector(".ph-row")).toHaveTextContent("2 ready · started kraft-new");
    act(() => onLiveFrame({ type: "intake_checked", payload: frame }));
    expect(list.querySelectorAll(".ph-row")).toHaveLength(3);
  });

  it("refetches the checks every 30 seconds and on focus, as the fallback for a frame it missed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { calls } = main();
      await screen.findByRole("region", { name: "Recent checks" });
      const n = () => calls.filter((c) => c.path === "/intake/checks").length;
      const before = n();
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(n()).toBe(before + 1);
      act(() => void window.dispatchEvent(new Event("focus")));
      expect(n()).toBe(before + 2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("edits the pickup rule: the interval in minutes becomes seconds, a priority becomes the ceiling", async () => {
    const { calls } = main();
    await userEvent.click(await screen.findByRole("button", { name: /^check every/ }));
    const box = screen.getByLabelText("Check every", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "0.1{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("more often than every 30 seconds");
    await userEvent.clear(box);
    await userEvent.type(box, "2{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_intake", patch: { interval_s: 120 } }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /^priority at or below/ }));
    await userEvent.click(screen.getByRole("radio", { name: "P3" }));
    await waitFor(() => expect(ops(calls)[1]).toEqual({ op: "set_intake", patch: { priority_ceiling: 3 } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts(calls)).toHaveLength(2);
  });

  it("the enabled switch and the repos list send set_intake", async () => {
    const { calls } = main();
    await userEvent.click(await screen.findByRole("switch", { name: /enabled/ }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_intake", patch: { enabled: false } }]));
    await userEvent.click(screen.getByRole("button", { name: /^repos/ }));
    await userEvent.type(screen.getByLabelText("Repos", { selector: "input" }), "/src/a, , /src/b,{Enter}");
    await waitFor(() => expect(ops(calls)[1]).toEqual({ op: "set_intake", patch: { repos: ["/src/a", "/src/b"] } }));
  });

  it("lists the schedules as cron, repo, chain and filed paused, and links each to its page", async () => {
    main();
    const row = await screen.findByRole("link", { name: /^Dependency check/ });
    expect(row).toHaveTextContent("weekdays 09:00 · platform · default · filed paused");
    expect(row).toHaveAttribute("href", "/settings/auto-intake/schedules/0");
  });

  it("marks a schedule with a problem", async () => {
    main(ans(view({}, { problems: [problem("schedules.0", "no such chain", { schedule: 0, field: "chain" })] })));
    expect(await screen.findByRole("link", { name: /^Dependency check/ })).toHaveTextContent("problem");
  });

  it("Add schedule sends add_schedule with the desktop's defaults and opens the new schedule", async () => {
    const two = view({ schedules: [...INTAKE.schedules, { index: 1, cron: "0 9 * * 1", repo: "/src/platform", chain: "default", title: "Scheduled item", description: "" }] }, {}, true);
    const { calls } = main(ans(view(), { "POST /drafts/intake/intake/ops": OPS(two) }));
    await userEvent.click(await screen.findByRole("button", { name: /Add schedule/ }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "add_schedule", cron: "0 9 * * 1", repo: "/src/platform", chain: "default", title: "Scheduled item" }]));
    await waitFor(() => expect(where()).toBe("/settings/auto-intake/schedules/1"));
  });

  it("with no schedules, says so", async () => {
    main(ans(view({ schedules: [] })));
    expect(await screen.findByText("No schedules yet.")).toBeInTheDocument();
  });
});

describe("a schedule (N.2)", () => {
  it("shows name, cron in words, repo, chain, description and that it is filed paused", async () => {
    sched();
    expect(await screen.findByRole("heading", { level: 1, name: "Dependency check" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^cron/ })).toHaveTextContent("0 9 * * 1-5");
    expect(screen.getByRole("button", { name: /^cron/ })).toHaveTextContent("weekdays 09:00");
    expect(screen.getByRole("button", { name: /^repo/ })).toHaveTextContent("platform");
    expect(screen.getByRole("button", { name: /^chain/ })).toHaveTextContent("default");
    expect(screen.getByRole("button", { name: /^description/ })).toHaveTextContent("Update pinned dependencies.");
    expect(screen.getByText("filed paused")).toBeInTheDocument();
  });

  it("each edit is a set_schedule patch of that one field", async () => {
    const { calls } = sched();
    await userEvent.click(await screen.findByRole("button", { name: /^cron/ }));
    const box = screen.getByLabelText("cron", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "30 6 * * *{Enter}");
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_schedule", index: 0, patch: { cron: "30 6 * * *" } }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /^repo/ }));
    await userEvent.click(screen.getByRole("radio", { name: "docs-site" }));
    await waitFor(() => expect(ops(calls)[1]).toEqual({ op: "set_schedule", index: 0, patch: { repo: "/src/docs" } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /^chain/ }));
    await userEvent.click(screen.getByRole("radio", { name: "docs_only" }));
    await waitFor(() => expect(ops(calls)[2]).toEqual({ op: "set_schedule", index: 0, patch: { chain: "docs_only" } }));
  });

  it("an empty name is refused before any call; an empty description is allowed", async () => {
    const { calls } = sched();
    await userEvent.click(await screen.findByRole("button", { name: /^name/ }));
    const box = screen.getByLabelText("name", { selector: "input,textarea" });
    await userEvent.clear(box);
    await userEvent.type(box, "{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot be empty");
    expect(posts(calls)).toEqual([]);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(await screen.findByRole("button", { name: /^description/ }));
    await userEvent.clear(screen.getByLabelText("description", { selector: "input,textarea" }));
    await userEvent.click(screen.getByRole("button", { name: "Set" }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "set_schedule", index: 0, patch: { description: "" } }]));
  });

  it("shows the server's problem on the field it is about", async () => {
    sched(0, ans(view({}, { problems: [problem("schedules.0", "chain nope does not exist", { schedule: 0, field: "chain" })] })));
    expect(await screen.findByRole("button", { name: /^chain/ })).toHaveTextContent("chain nope does not exist");
  });

  it("Remove schedule asks first, then sends remove_schedule and goes back to Auto-intake", async () => {
    const two = view({ schedules: [...INTAKE.schedules, { index: 1, cron: "0 6 * * *", repo: "/src/docs", chain: "docs_only", title: "Docs sweep", description: "" }] });
    const { calls } = sched(1, ans(two));
    await userEvent.click(await screen.findByRole("button", { name: "Remove schedule" }));
    const dialog = await screen.findByRole("dialog");
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove schedule" }));
    await waitFor(() => expect(ops(calls)).toEqual([{ op: "remove_schedule", index: 1 }]));
    await waitFor(() => expect(where()).toBe("/settings/auto-intake"));
  });

  it("a schedule that is not there says so", async () => {
    sched(9);
    expect(await screen.findByText("There is no schedule 9.")).toBeInTheDocument();
  });
});
