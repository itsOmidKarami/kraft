import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import * as d from "../templates/draft/draftApi";
import { onLiveFrame } from "../live";
import { INTAKE, intakeView } from "./intake/fixture";
import { IntakePage } from "./IntakePage";

const ok = <T,>(body: T, status = 200) => Promise.resolve({ status, body });
const CHECKS = [
  { id: 3, at: "2026-10-01T10:05:00Z", ready: 4, started: ["kraft-d71a"], skipped: [{ bead_id: "a", reason: "above_priority_ceiling" }, { bead_id: "b", reason: "above_priority_ceiling" }, { bead_id: "c", reason: "already_item" }] },
  { id: 2, at: "2026-10-01T10:00:00Z", ready: 3, started: [], skipped: [{ bead_id: "d", reason: "max_concurrent" }] },
];

const mount = () =>
  render(
    <MemoryRouter initialEntries={["/settings/auto-intake"]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/settings/auto-intake" element={<IntakePage />} />
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
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(intakeView()));
  vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...intakeView({}, INTAKE, true), ops: [] }));
  vi.spyOn(http, "request").mockImplementation(((path: string) => {
    if (path.startsWith("/intake/checks")) return ok(CHECKS);
    if (path === "/repos") return ok({ repos: [{ path: "/src/platform", name: "platform" }, { path: "/src/product", name: "product" }] });
    if (path === "/templates/chains") return ok([{ id: "default" }, { id: "docs_only" }]);
    return ok({});
  }) as never);
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

/** The pane starts on its rail (AreaIntake paneOpen:false). */
const expand = async (name = "bd ready") => userEvent.click(await screen.findByRole("button", { name: `Expand ${name}` }));

const edit = async (name: RegExp | string, text: string) => {
  await userEvent.click(screen.getByRole("button", { name }));
  const input = screen.getByRole("textbox");
  await userEvent.clear(input);
  await userEvent.type(input, `${text}{Enter}`);
};

