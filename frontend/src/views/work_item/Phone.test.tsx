import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { ChainNode, WorkItem, WorkerSession } from "../../types";
import { WorkItemDetail } from ".";

const here = dirname(fileURLToPath(import.meta.url));

/** Forces `usePhone()` to `true` for the life of a test, the same
 *  `matchMedia` stub `theme.test.ts` uses. */
function mockPhone() {
  const mql: Partial<MediaQueryList> = {
    matches: true,
    media: "(max-width: 767px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue(mql));
}

const NODES: ChainNode[] = [
  { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null },
];

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "wi_01HX3K9", title: "T", repo: "/r", status: "active", chain_template: "default",
    chain_definition: { template_id: "default", nodes: NODES },
    current_node_id: "verify", bead_id: "B", created_at: "t", updated_at: "t",
    completedNodes: ["spec", "plan"],
    node_overrides: {},
    node_overrides_count: 0,
    effective_chain: { template_id: "default", nodes: NODES },
    budget_cap: { cap_usd: 10, source: "policy", spent_usd: 1 },
    ...over,
  }) as WorkItem;

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "wi_01HX3K9", node_id: "verify", hook_point: "on.test.run",
    status: "running", attempt: 1, round: 0, created_at: "t", started_at: "t", exited_at: null,
    tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, model: null, head_sha: null,
    ...over,
  }) as WorkerSession;

const setup = (over: Partial<WorkItem> = {}, sessions: WorkerSession[] = []) =>
  useStore.setState({
    workItems: { wi_01HX3K9: item(over) },
    sessionsByItem: { wi_01HX3K9: sessions },
    eventsByItem: { wi_01HX3K9: [] },
  } as never);

beforeEach(() => {
  mockPhone();
  setup();
  vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue(undefined);
  vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "wi_01HX3K9", documents: [] });
  vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
    base_ref: "abc123", diff: "", files: [], untracked: [], truncated: false, landed: null,
  } as never);
  vi.spyOn(api, "getLogLines").mockResolvedValue({ session_id: "s1", status: "done", lines: [] });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const renderDetail = (hash = "") =>
  render(
    <MemoryRouter
      initialEntries={[`/work-items/wi_01HX3K9${hash}`]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/work-items/:id" element={<WorkItemDetail />} />
      </Routes>
    </MemoryRouter>,
  );

describe("WorkItemDetail on a phone (m04)", () => {
  it("shows the vertical tap-a-stage list instead of the desktop split", () => {
    renderDetail();
    expect(screen.getByTestId("phone-stage-list")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /chain stages/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("inspector")).not.toBeInTheDocument();
  });

  it("marks the current node's row and gives the others a duration or a dash", () => {
    renderDetail();
    expect(screen.getByTestId("phone-stage-verify")).toHaveAttribute("data-state", "current");
    expect(screen.getByTestId("phone-stage-spec")).toHaveAttribute("data-state", "done");
  });

  it("caps the current node's log at 8 lines under the stage list", async () => {
    setup({}, [session()]);
    renderDetail();
    expect(await screen.findByTestId("right-pane-log")).toBeInTheDocument();
  });

  it("tapping a stage opens the full-screen node page (m05), not the split", async () => {
    renderDetail();
    await userEvent.click(within(screen.getByTestId("phone-stage-plan")).getByText("plan"));
    const page = await screen.findByTestId("phone-node-page");
    expect(within(page).getByRole("tab", { name: /tasks/i })).toBeInTheDocument();
    expect(screen.queryByTestId("phone-stage-list")).not.toBeInTheDocument();
  });

  it("the node page has its own back button labelled with the item title (W3.7), with no Timeline tab", async () => {
    renderDetail("#node=plan");
    const page = await screen.findByTestId("phone-node-page");
    expect(within(page).getByRole("button", { name: "‹ T" })).toBeInTheDocument();
    expect(within(page).queryByRole("button", { name: /wi_01HX3K9/ })).not.toBeInTheDocument();
    expect(within(page).queryByRole("tab", { name: /timeline/i })).not.toBeInTheDocument();
  });

  it("the phone item header is back + title only: no repo, no id (W3.1)", () => {
    setup({ title: "Design the caching layer", repo: "/code/kraft-plugins" });
    renderDetail();
    const bar = document.querySelector(".phone-topbar") as HTMLElement;
    expect(within(bar).getByRole("link", { name: "‹ Board" })).toHaveAttribute("href", "/");
    expect(within(bar).getByText("Design the caching layer")).toBeInTheDocument();
    expect(bar.textContent).not.toMatch(/wi_01HX3K9|kraft-plugins|swipe/);
  });

  it("maximizes the node page's log as a page layout, not a dead button (W0)", async () => {
    const user = userEvent.setup();
    setup({}, [session({ id: "s1", node_id: "verify" })]);
    renderDetail("#node=verify&tab=tasks&session=s1");
    await user.click(await screen.findByRole("button", { name: /maximi/i }));
    expect(document.querySelector(".item-max-strip")).toBeTruthy();
    expect(screen.queryByTestId("phone-node-page")).toBeNull();
  });

  it("the back button returns to the stage list", async () => {
    renderDetail("#node=plan");
    await screen.findByTestId("phone-node-page");
    await userEvent.click(screen.getByRole("button", { name: "‹ T" }));
    await waitFor(() => expect(screen.getByTestId("phone-stage-list")).toBeInTheDocument());
  });

  it("the node page's tabs switch between Tasks, Changes, Documents and Config", async () => {
    renderDetail("#node=verify");
    const page = await screen.findByTestId("phone-node-page");
    await userEvent.click(within(page).getByRole("tab", { name: /config/i }));
    expect(within(page).getByTestId("inspector-config")).toBeInTheDocument();
  });

  it("shows Approve in the document pane at a gate", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "wi_01HX3K9", path: "docs/spec.md", title: "The spec",
      content: "body", truncated: false, artifact_max_bytes: 1_000_000,
    });

    setup({ status: "needs_human", pending_gate: "spec_approval", gate_artifact: "docs/spec.md", current_node_id: "spec" });
    renderDetail("#node=spec");
    const page = await screen.findByTestId("phone-node-page");
    await userEvent.click(within(page).getByRole("tab", { name: /documents/i }));

    expect(await screen.findByText("body")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Approve$/ })).toBeInTheDocument();
  });

  it("wraps the item card's button row on a phone, its buttons sharing each line (W11 rule 10)", () => {
    // jsdom has no viewport to render a `@media` rule from; pin the source
    // instead, the way styles.order.test.ts does.
    const css = readFileSync(join(here, "work_item.css"), "utf-8");
    expect(css).toMatch(/\.item-card-actions\s*\{[^}]*flex-wrap:\s*wrap/);
    const phone = css.slice(css.indexOf("@media (max-width: 767px)"));
    expect(phone).toMatch(/\.item-card-actions\s*>\s*\.btn\s*\{[^}]*flex:\s*1/);
  });
});
