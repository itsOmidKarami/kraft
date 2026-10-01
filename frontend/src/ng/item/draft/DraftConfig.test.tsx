import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { detail, stubFetch, V1, type Call } from "../testkit";
import { ItemDraftProvider } from "./context";
import { DraftConfig } from "./DraftConfig";
import type { DraftView, MarkedOp } from "./types";

const TASK = "merge_request.open.open_draft";
const reply = (ops: MarkedOp[], problems: DraftView["problems"] = []): [number, DraftView] => [200, { ops, problems, checks: { budget: { spent_usd: 0, cap_usd: null } }, nodes: V1, base_seq: 1, updated_at: null }];
const ov = (path: string, task_config?: Record<string, unknown>, policy?: Record<string, unknown>): MarkedOp => ({ op: "override", path, ...(task_config ? { task_config } : {}), ...(policy ? { policy } : {}), passed: false });
const harness = { "GET /harnesses/profiles": [200, { profiles: [{ id: "claude", provider: "claude" }, { id: "codex", provider: "codex" }], agent_profiles: [] }], "GET /harnesses/providers": [200, { valid: { claude: { capabilities: { effort: { values: ["low", "high"] } } } } }] } as Record<string, [number, unknown]>;

let calls: Call[];
const show = async (path: string, draft = reply([]), more: Record<string, [number, unknown]> = {}) => {
  calls = stubFetch({ "GET /work-items/w1/draft": draft, ...harness, ...more });
  render(<ItemDraftProvider item={detail()} reload={() => {}}><DraftConfig path={path} /></ItemDraftProvider>);
  await waitFor(() => expect(calls.some((c) => c.path === "/work-items/w1/draft")).toBe(true));
};
const puts = () => calls.filter((c) => c.method === "PUT").map((c) => c.body);
beforeEach(() => resetHarnessOptions());
afterEach(() => vi.unstubAllGlobals());

describe("DraftConfig", () => {
  it("offers task fields then policy rows on a task that has not run", async () => {
    await show(TASK);
    const labels = (await screen.findAllByRole("button", { name: /^Override / })).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Override harness", "Override model", "Override effort", "Override prompt", "Override command", "Override running cap", "Override total cap", "Override budget ($)", "Override token budget"]);
  });

  it("offers policy rows only on a node or a step", async () => {
    await show("merge_request");
    expect((await screen.findAllByRole("button", { name: /^Override / }))).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Override model" })).toBeNull();
  });

  it("sends the typed value as task_config on Enter, and nothing on Escape", async () => {
    await show(TASK, reply([]), { "PUT /work-items/w1/draft": reply([ov(TASK, { model: "opus" })]) });
    await userEvent.click(await screen.findByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("textbox", { name: "model" }), "gpt{Escape}");
    expect(puts()).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Override model" }));
    await userEvent.type(screen.getByRole("textbox", { name: "model" }), "opus{Enter}");
    await waitFor(() => expect(puts()).toEqual([{ ops: [{ op: "override", path: TASK, task_config: { model: "opus" } }] }]));
  });

  it("sends a policy field under policy, as a number", async () => {
    await show("merge_request", reply([]), { "PUT /work-items/w1/draft": reply([ov("merge_request", undefined, { time_cap_minutes: 30 })]) });
    await userEvent.click(await screen.findByRole("button", { name: "Override running cap" }));
    await userEvent.type(screen.getByRole("textbox", { name: "running cap" }), "30{Enter}");
    await waitFor(() => expect(puts()).toEqual([{ ops: [{ op: "override", path: "merge_request", policy: { time_cap_minutes: 30 } }] }]));
  });

  it("refuses a cap of 0 before sending, saying why", async () => {
    await show("merge_request");
    await userEvent.click(await screen.findByRole("button", { name: "Override running cap" }));
    await userEvent.type(screen.getByRole("textbox", { name: "running cap" }), "0{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("running cap: A whole number above 0.");
    expect(puts()).toEqual([]);
  });

  it("refuses a budget of 0 before sending", async () => {
    await show("merge_request");
    await userEvent.click(await screen.findByRole("button", { name: "Override budget ($)" }));
    await userEvent.type(screen.getByRole("textbox", { name: "budget ($)" }), "0{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("above 0");
    expect(puts()).toEqual([]);
  });

  it("shows an override as the item's own, and ↺ removes that one field, deleting an emptied draft", async () => {
    await show(TASK, reply([ov(TASK, { model: "opus" })]), { "PUT /work-items/w1/draft": reply([]) });
    expect(await screen.findByText("opus")).toBeInTheDocument();
    expect(screen.getByLabelText("overridden for this item")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reset model" }));
    await waitFor(() => expect(puts()).toEqual([{ ops: [] }]));
  });

  it("keeps the other fields of the op when one is reset", async () => {
    await show(TASK, reply([ov(TASK, { model: "opus", effort: "high" })]), { "PUT /work-items/w1/draft": reply([]) });
    await userEvent.click(await screen.findByRole("button", { name: "Reset model" }));
    await waitFor(() => expect(puts()).toEqual([{ ops: [{ op: "override", path: TASK, task_config: { effort: "high" } }] }]));
  });

  it("offers the harness and effort the server lists", async () => {
    await show(TASK);
    await userEvent.click(await screen.findByRole("button", { name: "Override harness" }));
    expect(within(await screen.findByRole("combobox", { name: "harness" })).getAllByRole("option").map((o) => o.textContent)).toEqual(["not set", "claude", "codex"]);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Override effort" }));
    expect(within(await screen.findByRole("combobox", { name: "effort" })).getAllByRole("option").map((o) => o.textContent)).toEqual(["not set", "low", "high"]);
  });

  it("says a prompt override replaces the whole prompt", async () => {
    await show(TASK);
    await userEvent.click(await screen.findByRole("button", { name: "Override prompt" }));
    expect(screen.getByRole("textbox", { name: "prompt" })).toHaveAttribute("placeholder", "Replaces the whole prompt for this item");
  });

  it("shows the server's message for an override it refuses, on that row", async () => {
    await show(TASK, reply([ov(TASK, { command: "make" })], [{ op: 0, message: "task_config.command: not a field of an agent task" }]));
    expect(await screen.findByRole("alert")).toHaveTextContent("not a field of an agent task");
  });

  it("offers no ✎ at or before the node the run stands on, and says so once", async () => {
    await show("verification.checks.lint");
    expect(await screen.findByText("Already run or running: edit a later node.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Override / })).toBeNull();
  });

  it("shows nothing until the draft has loaded", () => {
    stubFetch({ "GET /work-items/w1/draft": [500, { detail: "boom" }] });
    render(<ItemDraftProvider item={detail()} reload={() => {}}><DraftConfig path={TASK} /></ItemDraftProvider>);
    expect(screen.queryByRole("button", { name: /^Override / })).toBeNull();
  });
});
