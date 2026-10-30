import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../../store";
import { item } from "../../../testFixtures";
import { ItemDraftProvider } from "../draft/context";
import { ReviewDialog } from "../draft/ReviewDialog";
import { answer, ov } from "../draft/testkit";
import { acceptWrites, detail, inShell, stubFetch } from "../testkit";
import { ItemHeader } from "./ItemHeader";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/open-worktree", "POST /work-items/w1/pause", "POST /work-items/w1/resume", "POST /work-items/w1/retry", "POST /work-items/w1/reopen-mr");

/** The shell reads health and drafts itself; only the item's writes matter here. */
const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

beforeEach(() => useStore.setState({ workItems: {} }));
afterEach(() => vi.unstubAllGlobals());

type Handlers = { reload: () => void; onSettings: () => void; onRaise: () => void; onGate: (gate: string) => void; onAnswer: () => void };
const show = (over: Parameters<typeof detail>[0] = {}, handlers: Partial<Handlers> = {}, bodyActs?: boolean) =>
  inShell(<ItemHeader bodyActs={bodyActs} item={detail(over)} reload={handlers.reload ?? (() => {})} onSettings={handlers.onSettings ?? (() => {})} onRaise={handlers.onRaise} onGate={handlers.onGate} onAnswer={handlers.onAnswer} />);

