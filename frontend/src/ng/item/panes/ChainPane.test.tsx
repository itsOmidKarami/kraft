import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KraftEvent, WorkItemDocument } from "../../../types";
import type { EventType } from "../../../types/vocab.generated";
import { resetHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { ItemDraftProvider } from "../draft/context";
import { answer, ov } from "../draft/testkit";
import { acceptWrites, detail, fresh, stubFetch } from "../testkit";
import * as toast from "../../ui/Toast";
import { ChainConfig, ChainOverview, STILL_STOPPED } from "./ChainPane";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("PATCH /work-items/w1", "POST /work-items/w1/budget/raise", "POST /work-items/w1/retry");

afterEach(() => vi.unstubAllGlobals());
const NOW = Date.parse("2026-09-13T10:10:00Z");
const posts = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

describe("ChainOverview", () => {
  it("lists recent events newest first; a line about a node selects it", async () => {
    const onSelect = vi.fn();
    const ev = (seq: number, type: EventType, node_id: string | null): KraftEvent => ({ seq, work_item_id: "w1", type, payload: {}, node_id, created_at: "2026-09-13T10:04:00Z" });
    render(<ChainOverview item={detail({ summary: { nodes_done: 7, nodes_total: 15, gates_passed: 3, step: null } })} events={[ev(1, "work_item_created", null), ev(2, "node_completed", "plan"), ev(3, "worker_session_exited", "plan")]} now={NOW} onSelect={onSelect} />);
    expect(screen.getByText("7 of 15 nodes · 3 gates passed")).toBeInTheDocument();
    const lines = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(lines).toEqual(["6mplan finished", "6mfiled"]);
    await userEvent.click(screen.getByRole("button", { name: "plan finished" }));
    expect(onSelect).toHaveBeenCalledWith("plan");
    await userEvent.click(screen.getByRole("button", { name: "verification" }));
    expect(onSelect).toHaveBeenLastCalledWith("verification");
  });

  it("shows the last five lines of Recent, the rest behind a link: in place, or the caller's", async () => {
    const ev = (seq: number): KraftEvent => ({ seq, work_item_id: "w1", type: "node_completed", payload: {}, node_id: `n${seq}`, created_at: "2026-09-13T10:04:00Z" });
    const events = [1, 2, 3, 4, 5, 6, 7].map(ev);
    const { unmount } = render(<ChainOverview item={detail()} events={events} now={NOW} onSelect={() => {}} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    await userEvent.click(screen.getByRole("button", { name: "Show 2 earlier" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(7);
    unmount();
    const onMore = vi.fn();
    render(<ChainOverview item={detail()} events={events} now={NOW} onSelect={() => {}} onMore={onMore} />);
    await userEvent.click(screen.getByRole("button", { name: "All activity" }));
    expect(onMore).toHaveBeenCalled();
  });

  it("says a never-started item is not started, as the header does, and a paused mid-chain one is paused", () => {
    const { unmount } = render(<ChainOverview item={detail({ status: "paused", display_status: "paused", current_node_id: null })} events={[]} now={NOW} onSelect={() => {}} />);
    expect(screen.getByText("status").nextElementSibling).toHaveTextContent(/^not started$/);
    unmount();
    render(<ChainOverview item={detail({ status: "paused", display_status: "paused", current_node_id: "verification" })} events={[]} now={NOW} onSelect={() => {}} />);
    expect(screen.getByText("status").nextElementSibling).toHaveTextContent(/^paused/);
  });

  it("lists the spec and plan attached at intake, each opening its document when the page can open one", async () => {
    const onDoc = vi.fn();
    // One name for both, as Kraft's own specs/<x>.md and plans/<x>.md have: the kind tells them apart (R11b-07).
    const attachments = [{ kind: "spec" as const, path: "docs/specs/ws.md" }, { kind: "plan" as const, path: "docs/plans/ws.md" }];
    const docs = [{ document_id: "d-plan", attachment_kind: "plan", path: "docs/plans/ws.md" }, { document_id: "d-spec", attachment_kind: "spec", path: "docs/specs/ws.md" }, { document_id: "d-x", attachment_kind: null, path: "x.md" }] as WorkItemDocument[];
    const { unmount } = render(<ChainOverview item={detail({ attachments })} events={[]} now={NOW} onSelect={() => {}} docs={docs} onDoc={onDoc} />);
    expect(screen.getByText("attached").closest("div")).toHaveTextContent("spec ws.mdplan ws.md");
    await userEvent.click(screen.getByRole("button", { name: "Plan: ws.md" }));
    expect(onDoc).toHaveBeenCalledWith(docs[0]);
    unmount();
    // With no indexed document to open (the board's peek, or any item before it starts),
    // a name opens the copy Kraft kept at intake.
    const calls = stubFetch({ "GET /work-items/w1/attachments/spec": [200, { title: "Workspace spec", path: "docs/specs/ws.md", content: "# Workspace spec\n\nShare one **worktree**.", truncated: false }] });
    const peek = render(<ChainOverview item={detail({ attachments })} events={[]} now={NOW} onSelect={() => {}} />);
    expect(screen.getByText("attached").closest("div")).toHaveTextContent("spec ws.mdplan ws.md");
    await userEvent.click(screen.getByRole("button", { name: "Spec: ws.md" }));
    const viewer = await screen.findByRole("dialog", { name: "Workspace spec" });
    expect(await within(viewer).findByText("worktree")).toBeInTheDocument();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["GET /work-items/w1/attachments/spec"]);
    await userEvent.click(within(viewer).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    peek.unmount();
    render(<ChainOverview item={detail({ attachments: [] })} events={[]} now={NOW} onSelect={() => {}} />);
    expect(screen.queryByText("attached")).toBeNull();
  });
});

describe("ChainConfig", () => {
  const show = (over: Parameters<typeof detail>[0] = {}, editBudget = false) => {
    const reload = vi.fn();
    const onEditBudget = vi.fn();
    render(<ChainConfig item={detail({ budget_cap: { cap_usd: 5, source: "policy", spent_usd: 4, daily: { spent_usd: 18.2, cap_usd: 50 } }, ...over })} policy={null} reload={reload} editBudget={editBudget} onEditBudget={onEditBudget} />);
    return { reload, onEditBudget };
  };

  // R11a-03: the field opened with the caret after the cap, so typing 0.03 saved $100.03.
  // R11b-03: the field that had the focus is gone after Escape, Cancel or a save; ✎ takes it back.
  // A decimal comma is a point: "0,03" left Save off and saved nothing (#504 review); a grouping one is not: "1,000" saved $1.00 (R12b-10).
  it.each([["{Escape}", null], ["Cancel", null], ["7{Enter}", 7], ["0,03{Enter}", 0.03], ["1,000.50{Enter}", 1000.5]] as const)("selects the cap in force on ✎, and hands the focus back to ✎ after %s", async (close, saved) => {
    const calls = stubFetch(WRITES);
    function Editing() {
      const [on, setOn] = useState(false);
      return <ChainConfig item={detail({ budget_cap: { cap_usd: 100, source: "item", spent_usd: 4 } })} policy={null} reload={() => {}} editBudget={on} onEditBudget={setOn} />;
    }
    render(<Editing />);
    await userEvent.click(screen.getByRole("button", { name: "Edit budget" }));
    const field = screen.getByRole<HTMLInputElement>("textbox", { name: "Budget in dollars" });
    expect(field).toHaveFocus();
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, 3]);
    if (close === "Cancel") await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    else await userEvent.keyboard(close);
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit budget" })).toHaveFocus());
    expect(posts(calls)).toEqual(saved === null ? [] : [{ method: "PATCH", path: "/work-items/w1", body: { budget_usd: saved } }]);
  });

  // r12 review: "1,000" is a thousand or one, so it saves nothing and says how to type it.
  it.each(["1,000", "Infinity"])("keeps Save off for %j and says how to type the amount", async (typed) => {
    const calls = stubFetch(WRITES);
    render(<ChainConfig item={detail({ budget_cap: { cap_usd: 100, source: "item", spent_usd: 4 } })} policy={null} reload={() => {}} editBudget onEditBudget={() => {}} />);
    await userEvent.keyboard(`${typed}{Enter}`);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText("Type the amount plainly, like 1000 or 1.5.")).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
  });

  // r12 review: a cap of $1.234 was prefilled "1.234", which the editor then refused as ambiguous.
  it("re-saves a cap in force with three decimals unchanged", async () => {
    const calls = stubFetch(WRITES);
    render(<ChainConfig item={detail({ budget_cap: { cap_usd: 1.234, source: "item", spent_usd: 1 } })} policy={null} reload={() => {}} editBudget onEditBudget={() => {}} />);
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { budget_usd: 1.234 } }]));
  });

  // An item with no budget meter has no ✎: the focus goes back to what opened the editor (Raise cap).
  it("hands the focus back to what opened the editor when there is no ✎", async () => {
    stubFetch(WRITES);
    function Raising() {
      const [on, setOn] = useState(false);
      return <><button type="button" onClick={() => setOn(true)}>Raise cap</button><ChainConfig item={detail({ budget_cap: undefined })} policy={null} reload={() => {}} editBudget={on} onEditBudget={setOn} /></>;
    }
    render(<Raising />);
    await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
    expect(screen.getByRole("textbox", { name: "Budget in dollars" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Raise cap" })).toHaveFocus();
  });

  it("turns a meter amber past 75% and red at the cap", () => {
    show();
    expect(screen.getByRole("meter", { name: "Budget" }).closest(".meter")).toHaveClass("is-warn");
    expect(screen.getByRole("meter", { name: "Today, all items" }).closest(".meter")).not.toHaveClass("is-warn");
  });

  it("raises the budget with a quick pick through PATCH, or /budget/raise on a budget stop", async () => {
    const calls = stubFetch(WRITES);
    const { reload } = show({}, true);
    await userEvent.click(screen.getByRole("button", { name: "+$5" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { budget_usd: 10 } }]);
  });

  it("on a budget stop raises and retries in one call", async () => {
    const calls = stubFetch(WRITES);
    show({ display_status: "needs_you", stop: { kind: "budget", node: "n", resume_at: null, reason: null, scope: "work_item" } }, true);
    await userEvent.click(screen.getByRole("button", { name: "No cap" }));
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/budget/raise", body: { budget_usd: null } }]));
  });

  it("on a budget stop the item's own cap did not make, sets that cap without /budget/raise, which would refuse it", async () => {
    const calls = stubFetch(WRITES);
    const said = vi.spyOn(toast, "showToast");
    // The daily cap stopped it: the item's own is not the one in the way.
    show({ display_status: "needs_you", stop: { kind: "budget", node: "n", resume_at: null, reason: null, scope: "daily" } }, true);
    await userEvent.click(screen.getByRole("button", { name: "+$5" }));
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { budget_usd: 10 } }]));
    expect(said).toHaveBeenCalledWith(STILL_STOPPED);
    said.mockRestore();
  });

  it("on a policy budget stop raises that policy and retries, with no No-cap pick", async () => {
    const calls = stubFetch(WRITES);
    const { reload } = show({ display_status: "needs_you", stop: { kind: "budget", node: "n", resume_at: null, reason: null, limit: { path: "", key: "budget_usd", value: 5, maximum: 25 } } }, true);
    expect(screen.queryByRole("button", { name: "No cap" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "+$5" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { policy: { budget_usd: 10 } } }, { method: "POST", path: "/work-items/w1/retry", body: {} }]);
  });

  it("after a Raise cap, meters and lists the item's policy cap, and the pencil raises that cap keeping the rest of the override", async () => {
    const calls = stubFetch(WRITES);
    const policy_override = { budget_usd: 1, paths: { verification: { max_attempts: 5 } } };
    const { reload } = show({ budget_cap: { cap_usd: 1, source: "item", key: "policy.budget_usd", spent_usd: 0.07 }, policy_override }, true);
    expect(screen.getByText("Budget").closest(".meter")).toHaveTextContent("$0.070 of $1.00");
    expect(screen.queryByText(/Nothing changed/)).toBeNull();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["budget_usd $1.00 · item policy", "verification max_attempts 5 · item policy"]);
    expect(screen.queryByRole("button", { name: "No cap" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "+$5" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { policy: { ...policy_override, budget_usd: 6 } } }]);
  });

  it("lists the item's own cap whenever it set one, beside a lower policy cap", () => {
    show({ budget_set: 1, budget_usd: 20, budget_cap: { cap_usd: 5, source: "item", key: "policy.budget_usd", spent_usd: 1 }, policy_override: { budget_usd: 5, max_attempts: 4 } });
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["budget $20.00", "budget_usd $5.00 · item policy", "max_attempts 4 · item policy"]);
  });

  /** A never-started item's Config also draws its agents' rows, which read the harnesses. */
  const HARNESS = { "GET /harnesses/profiles": [200, { profiles: [{ id: "claude", provider: "claude" }], agent_profiles: [] }], "GET /harnesses": [200, []] } as Record<string, [number, unknown]>;

  it("lists an item-wide model and effort set before start, never saying nothing changed under them", async () => {
    resetHarnessOptions();
    const calls = stubFetch({ ...WRITES, ...HARNESS });
    render(<ChainConfig item={fresh({ agent_overrides: { model: "claude-sonnet-4-5", effort: "low" }, node_overrides: { verification: { model: "opus" } } })} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    expect(screen.queryByText(/Nothing changed/)).toBeNull();
    const listed = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(listed).toEqual(["every agent task model claude-sonnet-4-5, effort low · item-wide reset", "verification model opus · node reset"]);
    await userEvent.click(screen.getAllByRole("button", { name: "reset" })[0]);
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { agent_overrides: {} } }]));
  });

  it("lists what the draft holds as not applied yet, leaving out an edit the run has passed", async () => {
    resetHarnessOptions();
    stubFetch({ ...HARNESS, "GET /work-items/w1/draft": answer([ov("implementation", undefined, { budget_usd: 2 }), ov("plan.write.plan", { model: "x" }, undefined, true)]) });
    const it = fresh();
    render(<ItemDraftProvider item={it} reload={() => {}}><ChainConfig item={it} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} /></ItemDraftProvider>);
    expect(await screen.findByText("implementation budget ($) → 2")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["implementation budget ($) → 2 · in the draft, not applied yet"]);
    expect(screen.queryByText(/Nothing changed/)).toBeNull();
  });

  it("says nothing changed without overrides, and resets a node override", async () => {
    const { unmount } = render(<ChainConfig item={detail({ node_overrides: {} })} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    expect(screen.getByText(/Nothing changed/)).toBeInTheDocument();
    unmount();
    const calls = stubFetch({ "PATCH /work-items/w1": [409, { detail: "node verification has already started" }] });
    render(<ChainConfig item={detail({ node_overrides: { verification: { attempts: 4 } } })} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "reset" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already started");
    expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { node_overrides: { verification: {} } } }]);
  });
});
