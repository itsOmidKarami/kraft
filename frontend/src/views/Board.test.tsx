import { readFileSync } from "node:fs";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { KraftEvent, WorkerSession, WorkItem } from "../types";
import type { ToastPayload } from "../components/Toast";
import { Board } from "./Board";

const wi = (over: Partial<WorkItem>): WorkItem =>
  ({
    id: over.id ?? "w1",
    title: over.title ?? "Item",
    repo: over.repo ?? "/repo-a",
    status: over.status ?? "active",
    chain_template: over.chain_template ?? "quick-task",
    chain_definition: {
      template_id: "quick-task",
      nodes: [
        { id: "plan", tasks: ["a"], gate_after: "plan_approval" },
        { id: "verify", tasks: ["b"], gate_after: null },
      ],
    },
    current_node_id: "verify",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

const setItems = (...items: WorkItem[]) =>
  useStore.setState({ workItems: Object.fromEntries(items.map((i) => [i.id, i])), sessionsByItem: {}, eventsByItem: {} } as never);

/** A needs_human item with its escalation turn running, sessions and events in the store (W11 · J). */
const escalating = (id: string, title: string, startedMinutesAgo: number) => {
  const started = new Date(Date.now() - startedMinutesAgo * 60_000).toISOString();
  return {
    item: wi({ id, title, status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
    sessions: [
      { id: `${id}-e1`, work_item_id: id, node_id: "verify", hook_point: "escalation", status: "running", attempt: 1, round: 0, created_at: started, started_at: started, exited_at: null } as WorkerSession,
    ],
    events: [
      { seq: 1, work_item_id: id, type: "work_item_needs_human", payload: {}, created_at: started },
      { seq: 2, work_item_id: id, type: "escalation_message", payload: { session_id: `${id}-e1`, message: "go" }, created_at: started },
    ] as KraftEvent[],
  };
};

beforeEach(() => {
  localStorage.clear();
  setItems(
    wi({ id: "w1", repo: "/repo-a", status: "active" }),
    wi({ id: "w2", repo: "/repo-b", status: "completed", chain_template: "default" }),
  );
  // Kraft-5fx.17: resetAllMocks, not restoreAllMocks — see store.test.ts's
  // beforeEach for why restoreAllMocks doesn't reliably undo a spy on a
  // zustand store method under vitest 5.
  vi.resetAllMocks();
  // Board re-bootstraps on mount; keep it inert so tests keep the state set above.
  vi.spyOn(useStore.getState(), "bootstrap").mockResolvedValue();
  // Board checks repo count for the fresh-install branch (design 08); a
  // non-empty default keeps every other test on the normal board.
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/repo-a" } as never] });
  vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({ items: [], cursor: 0 });
  vi.spyOn(api, "getTheme").mockResolvedValue({
    palette: "nocturne",
    mode: "dark",
    density: "compact",
    board: { group_by: "status", show_done: 5, open_in: "peek" },
  });
});

const renderBoard = () =>
  render(
    <MemoryRouter>
      <Board />
    </MemoryRouter>,
  );

describe("Board facet overflow chip (W12.5)", () => {
  it("reads '+N more' with a caret and says it opens a list", async () => {
    setItems(
      wi({ id: "w1", repo: "/repo-a" }),
      wi({ id: "w2", repo: "/repo-b", status: "completed" }),
      wi({ id: "w3", repo: "/repo-c" }),
    );
    // jsdom has no layout: put every chip after the first two on a second row,
    // which is what Board measures to decide what spills into "+N".
    vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockImplementation(function (this: HTMLElement) {
      const box = this.parentElement;
      if (!box?.classList.contains("board-filter-chips")) return 0;
      const chips = [...box.children].filter((k) => k.classList.contains("chip"));
      return chips.indexOf(this) >= 2 ? 40 : 0;
    });
    const { container } = renderBoard();
    const summary = await waitFor(() => {
      const s = container.querySelector(".board-more > summary");
      expect(s).not.toBeNull();
      return s as HTMLElement;
    });
    const hidden = container.querySelectorAll(".board-filter-chips > [data-overflow]").length;
    expect(hidden).toBeGreaterThan(0);
    expect(summary).toHaveClass("chip");
    expect(summary).toHaveAttribute("aria-haspopup", "true");
    expect(summary.textContent).toBe(`+${hidden} more `);
    expect(summary.querySelector("svg")).not.toBeNull();
  });
});

/** So the peek-clear test (Kraft-3e16 §2.2) can read the URL of whichever
 *  history entry is current, and press Back to move between entries —
 *  properties `renderBoard()`'s plain MemoryRouter has no sibling to expose. */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname}{location.search}</span>;
}
function GoBack() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate(-1)} aria-label="test-go-back">
      back
    </button>
  );
}
const renderBoardWithProbe = () =>
  render(
    <MemoryRouter>
      <GoBack />
      <LocationProbe />
      <Board />
    </MemoryRouter>,
  );