describe("ItemHeader", () => {
  it("counts the others that need you, never this item, and hides at zero", () => {
    useStore.setState({ workItems: { w1: item({ id: "w1", display_status: "needs_you" }), a: item({ id: "a", display_status: "needs_you" }), b: item({ id: "b", display_status: "needs_you" }), c: item({ id: "c", display_status: "running" }) } });
    const { unmount } = show({ display_status: "needs_you" });
    expect(screen.getByRole("link", { name: /2 others need you/ })).toHaveAttribute("href", "/");
    unmount();
    useStore.setState({ workItems: { w1: item({ id: "w1", display_status: "needs_you" }) } });
    show({ display_status: "needs_you" });
    expect(screen.queryByText(/need you/)).toBeNull();
  });

  it("counts the others as the board's Needs you group does: failed and paused mid-chain too, never started not", () => {
    useStore.setState({ workItems: {
      w1: item({ id: "w1", display_status: "needs_you" }),
      a: item({ id: "a", display_status: "needs_you" }),
      f: item({ id: "f", display_status: "failed" }),
      p: item({ id: "p", status: "paused", display_status: "paused", current_node_id: "implementation" }),
      n: item({ id: "n", status: "paused", display_status: "paused", current_node_id: null }),
    } });
    show({ display_status: "needs_you" });
    expect(screen.getByRole("link", { name: /3 others need you/ })).toBeInTheDocument();
  });

  // R11b-11: the header's clock stood still while the pane's counted each second.
  it("stops its clock when the item ended, not when archiving last touched it", () => {
    show({ display_status: "archived", status: "completed", started_at: "2026-09-13T08:00:00Z", ended_at: "2026-09-13T09:00:00Z", updated_at: "2026-10-13T09:00:00Z" });
    expect(document.querySelector(".item-elapsed")).toHaveTextContent(/^1h$/);
  });

  // Filed at 08:00:00; a wait behind a dependency before its first node is not run time.
  it.each([
    { id: "never started: from filing", started_at: null, first: "12s", later: "16s" },
    { id: "from its first node", started_at: "2026-09-13T08:00:10Z", first: "2s", later: "6s" },
  ])("ticks its elapsed clock every second while something runs ($id)", async ({ started_at, first, later }) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      vi.setSystemTime(Date.parse("2026-09-13T08:00:12Z"));
      const running = { id: "s1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1 } as never;
      show({ worker_sessions: [running], started_at });
      expect(document.querySelector(".item-elapsed")).toHaveTextContent(new RegExp(`^${first}$`));
      await act(async () => void vi.advanceTimersByTime(4_000));
      expect(document.querySelector(".item-elapsed")).toHaveTextContent(new RegExp(`^${later}$`));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the server's badge and retries a failed item from the stopped task's path", async () => {
    const calls = stubFetch(WRITES);
    const reload = vi.fn();
    show({ display_status: "failed", stop: { kind: "failed", node: "merge_request", task: "merge_request.open.open_draft", attempt: 3, resume_at: null, reason: "403" } }, { reload });
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "merge_request.open.open_draft" } }]);
  });

  it("says an in-place item runs in your checkout, beside the status", () => {
    const { unmount } = show({ display_status: "running", in_place: true, worktree_path: "/src/app" });
    // The path is in the house tip, on a chip a keyboard can reach, and described to a screen reader.
    // A note, not a generic span: ARIA lets a description sit on a note.
    const chip = screen.getByRole("note");
    expect(chip).toHaveTextContent("in your checkout");
    expect(chip).toHaveAttribute("data-tip", "/src/app");
    expect(chip).toHaveAttribute("tabindex", "0");
    expect(chip).toHaveAccessibleDescription("/src/app");
    unmount();
    show({ display_status: "running" });
    expect(screen.queryByText("in your checkout")).toBeNull();
  });

  it("calls a never-started item NOT STARTED with Start, which resumes it from the first node", async () => {
    const calls = stubFetch(WRITES);
    const reload = vi.fn();
    show({ status: "paused", display_status: "paused", current_node_id: null }, { reload });
    expect(screen.getByText("NOT STARTED")).toBeInTheDocument();
    expect(screen.queryByText("PAUSED")).toBeNull();
    expect(screen.queryByRole("button", { name: /Resume/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/resume", body: { steer: null } }]);
  });

  describe("Start on a one-off chain nobody approved", () => {
    const NEVER = { status: "paused", display_status: "paused", current_node_id: null, one_off: { extends: "default", custom: 1, needs_review: true, approval: null } } as const;
    const REVIEW = { custom: [{ path: "add[0].node", what: "adds k6", value: "run k6" }], provenance: null, extends: "default", digest: "d".repeat(64), needs_review: true, added: [], redrafted: [] };
    const ROUTES = { ...WRITES, "GET /work-items/w1/one-off": [200, REVIEW] as [number, unknown], "POST /work-items/w1/one-off/approve": [200, {}] as [number, unknown] };
    const sends = (calls: { method: string; path: string }[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);

    it("opens the review and resumes nothing until Approve and start", async () => {
      const calls = stubFetch(ROUTES);
      show(NEVER);
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      const d = await screen.findByRole("dialog", { name: /Approve this chain, then start/ });
      expect(sends(calls)).toEqual([]);
      await userEvent.click(within(d).getByRole("button", { name: "Approve and start" }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/one-off/approve", "POST /work-items/w1/resume"]));
    });

    it("reads the chain on Start whatever the page last saw: a prop that says approved, a read that says not, opens the review with no resume", async () => {
      const calls = stubFetch(ROUTES);
      show({ ...NEVER, one_off: { extends: "default", custom: 1, needs_review: false, approval: null } });
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      await screen.findByRole("dialog", { name: /Approve this chain, then start/ });
      expect(sends(calls)).toEqual([]);
    });

    it("starts at once, with no dialog, when the read says nothing needs approving", async () => {
      const calls = stubFetch({ ...ROUTES, "GET /work-items/w1/one-off": [200, { ...REVIEW, needs_review: false }] });
      show(NEVER);
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/resume"]));
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("Resume on a one-off item that already ran resumes in place: no chain read, no review", async () => {
      const calls = stubFetch(ROUTES);
      show({ status: "paused", display_status: "paused", current_node_id: "implementation", one_off: { extends: "default", custom: 1, needs_review: false, approval: null } });
      await userEvent.click(screen.getByRole("button", { name: /Resume/ }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/resume"]));
      expect(calls.some((c) => c.path.endsWith("/one-off"))).toBe(false);
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("the draft's Apply and start approves before it resumes", async () => {
      const calls = stubFetch({ ...ROUTES, "GET /work-items/w1/draft": answer([ov("implementation", undefined, { budget_usd: 2 })]), "POST /work-items/w1/draft/apply": answer([]) });
      const it = detail(NEVER);
      inShell(<ItemDraftProvider item={it} reload={() => {}}><ItemHeader item={it} reload={() => {}} onSettings={() => {}} /><ReviewDialog /></ItemDraftProvider>);
      await screen.findByText("DRAFT · 1 CHANGE");
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      await userEvent.click(within(await screen.findByRole("dialog", { name: "Start with 1 unapplied change?" })).getByRole("button", { name: "Apply and start" }));
      await userEvent.click(await screen.findByRole("button", { name: "Approve and start" }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/draft/apply", "POST /work-items/w1/one-off/approve", "POST /work-items/w1/resume"]));
    });
  });

  describe("Start with a draft", () => {
    const NEVER = { status: "paused", display_status: "paused", current_node_id: null } as const;
    const DRAFT = { ...WRITES, "GET /work-items/w1/draft": answer([ov("implementation", undefined, { budget_usd: 2 })]), "POST /work-items/w1/draft/apply": answer([]) };
    const mount = (reload = vi.fn(), path?: string) => {
      const it = detail(NEVER);
      inShell(<ItemDraftProvider item={it} reload={reload}><ItemHeader item={it} reload={reload} onSettings={() => {}} /><ReviewDialog /></ItemDraftProvider>, path);
      return reload;
    };
    // The draft's own read and the shell's are reads: only these two matter.
    const sends = (calls: { method: string; path: string }[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);

    it("asks first instead of starting past the draft, and Apply and start applies it before it starts", async () => {
      const calls = stubFetch(DRAFT);
      const reload = mount();
      await screen.findByText("DRAFT · 1 CHANGE");
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      const d = await screen.findByRole("dialog", { name: "Start with 1 unapplied change?" });
      expect(sends(calls)).toEqual([]);
      await userEvent.click(within(d).getByRole("button", { name: "Apply and start" }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/draft/apply", "POST /work-items/w1/resume"]));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(reload).toHaveBeenCalled();
    });

    it("starts without applying only when told to, leaving the draft", async () => {
      const calls = stubFetch(DRAFT);
      mount();
      await screen.findByText("DRAFT · 1 CHANGE");
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Start without them" }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/resume"]));
    });

    it("arriving with ?start=1 (a Start from the peek) asks the same question, once, and starts only when told", async () => {
      const calls = stubFetch(DRAFT);
      mount(vi.fn(), "/work-items/w1?start=1");
      const d = await screen.findByRole("dialog", { name: "Start with 1 unapplied change?" });
      expect(sends(calls)).toEqual([]);
      await userEvent.click(within(d).getByRole("button", { name: "Start without them" }));
      await waitFor(() => expect(sends(calls)).toEqual(["POST /work-items/w1/resume"]));
    });

    // R10b-04 / R10b-05: focus opens on the safe default and never falls to the page.
    it("opens with focus on Apply and start, not on Discard draft", async () => {
      stubFetch(DRAFT);
      mount();
      await screen.findByText("DRAFT · 1 CHANGE");
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      const d = await screen.findByRole("dialog", { name: "Start with 1 unapplied change?" });
      expect(within(d).getByRole("button", { name: "Apply and start" })).toHaveFocus();
    });

    it("keeps focus on the header's main button while Start without them runs, which it does not disable", async () => {
      let release!: () => void;
      const held = new Promise<void>((r) => (release = r));
      stubFetch(DRAFT);
      const base = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (String(input).endsWith("/resume")) await held;
        return base(input as string, init);
      });
      mount();
      await screen.findByText("DRAFT · 1 CHANGE");
      const startButton = screen.getByRole("button", { name: /^Start$/ });
      await userEvent.click(startButton);
      await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Start without them" }));
      await waitFor(() => expect(startButton).toHaveAttribute("aria-disabled", "true"));
      expect(startButton).not.toBeDisabled();
      expect(startButton).toHaveFocus();
      release();
    });

    it("hands focus to the header's main button on Escape when it was opened from ?start=1, with nothing focused", async () => {
      stubFetch(DRAFT);
      mount(vi.fn(), "/work-items/w1?start=1");
      await screen.findByRole("dialog", { name: "Start with 1 unapplied change?" });
      await userEvent.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(screen.getByRole("button", { name: /^Start$/ })).toHaveFocus();
    });

    it("reads the draft again at Start: one made in another tab since this page read it asks first (R10b-07)", async () => {
      const answers: Record<string, [number, unknown]> = { ...DRAFT, "GET /work-items/w1/draft": answer([]) };
      const calls = stubFetch(answers);
      mount();
      await waitFor(() => expect(calls.some((c) => c.path === "/work-items/w1/draft")).toBe(true));
      expect(screen.queryByText(/DRAFT ·/)).toBeNull();
      // Another tab saves a draft; this page has not read it.
      answers["GET /work-items/w1/draft"] = answer([ov("implementation", undefined, { budget_usd: 2 })]);
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      expect(await screen.findByRole("dialog", { name: "Start with 1 unapplied change?" })).toBeInTheDocument();
      expect(sends(calls)).toEqual([]);
    });

    it("Back to editing starts nothing", async () => {
      const calls = stubFetch(DRAFT);
      mount();
      await screen.findByText("DRAFT · 1 CHANGE");
      await userEvent.click(screen.getByRole("button", { name: /^Start$/ }));
      await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Back to editing" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(sends(calls)).toEqual([]);
    });
  });

  it("asks before pausing, then pauses", async () => {
    const calls = stubFetch(WRITES);
    show();
    await userEvent.click(screen.getByRole("button", { name: /^Pause$/ }));
    const card = screen.getByRole("dialog", { name: "Pause this item?" });
    expect(writes(calls)).toEqual([]);
    await userEvent.click(within(card).getByRole("button", { name: "Pause now" }));
    await waitFor(() => expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/pause", body: {} }]));
    // R12b-08: the card and its Pause now are gone; the focus fell to the page.
    await waitFor(() => expect(screen.getByRole("button", { name: /^Pause$/ })).toHaveFocus());
  });

  it("words a queued item's pause as leaving the queue, in the button and in what it asks", async () => {
    const calls = stubFetch(WRITES);
    show({ display_status: "queued" });
    expect(screen.queryByRole("button", { name: /^Pause$/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Remove from queue" }));
    const card = screen.getByRole("dialog", { name: "Remove this item from the queue?" });
    expect(card).not.toHaveTextContent(/running task/);
    await userEvent.click(within(card).getByRole("button", { name: "Remove from queue" }));
    await waitFor(() => expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/pause", body: {} }]));
  });

  // R12b-08: a card's own button had the focus, and went with the card once its action was done.
  it.each([
    ["Cancel…", "More actions", "Cancel this item?", "Reason", "Cancel item", "POST /work-items/w1/cancel"],
    ["Escalate…", "More actions", "Escalate this item", "Message", "Escalate", "POST /work-items/w1/escalate"],
  ])("hands the focus to the main button once %s is done", async (opener, door, card, field, send, route) => {
    stubFetch({ ...WRITES, ...acceptWrites(route), "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "b", worktree: "/w", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } }] });
    show({ display_status: "failed", status: "needs_human", stop: { kind: "failed", node: "verification", task: null, resume_at: null, reason: null } });
    await userEvent.click(screen.getByRole("button", { name: door }));
    await userEvent.click(screen.getByRole("menuitem", { name: opener }));
    const dialog = await screen.findByRole("dialog", { name: card });
    await userEvent.type(within(dialog).getByRole("textbox", { name: new RegExp(field) }), "not needed");
    await userEvent.click(within(dialog).getByRole("button", { name: send }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: card })).toBeNull());
    await waitFor(() => expect(document.querySelector(".item-main-action")).toHaveFocus());
  });

  it("tells the Escalate card the item's escalation threads (WI-15)", async () => {
    stubFetch(WRITES);
    show({ display_status: "failed", status: "needs_human", stop: { kind: "failed", node: "verification", task: null, resume_at: null, reason: null }, escalation_threads: [{ thread: 1, session_id: "s", turns: 2, started_at: "t", ended_at: null, status: "done" }] });
    // Escalate… is the main button's menu's only: the ⋮ beside it does not repeat it.
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    expect(screen.queryByRole("menuitem", { name: /Escalate/ })).toBeNull();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Escalate…" }));
    expect(await screen.findByText("continues thread 1 (turn 3), so it remembers the earlier turns")).toBeInTheDocument();
  });

  // F4: the server's `actions` refuse every way on: no main button, and its panel's doors are on the ⋮ instead.
  it("with no main button the server admits, puts the panel's doors on the ⋮, and they act", async () => {
    stubFetch(WRITES);
    show({ display_status: "failed", status: "needs_human", stop: { kind: "failed", node: "verification", task: null, resume_at: null, reason: null }, actions: ["escalate", "complete", "cancel"], escalation_threads: [{ thread: 1, session_id: "s", turns: 2, started_at: "t", ended_at: null, status: "done" }] });
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent).slice(-3)).toEqual(["Escalate…", "Mark complete…", "Cancel…"]);
    await userEvent.click(screen.getByRole("menuitem", { name: "Escalate…" }));
    expect(await screen.findByText("continues thread 1 (turn 3), so it remembers the earlier turns")).toBeInTheDocument();
  });

  // R11b-01: /pause answers every stopped item 409, so a needs-you stop's main button is its way on, never Pause.
  it.each([
    ["cap", /Raise cap/, { handler: "onRaise" as const }],
    ["gate", /Open gate/, { handler: "onGate" as const, arg: "verification" }],
    ["question", /Answer/, { handler: "onAnswer" as const }],
    ["mr_closed", /Reopen MR/, { write: { method: "POST", path: "/work-items/w1/reopen-mr", body: {} } }],
    ["stuck", /Retry/, { write: { method: "POST", path: "/work-items/w1/retry", body: { path: "verification" } } }],
    // R12b-01: a stuck fix loop stops on its judge, a task no retry path names: 422 until it retried the node.
    ["stuck", /Retry/, { task: "verification.fix_loop.judge", write: { method: "POST", path: "/work-items/w1/retry", body: { path: "verification" } } }],
  ] as const)("a needs-you %s stop's main button is %s, and it acts", async (kind, name, want: { handler?: keyof Handlers; arg?: string; write?: object; task?: string }) => {
    const calls = stubFetch(WRITES);
    const handlers = { onRaise: vi.fn(), onGate: vi.fn(), onAnswer: vi.fn() };
    show({ display_status: "needs_you", status: "needs_human", stop: { kind, node: "verification", task: want.task ?? null, resume_at: null, reason: null, ...(kind === "cap" && { limit: { path: "", key: "time_cap_minutes", value: 60, maximum: null } }) } }, handlers);
    expect(screen.queryByRole("button", { name: /Pause/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name }));
    if (want.handler) {
      expect(handlers[want.handler as keyof typeof handlers]).toHaveBeenCalledWith(...(want.arg ? [want.arg] : []));
      expect(writes(calls)).toEqual([]);
    } else await waitFor(() => expect(writes(calls)).toEqual([want.write]));
  });

  // One primary per state: on the item page a state whose banner or card carries its action has no main button in the header,
  // and the panel's doors it would have held are on the ⋮; a state with no card keeps its button (a null `moved`).
  const stop = (kind: string, more: object = {}) => ({ stop: { kind, node: "verification", task: null, resume_at: null, reason: null, ...more } as never });
  it.each<[string, Parameters<typeof detail>[0], string | false | null]>([
    ["a gate", { display_status: "needs_you", ...stop("gate") }, "Cancel…"],
    ["a question", { display_status: "needs_you", needs_context_question: "Which?", ...stop("question") }, "Cancel…"],
    ["a cap", { display_status: "needs_you", ...stop("cap", { limit: { path: "", key: "time_cap_minutes", value: 60, maximum: null } }) }, "Cancel…"],
    ["conflicts", { display_status: "needs_you", ...stop("conflict") }, "Cancel…"],
    ["a closed MR", { display_status: "needs_you", ...stop("mr_closed") }, "Cancel…"],
    ["a stuck loop", { display_status: "needs_you", ...stop("stuck") }, "Cancel…"],
    ["a failed item", { display_status: "failed", ...stop("failed") }, "Cancel…"],
    ["a paused item", { display_status: "paused", current_node_id: "verification" }, "Cancel…"],
    ["a cancelled item", { display_status: "cancelled" }, "Archive"],
    ["a running item", { display_status: "running" }, null],
    // Its card holds Archive: the header keeps neither a main button nor a menu item for it.
    ["a done item", { display_status: "done" }, false],
    ["a never-started item", { display_status: "paused", current_node_id: null }, null],
    ["an escalated item", { display_status: "escalated" }, null],
    ["a question with no text", { display_status: "needs_you", ...stop("question") }, null],
    ["a budget the item cannot raise", { display_status: "needs_you", ...stop("budget", { scope: "daily" }) }, null],
  ])("on the item page, %s", async (_name, over, moved) => {
    show(over, {}, true);
    expect(!!document.querySelector(".item-main-action")).toBe(moved === null);
    if (moved === null) return;
    if (moved === false) {
      await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
      return expect(screen.queryByRole("menuitem", { name: "Archive" })).toBeNull();
    }
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    expect(screen.getByRole("menuitem", { name: moved })).toBeInTheDocument();
  });

  it("puts every ⋮ item on its route, and the copied link on the shipped path; Cancel… is the main menu's only (WI-13)", async () => {
    const calls = stubFetch({ ...WRITES, "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "b", worktree: "/w", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } }] });
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    const onSettings = vi.fn();
    show({}, { onSettings });
    const menu = async (name: RegExp) => {
      await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
      await userEvent.click(screen.getByRole("menuitem", { name }));
    };
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Review changes", "Item settings", "Open worktree in editor", "Copy ID", "Copy link"]);
    await userEvent.keyboard("{Escape}");
    await menu(/Item settings/);
    expect(onSettings).toHaveBeenCalled();
    await menu(/Copy ID/);
    await menu(/Copy link/);
    expect(writeText.mock.calls).toEqual([["w1"], [`${window.location.origin}/work-items/w1`]]);
    await menu(/Open worktree/);
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/work-items/w1/open-worktree", body: { editor: null } }));
    // The review page (W8): the shell's crumbs end in it.
    await menu(/Review changes/);
    expect(await screen.findByText("Review changes", { selector: '[aria-current="page"]' })).toBeInTheDocument();
  });

  it("disables Open worktree once the worktree is gone, and says Retry brings it back only on a live item, and that a never-started one is not started yet", async () => {
    const calls = stubFetch({});
    const open = async () => {
      await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
      return screen.getByRole("menuitem", { name: /Open worktree/ });
    };
    const { unmount } = show({ worktree_exists: false });
    const live = await open();
    expect(live).toBeDisabled();
    expect(live).toHaveTextContent("worktree removed · Retry recreates it");
    await userEvent.click(live);
    expect(writes(calls)).toEqual([]);
    unmount();
    const { unmount: unmount2 } = show({ worktree_exists: false, display_status: "done" });
    const ended = await open();
    expect(ended).toBeDisabled();
    expect(ended).toHaveTextContent(/^Open worktree in editorworktree removed$/);
    unmount2();
    // Never made, so nothing was removed and Retry has nothing to recreate (R13b-04).
    show({ worktree_exists: false, display_status: "paused", current_node_id: null });
    const fresh = await open();
    expect(fresh).toBeDisabled();
    expect(fresh).toHaveTextContent(/^Open worktree in editornot started yet$/);
  });

  it("offers Duplicate on an ended item and opens the copy", async () => {
    stubFetch({ "POST /work-items/w1/duplicate": [201, { id: "w2", status: "paused" }] });
    show({ display_status: "cancelled" });
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    expect(screen.queryByRole("menuitem", { name: /Cancel…/ })).toBeNull();
    await userEvent.click(screen.getByRole("menuitem", { name: /Duplicate as new item/ }));
    // The route the copy lands on is the item page's: the header (rendered for any path here) stays.
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("w2"));
  });
});
