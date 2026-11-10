import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { GateView } from "./GateView";

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "graph.css"), "utf-8");

describe("GateView", () => {
  it("labels the diamond 'you' after a reviewer, and every part is a button", async () => {
    const user = userEvent.setup();
    const cb = { onReviewer: vi.fn(), onGate: vi.fn(), doc: vi.fn(), reject: vi.fn() };
    render(
      <GateView
        gate={{ id: "review_gate", state: "current" }}
        reviewer={{ id: "auto_review", state: "done", chip: "approve", chipTone: "green" }}
        doc={{ label: "review.md", onClick: cb.doc }}
        reject={{ id: "implement", onClick: cb.reject }}
        onReviewer={cb.onReviewer}
        onGate={cb.onGate}
      />,
    );
    expect(screen.getByText("decides after the agent")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "you, gate, running" }));
    await user.click(screen.getByRole("button", { name: "auto_review, reviewer task, done" }));
    await user.click(screen.getByRole("button", { name: /review\.md/ }));
    await user.click(screen.getByRole("button", { name: "reject to implement" }));
    expect([cb.onGate, cb.onReviewer, cb.doc, cb.reject].map((f) => f.mock.calls.length)).toEqual([1, 1, 1, 1]);
  });

  it("names the reject target by its label, not its id", () => {
    render(<GateView gate={{ id: "approve_plan" }} reject={{ id: "write_plan", label: "write plan" }} />);
    expect(screen.getByRole("button", { name: "reject to write plan" })).toHaveTextContent("write plan");
  });

  it("names the diamond by the gate without a reviewer or a slot", () => {
    render(<GateView gate={{ id: "approve_plan" }} />);
    expect(screen.getByRole("button", { name: "approve_plan, gate" })).toBeInTheDocument();
    expect(screen.getByText("No message yet.")).toBeInTheDocument();
    expect(screen.queryByText("add a reviewer")).toBeNull();
  });

  it.each([
    ["checks_first", "checks first"],
    ["decides", "decides first"],
    [undefined, "runs first, before you"],
  ] as const)("words a reviewer with no run state by its mode (%s)", (mode, badge) => {
    render(<GateView gate={{ id: "approve_plan" }} reviewer={{ id: "check", mode }} />);
    expect(screen.getByRole("button", { name: /check, reviewer task/ })).toHaveTextContent(badge);
  });

  it("offers the add-a-reviewer slot in the editor", async () => {
    const onAdd = vi.fn();
    render(<GateView gate={{ id: "approve_plan" }} onAdd={onAdd} />);
    expect(screen.getByRole("button", { name: /you, gate/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /add a reviewer/ }));
    expect(onAdd).toHaveBeenCalled();
  });

  const rounds = (selected: number) => ({ rows: [{ n: 1, tone: "bad" as const, outcome: "changes requested · assistant" }, { n: 2, tone: "ok" as const, outcome: "approved · assistant" }], selected, latest: 2 });

  it("shows the round picker only with rounds, and a way back to the newest from an older one", async () => {
    const onRound = vi.fn();
    const { rerender } = render(<GateView gate={{ id: "approve_plan" }} />);
    expect(screen.queryByRole("button", { name: /round/ })).toBeNull();
    rerender(<GateView gate={{ id: "approve_plan" }} rounds={rounds(2)} onRound={onRound} />);
    expect(screen.getByRole("button", { name: /round 2 · latest/ })).toBeInTheDocument();
    rerender(<GateView gate={{ id: "approve_plan" }} rounds={rounds(1)} onRound={onRound} />);
    expect(screen.getByRole("button", { name: /^round 1/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "latest ↩" }));
    expect(onRound).toHaveBeenCalledWith(undefined);
    // Its menu is named for a gate's rounds, with no fix-loop limit.
    await userEvent.click(screen.getByRole("button", { name: /^round 1/ }));
    expect(screen.getByRole("menu", { name: "Rounds of this gate" })).toBeInTheDocument();
    expect(screen.queryByText(/limit:/)).toBeNull();
    await userEvent.click(screen.getByRole("menuitemradio", { name: /Round 2 · now/ }));
    expect(onRound).toHaveBeenLastCalledWith(undefined);
  });

  // The real note on 510ysadf was about 700 characters.
  const LONG = "The plan skips the invalidation story. Add a section on what happens when git_scan sees a blob_sha change mid-query. ".repeat(6).trim();
  const TWO = "Use `git_scan` here, not **a walk**: see [the spec](https://example.test/spec).\n\n- the second paragraph is a list";

  describe("a round's note", () => {
    // jsdom lays nothing out: say whether the text overflows its three lines.
    const overflow = (over: boolean) => {
      vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(over ? 200 : 100);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(100);
    };
    afterEach(() => vi.restoreAllMocks());

    it("previews its first paragraph only, rendered as markdown", () => {
      overflow(false);
      render(<GateView gate={{ id: "approve_plan" }} said={{ note: TWO, at: "t", n: 1 }} />);
      const preview = document.querySelector(".gateview-said .note-preview")!;
      expect(preview.querySelector("code")).toHaveTextContent("git_scan");
      expect(preview.querySelector("strong")).toHaveTextContent("a walk");
      expect(within(preview as HTMLElement).getByRole("link", { name: "the spec" })).toHaveAttribute("href", "https://example.test/spec");
      expect(preview).not.toHaveTextContent("second paragraph");
      expect(preview).not.toHaveTextContent("`");
      expect(preview).not.toHaveTextContent("**");
    });

    // "Read the note" is for a note that has more than the preview shows.
    it.each([
      ["a second paragraph", TWO, false, true],
      ["one paragraph the clip cuts", LONG, true, true],
      ["one paragraph that fits", "Short.", false, false],
    ])("has the Read the note button for %s: %s", async (_name, note, over, shown) => {
      overflow(over);
      const onRead = vi.fn();
      render(<GateView gate={{ id: "approve_plan" }} said={{ note, at: "t", n: 1, onRead }} />);
      const button = screen.queryByRole("button", { name: "Read the note" });
      expect(button !== null).toBe(shown);
      if (!button) return;
      await userEvent.click(button);
      expect(onRead).toHaveBeenCalledWith(button);
    });

    it("has no button, and nothing that opens in place, with nowhere to read it", () => {
      overflow(true);
      render(<GateView gate={{ id: "approve_plan" }} said={{ note: TWO, at: "t", n: 1 }} />);
      expect(screen.queryByRole("button", { name: /Read the note|Show all|Show less/ })).toBeNull();
    });

    // The clamp is on the text, not on the padded box, which would show half of a fourth line under the ellipsis.
    it("clamps the preview's text at three lines, not the padded box", () => {
      const ui = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../ui/ui.css"), "utf-8");
      const rule = (sheet: string, selector: string) => new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")} \\{([^}]*)\\}`).exec(sheet)?.[1] ?? "";
      expect(rule(ui, ".note-preview")).toContain("-webkit-line-clamp: 3");
      expect(rule(css, ".gateview-said")).not.toContain("line-clamp");
      expect(rule(css, ".gateview-said")).toContain("padding");
    });
  });

  it.each([
    ["sent work back: the target is drawn solid and named", { id: "plan", label: "plan", taken: true }, "rejected to", "rejected to plan", true],
    ["is only a branch yet: dashed", { id: "plan", label: "plan" }, "reject", "reject to plan", false],
  ])("a round that %s", (_name, reject, word, label, solid) => {
    render(<GateView gate={{ id: "approve_plan" }} reject={reject} />);
    expect(screen.getByText(word)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: label }).classList.contains("is-taken")).toBe(solid);
  });

  it("says fixed, with no target, for a round the reviewer repaired itself", () => {
    render(<GateView gate={{ id: "approve_plan" }} reject={{ id: "plan", label: "plan", fixed: true }} />);
    expect(screen.getByText("fixed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/ })).toBeNull();
  });
});
