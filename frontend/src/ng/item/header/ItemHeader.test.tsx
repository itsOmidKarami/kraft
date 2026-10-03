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
const show = (over: Parameters<typeof detail>[0] = {}, handlers: Partial<Handlers> = {}) =>
  inShell(<ItemHeader item={detail(over)} reload={handlers.reload ?? (() => {})} onSettings={handlers.onSettings ?? (() => {})} onRaise={handlers.onRaise} onGate={handlers.onGate} onAnswer={handlers.onAnswer} />);

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
  it("ticks its elapsed clock every second while something runs", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      vi.setSystemTime(Date.parse("2026-09-13T08:00:12Z"));
      const running = { id: "s1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1 } as never;
      show({ worker_sessions: [running] });
      expect(document.querySelector(".item-elapsed")).toHaveTextContent("12s");
      await act(async () => void vi.advanceTimersByTime(4_000));
      expect(document.querySelector(".item-elapsed")).toHaveTextContent("16s");
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

  // R12b-08: a card's own button had the focus, and went with the card once its action was done.
  it.each([
    ["Cancel…", "Cancel this item?", "Reason", "Cancel item", "POST /work-items/w1/cancel"],
    ["Escalate…", "Escalate this item", "Message", "Escalate", "POST /work-items/w1/escalate"],
  ])("hands the focus to the main button once %s is done", async (opener, card, field, send, route) => {
    stubFetch({ ...WRITES, ...acceptWrites(route), "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "b", worktree: "/w", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } }] });
    show({ display_status: "failed", status: "needs_human", stop: { kind: "failed", node: "verification", task: null, resume_at: null, reason: null } });
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    await userEvent.click(screen.getByRole("menuitem", { name: opener }));
    const dialog = await screen.findByRole("dialog", { name: card });
    await userEvent.type(within(dialog).getByRole("textbox", { name: new RegExp(field) }), "not needed");
    await userEvent.click(within(dialog).getByRole("button", { name: send }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: card })).toBeNull());
    await waitFor(() => expect(document.querySelector(".item-main-action")).toHaveFocus());
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
    show({ display_status: "needs_you", status: "needs_human", stop: { kind, node: "verification", task: want.task ?? null, resume_at: null, reason: null } }, handlers);
    expect(screen.queryByRole("button", { name: /Pause/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name }));
    if (want.handler) {
      expect(handlers[want.handler as keyof typeof handlers]).toHaveBeenCalledWith(...(want.arg ? [want.arg] : []));
      expect(writes(calls)).toEqual([]);
    } else await waitFor(() => expect(writes(calls)).toEqual([want.write]));
  });

  it("puts every ⋮ item on its route, the copied link on the shipped path, and Cancel… in ⋮ too", async () => {
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
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Review changes", "Item settings", "Open worktree in editor", "Copy ID", "Copy link", "Cancel…"]);
    await userEvent.keyboard("{Escape}");
    await menu(/Item settings/);
    expect(onSettings).toHaveBeenCalled();
    await menu(/Copy ID/);
    await menu(/Copy link/);
    expect(writeText.mock.calls).toEqual([["w1"], [`${window.location.origin}/work-items/w1`]]);
    await menu(/Open worktree/);
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/work-items/w1/open-worktree", body: { editor: null } }));
    await menu(/Cancel…/);
    expect(await screen.findByRole("dialog", { name: "Cancel this item?" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    // The review page (W8): the shell's crumbs end in it.
    await menu(/Review changes/);
    expect(await screen.findByText("Review changes", { selector: '[aria-current="page"]' })).toBeInTheDocument();
  });

  it("disables Open worktree once the worktree is gone, and says Retry brings it back only on a live item", async () => {
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
    show({ worktree_exists: false, display_status: "done" });
    const ended = await open();
    expect(ended).toBeDisabled();
    expect(ended).toHaveTextContent(/^Open worktree in editorworktree removed$/);
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