// selector pins this to the group heading, not the same-named Status facet button
const group = (label: string) =>
  screen.getByText(label, { selector: ".group-label" }).closest("section") as HTMLElement;

// Scopes a facet-chip query to the filter row: a board row is a role="button"
// too now (peek toggle), and its accessible name can include the same text
// (a chain template, a status word) a facet chip carries.
const filters = () => screen.getByRole("group", { name: /filters/i });

describe("Board", () => {
  it("groups by attention rather than showing a status column", () => {
    renderBoard();
    expect(within(group("Running")).getAllByTestId("board-card")).toHaveLength(1);
    expect(within(group("Done")).getAllByTestId("board-card")).toHaveLength(1);
    expect(within(group("Needs you")).queryAllByTestId("board-card")).toHaveLength(0);
    expect(within(group("Needs you")).getByText(/nothing here/)).toBeInTheDocument();
  });

  // jsdom does not load styles.css into the CSSOM (doing so via vitest's
  // `css: true` broke pointer-events assertions in unrelated suites), so
  // this pins the class names the no-wrap rules hang off rather than the
  // computed styles themselves; the Playwright spec in Task 9 covers the
  // rendered box.
  it("gives the title and meta line the classes that stop them wrapping", () => {
    renderBoard();
    const row = screen.getAllByTestId("board-card")[0];
    expect(row.querySelector(".board-row-title")).toBeInTheDocument();
    expect(row.querySelector(".board-row-main")).toBeInTheDocument();
  });

  it("clicking the title toggles the peek instead of navigating", async () => {
    setItems(wi({ id: "w1" }));
    renderBoard();
    const title = screen.getByTestId("board-card").querySelector(
      ".board-row-title",
    ) as HTMLElement;
    expect(title.tagName).toBe("SPAN");
    await userEvent.click(title);
    expect(screen.getByTestId("board-card")).toHaveAttribute("data-selected", "true");
  });

  it("Open → clears ?peek before navigating, so Back lands on a clean board (Kraft-3e16 §2.2)", async () => {
    setItems(wi({ id: "w1" }));
    renderBoardWithProbe();
    const title = screen.getByTestId("board-card").querySelector(
      ".board-row-title",
    ) as HTMLElement;
    await userEvent.click(title);
    expect(screen.getByTestId("location-probe").textContent).toContain("peek=w1");

    await userEvent.click(screen.getByRole("link", { name: /Open/ }));
    expect(screen.getByTestId("location-probe").textContent).toBe("/work-items/w1");

    await userEvent.click(screen.getByRole("button", { name: "test-go-back" }));
    // Not just "doesn't contain peek=w1" — the entry we return to is
    // exactly the pre-peek board: pathname "/", no search at all.
    expect(screen.getByTestId("location-probe").textContent).toBe("/");
  });

  it("filters on the repo facet and clears it when the same facet is clicked again", async () => {
    renderBoard();
    await userEvent.click(within(filters()).getByRole("button", { name: /^repo-a/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
    await userEvent.click(within(filters()).getByRole("button", { name: /^repo-a/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
  });

  it("combines the repo and template facets, and counts each under the other", async () => {
    renderBoard();
    // With /repo-a picked, the template facet only counts that repo's items.
    await userEvent.click(within(filters()).getByRole("button", { name: /^repo-a/ }));
    expect(within(filters()).getByRole("button", { name: /quick-task/ })).toHaveTextContent("1");
    expect(within(filters()).queryByRole("button", { name: /default/ })).toBeNull();
    await userEvent.click(within(filters()).getByRole("button", { name: /quick-task/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
  });

  // The New work item dialog omits `chain_template` when `default` is chosen
  // (Kraft-cd47), so a board holding one explicit template and one default
  // item mixes a real id with a null. Sorting those keys with
  // `localeCompare` threw on the null and blanked the whole board. It throws
  // when the null is the *receiver*, so it fired only when `sort` passed the
  // null key as a comparison's first argument -- which is why a full board
  // survived it and a small one did not. This test does not reproduce that
  // pairing directly; it pins the guard, and reverting `tplOf` reddens it.
  it("counts an item with no explicit template as `default` instead of blanking the board", async () => {
    setItems(
      wi({ id: "w0", repo: "/repo-a", status: "active", chain_template: null as never }),
      wi({ id: "w1", repo: "/repo-a", status: "active", chain_template: "quick-task" }),
    );
    renderBoard();
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
    expect(within(filters()).getByRole("button", { name: /^default/ })).toHaveTextContent("1");
    // And the chip filters to that item rather than to nothing.
    await userEvent.click(within(filters()).getByRole("button", { name: /^default/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
  });

  it("caps the Done group at five until 'show all' is clicked", async () => {
    setItems(
      ...Array.from({ length: 7 }, (_, n) =>
        wi({ id: `d${n}`, status: "completed", title: `Done ${n}` }),
      ),
    );
    renderBoard();
    expect(within(group("Done")).getAllByTestId("board-card")).toHaveLength(5);
    await userEvent.click(screen.getByRole("button", { name: /show all 7/ }));
    expect(within(group("Done")).getAllByTestId("board-card")).toHaveLength(7);
  });

  it("a needs-you gate row carries one button, Approve, which acts without opening the peek (W11 · B.3, B.5)", async () => {
    const approve = vi.spyOn(api, "approveGate").mockResolvedValue(undefined as never);
    vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue();
    setItems(wi({ id: "w3", status: "needs_human", current_node_id: "plan", pending_gate: "plan_approval" }));
    renderBoard();
    const row = within(group("Needs you")).getByTestId("board-card");
    expect([...row.querySelectorAll(".board-row-action button, .board-row-action a")].map((b) => b.textContent)).toEqual(["Approve"]);
    expect(row.querySelector(".gate-inline")).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "Approve" }));
    expect(approve).toHaveBeenCalledWith("w3", "plan_approval");
    expect(screen.queryByLabelText("peek")).toBeNull();
    expect(row).not.toHaveAttribute("data-selected");
  });

  it("puts an escalating item under Running, not Needs you, with a robot glyph, its tail and no button (W11 · J.1, J.2)", () => {
    const esc = escalating("we", "Escalating one", 3);
    setItems(esc.item);
    useStore.setState({ sessionsByItem: { we: esc.sessions }, eventsByItem: { we: esc.events } } as never);
    renderBoard();
    expect(within(group("Needs you")).queryByText("Escalating one")).toBeNull();
    const row = within(group("Running")).getByTestId("board-card");
    expect(row.querySelector('.glyph[data-status="escalating"]')).toBeTruthy();
    expect(row.querySelector(".board-row-reason")).toHaveTextContent("escalation running · 3m");
    expect(row.querySelector(".board-row-reason")).toHaveAttribute("data-tone", "neutral");
    expect(row.querySelectorAll(".board-row-action button, .board-row-action a")).toHaveLength(0);
  });

  it("leads Running with escalating items, whatever the sort (W11 · J.4)", () => {
    const esc = escalating("we", "Escalating one", 3);
    setItems(wi({ id: "w1", status: "active", title: "Newest running", updated_at: "2030-01-01T00:00:00Z" }), esc.item);
    useStore.setState({ sessionsByItem: { we: esc.sessions }, eventsByItem: { we: esc.events } } as never);
    renderBoard();
    const rows = within(group("Running")).getAllByTestId("board-card");
    expect(rows.map((r) => r.querySelector(".board-row-title")?.textContent)).toEqual(["Escalating one", "Newest running"]);
  });

  it("a running row has no button, and keeps the button cell so the columns line up", () => {
    setItems(wi({ id: "w1", status: "active" }));
    renderBoard();
    const row = within(group("Running")).getByTestId("board-card");
    expect(row.querySelector(".board-row-action")).toBeInTheDocument();
    expect(row.querySelectorAll("button, a")).toHaveLength(0);
  });

  it.each<[string, Partial<WorkItem>, RegExp, string | null]>([
    ["gate", { status: "needs_human", current_node_id: "plan", pending_gate: "plan_approval" }, /approve plan_approval$/, "Approve"],
    ["capped", { status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }, /verify hit its cap · 3 attempts$/, "Steer & retry"],
    ["question", { status: "needs_human", needs_context_question: "x".repeat(80) }, /agent asks: x{60}…$/, "Answer"],
    ["budget", { status: "needs_human", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } }, /spend cap reached$/, "Raise budget"],
    ["paused", { status: "paused" }, /paused at verify$/, "Resume"],
    ["running", { status: "active", progress: { current: 3, total: 6, title: "wire the store" } }, /(?<!Task )3 of 6 · wire the store$/, null],
    ["rate limited", { status: "rate_limited", retry_at: new Date(Date.now() + 4 * 60_000 + 30_000).toISOString() }, /retry in 4m$/, null],
  ])("%s: the meta line ends in its reason, and the button matches (W11 · B.2, B.3)", (_, over, reason, button) => {
    setItems(wi({ id: "w9", ...over }));
    renderBoard();
    const row = screen.getByTestId("board-card");
    expect(row.querySelector(".board-row-meta")?.textContent).toMatch(reason);
    expect([...row.querySelectorAll(".board-row-action button")].map((b) => b.textContent)).toEqual(button ? [button] : []);
  });

  it("colours a needs-you reason accent and a running one neutral", () => {
    setItems(
      wi({ id: "w3", status: "needs_human", pending_gate: "plan_approval", current_node_id: "plan" }),
      wi({ id: "w1", status: "active", progress: { current: 1, total: 2, title: "t" } }),
    );
    renderBoard();
    expect(within(group("Needs you")).getByTestId("board-card").querySelector(".board-row-reason")).toHaveAttribute("data-tone", "accent");
    expect(within(group("Running")).getByTestId("board-card").querySelector(".board-row-reason")).toHaveAttribute("data-tone", "neutral");
  });

  it("does not offer a blind Approve for human_review_approval, only a link to the detail view", () => {
    setItems(
      wi({
        id: "w6",
        status: "needs_human",
        current_node_id: "verify",
        pending_gate: "human_review_approval",
      }),
    );
    renderBoard();
    const row = within(group("Needs you")).getByTestId("board-card");
    expect(within(row).queryByRole("button", { name: /approve/i })).toBeNull();
    const link = within(row).getByRole("link", { name: /review to approve/i });
    expect(link).toHaveAttribute("href", "/work-items/w6");
  });

  it("puts a paused-mid-chain item in Needs you, and a never-started item in Not started", () => {
    setItems(
      wi({ id: "w5", status: "paused", current_node_id: "verify", title: "Paused mid-chain" }),
      wi({ id: "w7", status: "paused", current_node_id: null, title: "Never started" }),
    );
    renderBoard();
    expect(within(group("Needs you")).getByText("Paused mid-chain")).toBeInTheDocument();
    expect(within(group("Not started")).getByText("Never started")).toBeInTheDocument();
    expect(within(group("Needs you")).queryByText("Never started")).not.toBeInTheDocument();
    expect(within(group("Running")).queryAllByTestId("board-card")).toHaveLength(0);
  });

  it("filters on the status facet", async () => {
    setItems(
      wi({ id: "w1", status: "active" }),
      wi({ id: "w7", status: "paused", current_node_id: null, title: "Never started" }),
    );
    renderBoard();
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: /^Not started/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
    expect(screen.getByText("Never started")).toBeInTheDocument();
  });

  it("hides groups the status facet did not pick", async () => {
    setItems(
      wi({ id: "w1", status: "active" }),
      wi({ id: "w7", status: "paused", current_node_id: null, title: "Never started" }),
    );
    renderBoard();
    await userEvent.click(screen.getByRole("button", { name: /^Not started/ }));
    expect(screen.queryByText("Running", { selector: ".group-label" })).not.toBeInTheDocument();
    expect(screen.queryByText("Done", { selector: ".group-label" })).not.toBeInTheDocument();
    expect(group("Not started")).toBeInTheDocument();
  });

  it("cmd-clicking a second status facet adds it instead of replacing the first", async () => {
    setItems(
      wi({ id: "w1", status: "active" }),
      wi({ id: "w7", status: "paused", current_node_id: null, title: "Never started" }),
    );
    renderBoard();
    // held modifiers only persist across calls on the same instance (v14)
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^Not started/ }));
    await user.keyboard("{Meta>}");
    await user.click(screen.getByRole("button", { name: /^Running/ }));
    await user.keyboard("{/Meta}");
    expect(group("Running")).toBeInTheDocument();
    expect(group("Not started")).toBeInTheDocument();
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
  });

  it("sorts within a group by the chosen key", async () => {
    setItems(
      wi({ id: "w1", status: "active", title: "Bravo", updated_at: "2024-01-01T00:00:00Z" }),
      wi({ id: "w2", status: "active", title: "Alfa", updated_at: "2024-06-01T00:00:00Z" }),
    );
    renderBoard();
    // default: recently updated first
    let rows = within(group("Running")).getAllByTestId("board-card");
    expect(within(rows[0]).getByText("Alfa")).toBeInTheDocument();

    await userEvent.click(document.querySelector(".board-sort summary") as HTMLElement);
    await userEvent.click(screen.getByRole("button", { name: "title" }));
    rows = within(group("Running")).getAllByTestId("board-card");
    expect(within(rows[0]).getByText("Alfa")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Bravo")).toBeInTheDocument();
  });

  it("offers the four spec sort options and no native select", async () => {
    renderBoard();
    expect(document.querySelector(".board-sort select")).toBeNull();
    await userEvent.click(document.querySelector(".board-sort summary") as HTMLElement);
    for (const label of ["recently updated", "created", "needs attention", "title"]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
  });

  it("falls back to recently updated when localStorage holds a removed sort key", () => {
    localStorage.setItem("kraft.board_filters", JSON.stringify({ sort: "repo" }));
    renderBoard();
    expect(document.querySelector(".board-sort summary")?.textContent).toContain(
      "recently updated",
    );
  });

  it("cuts the title and the repo · bead · template · age meta line to one line each, whole on hover (W11 · B.2)", () => {
    setItems(wi({ id: "w1", title: "A long title" }));
    renderBoard();
    const row = screen.getByTestId("board-card");
    const title = row.querySelector(".board-row-title")!;
    expect(title).toHaveAttribute("title", "A long title");
    expect(title).toHaveAttribute("data-allow-ellipsis");
    const meta = row.querySelector(".board-row-meta")!;
    expect(meta).toHaveAttribute("data-allow-ellipsis");
    expect(meta.getAttribute("title")).toMatch(/^repo-a · B · quick-task/);
  });

  it("groups a rate_limited item under Running, not Needs you", () => {
    setItems(wi({ id: "w3", status: "rate_limited", current_node_id: "implementation" }));
    renderBoard();
    expect(within(group("Running")).getByText("Item")).toBeInTheDocument();
    expect(within(group("Needs you")).queryByText("Item")).not.toBeInTheDocument();
  });

  it("shows the retry time on a rate_limited card", () => {
    setItems(wi({ id: "w3", status: "rate_limited", retry_at: "2026-09-10T05:00:00Z" }));
    renderBoard();
    expect(screen.getByText(/retry/i)).toBeInTheDocument();
  });

  it("groups a waiting item under Running, not Needs you", () => {
    // Kraft-knym: ru98 landed 'waiting' on the backend with no frontend
    // treatment at all, so a parked item matched no board group and vanished
    // from the board entirely -- worse than the "looks hung" bug it was filed
    // to fix.
    setItems(wi({ id: "w3", status: "waiting", current_node_id: "mr_checks" }));
    renderBoard();
    expect(within(group("Running")).getByText("Item")).toBeInTheDocument();
    expect(within(group("Needs you")).queryByText("Item")).not.toBeInTheDocument();
  });

  it("shows the retry time on a waiting card", () => {
    setItems(wi({ id: "w3", status: "waiting", retry_at: "2026-09-10T05:00:00Z" }));
    renderBoard();
    expect(screen.getByText(/retry/i)).toBeInTheDocument();
  });

  it("N of M · title renders in the meta line when progress is set, with no bare task noun", () => {
    setItems(
      wi({ id: "w1", status: "active", progress: { current: 3, total: 6, title: "wire the store" } }),
    );
    renderBoard();
    expect(screen.getByText("3 of 6")).toBeInTheDocument();
    expect(screen.getByText("wire the store")).toBeInTheDocument();
    expect(screen.queryByText(/\btask \d/i)).toBeNull();
    // The ellipsized meta line's tooltip carries the same words.
    expect(document.querySelector(".board-row-meta")?.getAttribute("title")).toMatch(/(?<!Task )3 of 6 · wire the store$/);
  });

  it("plain click toggles the peek param; ⌘-click navigates instead", async () => {
    setItems(wi({ id: "w1" }));
    renderBoard();
    await userEvent.click(screen.getByTestId("board-card"));
    expect(screen.getByTestId("board-card")).toHaveAttribute("data-selected", "true");
    await userEvent.click(screen.getByTestId("board-card"));
    expect(screen.getByTestId("board-card")).not.toHaveAttribute("data-selected");
  });

  it("Select all selects every visible Done row and the floating bar shows the count", async () => {
    setItems(
      wi({ id: "w1", status: "completed" }),
      wi({ id: "w2", status: "abandoned" }),
    );
    renderBoard();
    await userEvent.click(within(group("Done")).getByRole("button", { name: /select all/i }));
    expect(screen.getByText(/2 selected/i)).toBeInTheDocument();
  });

  it("Archive on the floating bar calls the API for every selected id and clears the selection", async () => {
    const spy = vi
      .spyOn(api, "archiveWorkItem")
      .mockResolvedValue({ id: "w1", archived_by: "you", worktree_removed: true });
    vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({ items: [], cursor: 0 });
    setItems(wi({ id: "w1", status: "completed" }));
    renderBoard();
    await userEvent.click(within(group("Done")).getAllByRole("checkbox")[0]);
    await userEvent.click(screen.getByTestId("archive-selected"));
    expect(spy).toHaveBeenCalledWith("w1");
    expect(screen.queryByText(/selected/i)).toBeNull();
  });

  it("archiving says how many and offers Undo for 6s, which restores them (W4.9)", async () => {
    vi.spyOn(api, "archiveWorkItem").mockResolvedValue({ id: "w1", archived_by: "you", worktree_removed: true });
    const restore = vi.spyOn(api, "restoreWorkItem").mockResolvedValue({ id: "w1", status: "completed" });
    const toasts: ToastPayload[] = [];
    const onToast = (e: Event) => toasts.push((e as CustomEvent<ToastPayload>).detail);
    window.addEventListener("kraft:toast", onToast);
    setItems(wi({ id: "w1", status: "completed" }));
    renderBoard();
    await userEvent.click(within(group("Done")).getAllByRole("checkbox")[0]);
    await userEvent.click(screen.getByTestId("archive-selected"));
    await waitFor(() => expect(toasts[0]?.message).toBe("1 item archived"));
    expect(toasts[0].ms).toBe(6000);
    await toasts[0].action!.run();
    expect(restore).toHaveBeenCalledWith("w1");
    window.removeEventListener("kraft:toast", onToast);
  });

  it("a Done row carries no button; archiving is its checkbox (W11 · B.3, B.6)", () => {
    setItems(wi({ id: "w1", status: "completed" }));
    renderBoard();
    const row = within(group("Done")).getByTestId("board-card");
    expect(within(row).getByRole("checkbox")).toBeInTheDocument();
    expect(within(row).queryAllByRole("button")).toHaveLength(0);
  });

  it("long-press opens the peek pane instead of navigating (phone)", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }) as never);
    setItems(wi({ id: "w1" }));
    renderBoard();
    const row = screen.getByTestId("board-card");
    fireEvent.pointerDown(row);
    await new Promise((r) => setTimeout(r, 550));
    fireEvent.pointerUp(row);
    fireEvent.click(row);
    expect(await screen.findByLabelText("peek")).toBeInTheDocument();
  });

  it("a plain tap navigates to the item on phone instead of toggling peek", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }) as never);
    setItems(wi({ id: "w1" }));
    const { container } = render(
      <MemoryRouter
        initialEntries={["/"]}
      >
        <Board />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByTestId("board-card"));
    expect(container.querySelector('[aria-label="peek"]')).toBeNull();
  });

  it("renders the fresh-install card when there are no repos", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    setItems();
    renderBoard();
    expect(await screen.findByText(/nothing on the board yet/i)).toBeInTheDocument();
  });

  it("keeps rendering the board, not the fresh-install card, when getRepos fails", async () => {
    vi.spyOn(api, "getRepos").mockRejectedValue(new Error("boom"));
    renderBoard();
    expect(await screen.findAllByTestId("board-card")).toHaveLength(2);
    expect(screen.queryByText(/nothing on the board yet/i)).toBeNull();
  });

  it("Enter on the row navigates, but Enter bubbling from a focused child does not", async () => {
    setItems(wi({ id: "w1", status: "completed" }));
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<div>item page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    const row = screen.getByTestId("board-card");
    const checkbox = within(row).getByRole("checkbox");

    fireEvent.keyDown(checkbox, { key: "Enter" });
    expect(screen.queryByText("item page")).toBeNull();

    fireEvent.keyDown(row, { key: "Enter" });
    expect(screen.getByText("item page")).toBeInTheDocument();
  });

  it("facet selection survives a remount via localStorage", async () => {
    setItems(wi({ id: "w1", repo: "/repo-a" }), wi({ id: "w2", repo: "/repo-b" }));
    const { unmount } = renderBoard();
    const chip = within(filters()).getByRole("button", { name: /^repo-a/ });
    await userEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
    unmount();
    renderBoard();
    expect(within(filters()).getByRole("button", { name: /^repo-a/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("groups by repo when the board prefs say so", async () => {
    vi.spyOn(api, "getTheme").mockResolvedValue({
      palette: "nocturne",
      mode: "dark",
      density: "compact",
      board: { group_by: "repo", show_done: 5, open_in: "peek" },
    });
    setItems(
      wi({ id: "w1", repo: "/repo-a", status: "active" }),
      wi({ id: "w2", repo: "/repo-b", status: "active" }),
    );
    renderBoard();
    expect(await screen.findAllByText("repo-a")).not.toHaveLength(0);
    expect(screen.getAllByText("repo-b").length).toBeGreaterThan(0);
    expect(screen.queryByText("Running", { selector: ".group-label" })).toBeNull();
  });

  it("groups by template when the board prefs say so, headed by template id (W4.10)", async () => {
    vi.spyOn(api, "getTheme").mockResolvedValue({
      palette: "nocturne",
      mode: "dark",
      density: "compact",
      board: { group_by: "template", show_done: 5, open_in: "peek" },
    });
    setItems(
      wi({ id: "w1", chain_template: "default", status: "active" }),
      wi({ id: "w2", chain_template: "quick-task", status: "active" }),
    );
    renderBoard();
    expect(await screen.findByText("default", { selector: ".group-label" })).toBeInTheDocument();
    expect(screen.getByText("quick-task", { selector: ".group-label" })).toBeInTheDocument();
    expect(screen.queryByText("Running", { selector: ".group-label" })).toBeNull();
  });

  it("respects a configured show-done count", async () => {
    vi.spyOn(api, "getTheme").mockResolvedValue({
      palette: "nocturne",
      mode: "dark",
      density: "compact",
      board: { group_by: "status", show_done: 2, open_in: "peek" },
    });
    setItems(
      ...Array.from({ length: 5 }, (_, i) => wi({ id: `d${i}`, status: "completed" })),
    );
    renderBoard();
    await screen.findByText(/show all 5/);
    expect(group("Done").querySelectorAll(".board-row")).toHaveLength(2);
  });

  it("opens the full page on row click when open_in is full", async () => {
    vi.spyOn(api, "getTheme").mockResolvedValue({
      palette: "nocturne",
      mode: "dark",
      density: "compact",
      board: { group_by: "status", show_done: 5, open_in: "full" },
    });
    render(
      <MemoryRouter
        initialEntries={["/"]}
      >
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<div>item page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    const rows = await screen.findAllByTestId("board-card");
    fireEvent.click(rows[0]);
    expect(screen.getByText("item page")).toBeInTheDocument();
  });
});

/* The board row's grid contract is two facts in two files -- the tracks in
   styles.css and the children BoardRow renders -- and five review cycles in a
   row broke it by moving one without the other. This is the check that fails
   when that happens again: for every breakpoint, tracks must equal in-flow
   children, and any child that has nowhere to auto-place must be placed by
   hand. */
describe("the .board-row grid contract", () => {
  const css = readFileSync("src/styles.css", "utf8") /* vitest root is frontend/ */;

  /** Every `@media (max-width: N)` block, plus the unconditional rules under
   *  `Infinity`, in source order. */
  const blocks: { at: number; body: string }[] = [{ at: Infinity, body: "" }];
  for (let i = 0; i < css.length; ) {
    const open = css.indexOf("@media", i);
    if (open === -1) {
      blocks[0].body += css.slice(i);
      break;
    }
    blocks[0].body += css.slice(i, open);
    let depth = 0;
    let j = css.indexOf("{", open);
    const head = css.slice(open, j);
    for (; j < css.length; j++) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}" && --depth === 0) break;
    }
    const max = head.match(/max-width:\s*(\d+)px/);
    const min = head.match(/min-width:\s*(\d+)px/);
    // min-width blocks never subtract a child or a track from the ladder, and
    // a height query is not a width: only max-width blocks are modelled.
    if (max && !min) blocks.push({ at: Number(max[1]), body: css.slice(open, j) });
    i = j + 1;
  }

  const appliesAt = (w: number) => blocks.filter((b) => w <= b.at);
  const lastMatch = (w: number, re: RegExp) =>
    appliesAt(w).reduce<string | null>((acc, b) => {
      const hits = [...b.body.matchAll(re)];
      return hits.length ? hits[hits.length - 1][1] : acc;
    }, null);

  it("renders exactly the five children the tracks are counted against", () => {
    setItems(wi({ id: "w1", status: "active" }));
    renderBoard();
    const row = screen.getAllByTestId("board-card")[0];
    expect(row.children).toHaveLength(5);
    expect(row.children[1].className).toBe("board-row-main");
    expect(row.children[2].className).toContain("chain-bar");
    expect(row.children[3].className).toBe("board-row-current");
    expect(row.children[4].className).toBe("board-row-action");
  });

  it.each([1500, 1300, 1100, 900, 800, 700, 600])("has one track per in-flow child at %ipx", (w) => {
    const tracks = lastMatch(w, /\.board-row\s*\{[^}]*grid-template-columns:([^;}]+)/g);
    expect(tracks).not.toBeNull();
    const trackCount = tracks!.trim().split(/\s+(?![^(]*\))/).length;

    const hidden = (re: RegExp) => (lastMatch(w, re)?.includes("display: none") ? 1 : 0);
    const inFlow =
      5 -
      hidden(/\.board-row\s*>\s*\.chain-bar\.sm\s*\{([^}]*)\}/g) -
      hidden(/\.board-row\s*>\s*\.board-row-action\s*\{([^}]*)\}/g);
    expect(trackCount).toBe(inFlow);
  });
});
