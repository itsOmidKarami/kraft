import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscalationThread } from "../../../types";
import { acceptWrites, stubFetch } from "../testkit";
import { CompleteCard, EscalateCard } from "./Dialogs";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/complete", "POST /work-items/w1/escalate");
const preview = (running: object | null) => ({ running, kept: { branch: "kraft/cb59", worktree: "/wt", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } });
const ANSWERS = { ...WRITES, "GET /work-items/w1/cancel-preview": [200, preview(null)] as [number, unknown] };

afterEach(() => vi.unstubAllGlobals());
const anchor = () => {
  const ref = createRef<HTMLDivElement>() as { current: HTMLDivElement };
  ref.current = document.body.appendChild(document.createElement("div"));
  return ref;
};

describe("item cards", () => {
  it("sends an escalation on ⌘↵ from its message, a plain ↵ staying a newline", async () => {
    const calls = stubFetch(ANSWERS);
    const onDone = vi.fn();
    render(<EscalateCard id="w1" anchor={anchor()} onClose={() => {}} onDone={onDone} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "Look at{Enter}the lint step");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(calls.find((c) => c.path === "/work-items/w1/escalate")).toMatchObject({ method: "POST", body: { message: "Look at\nthe lint step", new_thread: false } });
    expect(onDone).toHaveBeenCalled();
  });

  // WI-15
  it.each([
    ["continues the last thread", false, 'continues thread 2 (turn 4), so it remembers the earlier turns'],
    ["starts the next one when asked", true, "starts thread 3, a fresh session that does not see thread 2"],
  ])("gives the message an example, and says what the thread box does: %s", async (_, fresh, hint) => {
    const threads = [{ thread: 1, turns: 2 }, { thread: 2, turns: 3 }] as EscalationThread[];
    render(<EscalateCard id="w1" anchor={anchor()} threads={threads} onClose={() => {}} onDone={() => {}} />);
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveAttribute("placeholder", expect.stringMatching(/^What should it look at\? \(required\) e\.g\. "/));
    if (fresh) await userEvent.click(screen.getByRole("checkbox", { name: "Start a new thread" }));
    expect(screen.getByText(hint)).toBeInTheDocument();
  });

  it("says nothing about threads when it does not know them", () => {
    render(<EscalateCard id="w1" anchor={anchor()} onClose={() => {}} onDone={() => {}} />);
    expect(screen.queryByText(/thread \d/)).toBeNull();
  });

  // WI-14: the card says what ending does, as Cancel does, then the beads box, then the reason.
  it("says what Mark complete stops and keeps, then asks for the beads and the reason", async () => {
    const started = new Date(Date.now() - 41_000).toISOString();
    stubFetch({ ...WRITES, "GET /work-items/w1/cancel-preview": [200, preview({ node: "verification", task: "verification.review.code_review", attempt: 2, started_at: started })] });
    render(<CompleteCard id="w1" anchor={anchor()} onClose={() => {}} onDone={() => {}} />);
    expect(await screen.findByText("code_review, attempt 2 (41s). That attempt's work is lost.")).toBeInTheDocument();
    const card = screen.getByRole("dialog", { name: "Mark this item complete?" });
    expect([...card.querySelectorAll("dt")].map((d) => d.textContent)).toEqual(["stops now", "keeps", "afterwards"]);
    expect(card).toHaveTextContent("keeps" + "branch kraft/cb59, the worktree, findings and the run log");
    expect(card).toHaveTextContent("Status COMPLETED. Archive it when you are done.");
    const order = [card.querySelector(".item-facts"), screen.getByRole("checkbox", { name: "Also close its beads" }), screen.getByRole("textbox", { name: /Reason/ })];
    order.slice(1).forEach((el, i) => expect(order[i]!.compareDocumentPosition(el!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy());
  });

  it("leaves 'stops now' out when nothing is running", async () => {
    stubFetch(ANSWERS);
    render(<CompleteCard id="w1" anchor={anchor()} onClose={() => {}} onDone={() => {}} />);
    expect(await screen.findByText(/^branch kraft\/cb59/)).toBeInTheDocument();
    expect(screen.queryByText("stops now")).toBeNull();
  });

  it("marks complete on Ctrl+↵ from its reason, and not while the reason is blank", async () => {
    const calls = stubFetch(ANSWERS);
    render(<CompleteCard id="w1" anchor={anchor()} onClose={() => {}} onDone={() => {}} />);
    const reason = screen.getByRole("textbox", { name: /Reason/ });
    await userEvent.click(reason);
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.type(reason, "Landed in #12");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(calls.find((c) => c.path === "/work-items/w1/complete")).toMatchObject({ method: "POST", body: { reason: "Landed in #12" } });
  });

  it.each([
    ["Escalate", EscalateCard, "Escalate this item", "Message"],
    ["Mark complete", CompleteCard, "Mark this item complete?", "Reason"],
  ])("opens %s as a card, not a modal, that a stray press closes only while nothing is typed", async (_, Card, name, field) => {
    stubFetch(ANSWERS);
    const onClose = vi.fn();
    render(<Card id="w1" anchor={anchor()} onClose={onClose} onDone={() => {}} />);
    const card = screen.getByRole("dialog", { name });
    expect(card).toHaveClass("popover");
    expect(card).not.toHaveAttribute("aria-modal");
    await userEvent.type(screen.getByRole("textbox", { name: new RegExp(field) }), "why");
    fireEvent.mouseDown(document.body);
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.clear(screen.getByRole("textbox", { name: new RegExp(field) }));
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // R12b-05: Escape closed the card and dropped what was typed; empty, it still closes.
  it.each([
    ["Escalate", EscalateCard, "Message"],
    ["Mark complete", CompleteCard, "Reason"],
  ])("%s keeps its typed text on Escape, and closes on one while empty", async (_, Card, field) => {
    stubFetch(ANSWERS);
    const onClose = vi.fn();
    render(<Card id="w1" anchor={anchor()} onClose={onClose} onDone={() => {}} />);
    const box = screen.getByRole("textbox", { name: new RegExp(field) });
    await userEvent.type(box, "Half-written");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(box).toHaveValue("Half-written");
    await userEvent.clear(box);
    fireEvent.keyDown(box, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
