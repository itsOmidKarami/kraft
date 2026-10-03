import { screen, waitFor, within } from "@testing-library/react";
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
const WRITES = acceptWrites("POST /work-items/w1/open-worktree", "POST /work-items/w1/pause", "POST /work-items/w1/resume", "POST /work-items/w1/retry");

/** The shell reads health and drafts itself; only the item's writes matter here. */
const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

beforeEach(() => useStore.setState({ workItems: {} }));
afterEach(() => vi.unstubAllGlobals());

const show = (over: Parameters<typeof detail>[0] = {}, handlers: Partial<{ reload: () => void; onSettings: () => void; onRunLog: () => void }> = {}) =>
  inShell(<ItemHeader item={detail(over)} reload={handlers.reload ?? (() => {})} onSettings={handlers.onSettings ?? (() => {})} onRunLog={handlers.onRunLog ?? (() => {})} />);

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
      inShell(<ItemDraftProvider item={it} reload={reload}><ItemHeader item={it} reload={reload} onSettings={() => {}} onRunLog={() => {}} /><ReviewDialog /></ItemDraftProvider>, path);
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
  });

  it("opens Item settings for a capped item's Resume instead of resuming", async () => {
    const calls = stubFetch();
    const onSettings = vi.fn();
    show({ display_status: "needs_you", stop: { kind: "cap", node: "verification", resume_at: null, reason: null } }, { onSettings });
    await userEvent.click(screen.getByRole("button", { name: /Resume/ }));
    expect(onSettings).toHaveBeenCalled();
    expect(writes(calls)).toEqual([]);
  });

  it("puts every ⋮ item on its route, the copied link on the shipped path, and Cancel… in ⋮ too", async () => {
    const calls = stubFetch({ ...WRITES, "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "b", worktree: "/w", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } }] });
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    const onSettings = vi.fn();
    const onRunLog = vi.fn();
    show({}, { onSettings, onRunLog });
    const menu = async (name: RegExp) => {
      await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
      await userEvent.click(screen.getByRole("menuitem", { name }));
    };
    await menu(/Item settings/);
    expect(onSettings).toHaveBeenCalled();
    await menu(/View run log/);
    expect(onRunLog).toHaveBeenCalled();
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