describe("Auto-intake page", () => {
  it("says what the rule does in a sentence, On and Off", async () => {
    mount();
    expect(await screen.findByText(/Every 5 minutes, starts ready beads at P2 and below from every enabled repo\. Each runs to its first gate and waits for you there\./)).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("button", { pressed: true })).toHaveTextContent("On");
    document.body.innerHTML = "";
    vi.mocked(d.getDraft).mockImplementation(() => ok(intakeView({}, { ...INTAKE, enabled: false })));
    mount();
    expect(await screen.findByText(/Nothing is picked up automatically/)).toBeInTheDocument();
    expect(screen.getByText("Auto-intake is off.")).toBeInTheDocument();
  });

  it("says one minute, not 1 minutes (R14b-08)", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(intakeView({}, { ...INTAKE, interval_s: 60 })));
    mount();
    expect(await screen.findByText(/^Every 1 minute, starts ready beads/)).toBeInTheDocument();
  });

  it("says when the next check is, from the last check and the interval, but not for an unpublished interval (ST-3)", async () => {
    const recent = [{ id: 9, at: new Date(Date.now() - 2 * 60_000).toISOString(), ready: 0, started: [], skipped: [] }];
    vi.mocked(http.request).mockImplementation(((path: string) => (path.startsWith("/intake/checks") ? ok(recent) : ok({ repos: [] }))) as never);
    mount();
    const status = () => within(screen.getByRole("main")).getByRole("button", { pressed: true }).querySelector(".ink-status")!;
    await waitFor(() => expect(status()).toHaveTextContent(/^On · next check in 3 min$/));
    document.body.innerHTML = "";
    vi.mocked(d.getDraft).mockImplementation(() => ok(intakeView({ changes: [{ path: "interval_s", kind: "change", summary: "300 → 120", file: "intake.yaml" }] }, INTAKE, true)));
    mount();
    await screen.findByText("0 ready · none started");
    expect(status()).toHaveTextContent(/^On$/);
  });

  it("counts the next check from a live check frame, not the last read's clock", async () => {
    const t0 = Date.parse("2026-10-01T10:00:00Z");
    const clock = vi.spyOn(Date, "now").mockReturnValue(t0);
    vi.mocked(http.request).mockImplementation(((path: string) => (path.startsWith("/intake/checks") ? ok([{ id: 1, at: new Date(t0 - 60_000).toISOString(), ready: 0, started: [], skipped: [] }]) : ok({ repos: [] }))) as never);
    mount();
    const status = () => within(screen.getByRole("main")).getByRole("button", { pressed: true }).querySelector(".ink-status")!;
    await waitFor(() => expect(status()).toHaveTextContent(/^On · next check in 4 min$/));
    clock.mockReturnValue(t0 + 3 * 60_000);
    act(() => onLiveFrame({ type: "intake_checked", payload: { id: 2, at: new Date(t0 + 3 * 60_000).toISOString(), ready: 0, started: [], skipped: [] } }));
    expect(status()).toHaveTextContent(/^On · next check in 5 min$/);
    clock.mockRestore();
  });

  it("lists recent checks with each skip reason in its own words, and the empty state", async () => {
    mount();
    const list = await screen.findByRole("region", { name: "Recent checks" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(2));
    expect(list).toHaveTextContent("4 ready · started kraft-d71a · 2 above P2 · 1 already an item");
    expect(list).toHaveTextContent("3 ready · none started · 1 left: max at a time reached");
    document.body.innerHTML = "";
    vi.mocked(http.request).mockImplementation(((path: string) => (path.startsWith("/intake/checks") ? ok([]) : ok({ repos: [] }))) as never);
    mount();
    expect(await screen.findByText("No checks yet.")).toBeInTheDocument();
  });

  it("prepends a live intake_checked frame to the list, once", async () => {
    mount();
    const list = await screen.findByRole("region", { name: "Recent checks" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(2));
    const frame = { id: 4, at: "2026-10-01T10:10:00Z", ready: 2, started: ["kraft-new"], skipped: [] };
    act(() => onLiveFrame({ type: "intake_checked", payload: frame }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("2 ready · started kraft-new");
    act(() => onLiveFrame({ type: "intake_checked", payload: frame }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  });

  it("still refetches the checks every 30 seconds, as the fallback for a frame it missed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mount();
      await screen.findByRole("region", { name: "Recent checks" });
      const calls = () => vi.mocked(http.request).mock.calls.filter((c) => String(c[0]).startsWith("/intake/checks")).length;
      const before = calls();
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(calls()).toBe(before + 1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("edits the pickup rule: the interval in minutes becomes seconds, priority P0-P4 the ceiling", async () => {
    mount();
    await screen.findByRole("region", { name: "Recent checks" });
    await expand();
    await edit(/^check every, minutes, 5 min/, "2");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_intake", patch: { interval_s: 120 } }));
    await userEvent.click(within(screen.getByRole("radiogroup", { name: "priority at or below" })).getByRole("radio", { name: "P4" }));
    expect(sent()).toContainEqual({ op: "set_intake", patch: { priority_ceiling: 4 } });
    await userEvent.click(within(screen.getByRole("radiogroup", { name: "enabled" })).getByRole("radio", { name: "no" }));
    expect(sent()).toContainEqual({ op: "set_intake", patch: { enabled: false } });
  });

  it("refuses an interval under 30 seconds inline without sending it", async () => {
    mount();
    await screen.findByRole("region", { name: "Recent checks" });
    await expand();
    await edit(/^check every, minutes, 5 min/, "0.25");
    expect(await screen.findByRole("alert")).toHaveTextContent("Checks cannot come more often than every 30 seconds.");
    expect(d.postOps).not.toHaveBeenCalled();
  });

  it("adds a schedule with the documented defaults and selects it", async () => {
    vi.mocked(d.postOps).mockImplementation(() => ok({ ...intakeView({}, { ...INTAKE, schedules: [...INTAKE.schedules, { index: 1, cron: "0 9 * * 1", repo: "/src/platform", chain: "default", title: "Scheduled item", description: "" }] }, true), ops: [] }));
    mount();
    await screen.findByRole("region", { name: "Schedules" });
    await userEvent.click(screen.getByRole("button", { name: /Add schedule/ }));
    await waitFor(() => expect(sent()).toContainEqual({ op: "add_schedule", cron: "0 9 * * 1", repo: "/src/platform", chain: "default", title: "Scheduled item" }));
    expect(await screen.findByRole("complementary", { name: "Scheduled item pane" })).toBeInTheDocument();
  });

  it("edits a schedule by its index, in intake.yaml's schedules, and removes it then selects the pickup card", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Dependency check/ }));
    expect(screen.getByText("schedule · schedules: in intake.yaml")).toBeInTheDocument();
    await edit(/^cron, 0 9 \* \* 1-5/, "0 8 * * *");
    await waitFor(() => expect(sent()).toContainEqual({ op: "set_schedule", index: 0, patch: { cron: "0 8 * * *" } }));
    await userEvent.selectOptions(screen.getByLabelText("repo"), "/src/product");
    expect(sent()).toContainEqual({ op: "set_schedule", index: 0, patch: { repo: "/src/product" } });
    await userEvent.selectOptions(screen.getByLabelText("chain"), "docs_only");
    expect(sent()).toContainEqual({ op: "set_schedule", index: 0, patch: { chain: "docs_only" } });
    await userEvent.click(screen.getByRole("button", { name: "Remove schedule" }));
    expect(sent()).toContainEqual({ op: "remove_schedule", index: 0 });
    await waitFor(() => expect(screen.getByRole("complementary", { name: "bd ready pane" })).toBeInTheDocument());
  });

  it("shows a schedule's problem on its row and on the field it is about", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(intakeView({ problems: [{ path: "schedules[0].repo", field: "repo", message: "/src/platform is not a connected repo", file: "intake.yaml", line: 1, col: 1, schedule: 0 }] }, INTAKE, true)));
    mount();
    const row = await screen.findByRole("button", { name: /Dependency check/ });
    expect(within(row).getByRole("img", { name: "has a problem" })).toBeInTheDocument();
    await userEvent.click(row);
    expect(screen.getAllByText("/src/platform is not a connected repo").length).toBeGreaterThanOrEqual(2);
  });

  it("describes a schedule's cron in words, and says when there are none", async () => {
    mount();
    expect(await screen.findByText("weekdays 09:00 UTC · platform · default")).toBeInTheDocument();
    document.body.innerHTML = "";
    vi.mocked(d.getDraft).mockImplementation(() => ok(intakeView({}, { ...INTAKE, schedules: [] })));
    mount();
    expect(await screen.findByText("No schedules yet.")).toBeInTheDocument();
  });

  it("marks a changed row against the file it is in", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(intakeView({ changes: [{ path: "interval_s", kind: "change", summary: "300 → 120", file: "intake.yaml" }, { path: "priority_ceiling", kind: "change", summary: "2 → 3", file: "intake.yaml" }] }, INTAKE, true)));
    mount();
    await screen.findByRole("region", { name: "Recent checks" });
    await expand();
    const chips = screen.getAllByText("changed");
    expect(chips).toHaveLength(2);
  });
});

describe("Auto-intake page: the pane's first state", () => {
  it("loads with the pane on its rail, and picking the rule opens it", async () => {
    mount();
    await screen.findByRole("region", { name: "Recent checks" });
    expect(screen.getByRole("complementary", { name: "bd ready pane, collapsed" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("main")).getByRole("button", { pressed: true }));
    expect(screen.getByRole("complementary", { name: "bd ready pane" })).toBeInTheDocument();
  });
});
