import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { acceptWrites, stubFetch } from "../testkit";
import { CompleteCard, EscalateCard } from "./Dialogs";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/complete", "POST /work-items/w1/escalate");

afterEach(() => vi.unstubAllGlobals());
const anchor = () => {
  const ref = createRef<HTMLDivElement>() as { current: HTMLDivElement };
  ref.current = document.body.appendChild(document.createElement("div"));
  return ref;
};

describe("item cards", () => {
  it("sends an escalation on ⌘↵ from its message, a plain ↵ staying a newline", async () => {
    const calls = stubFetch(WRITES);
    const onDone = vi.fn();
    render(<EscalateCard id="w1" anchor={anchor()} onClose={() => {}} onDone={onDone} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "Look at{Enter}the lint step");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(calls.find((c) => c.path === "/work-items/w1/escalate")).toMatchObject({ method: "POST", body: { message: "Look at\nthe lint step", new_thread: false } });
    expect(onDone).toHaveBeenCalled();
  });

  it("marks complete on Ctrl+↵ from its reason, and not while the reason is blank", async () => {
    const calls = stubFetch(WRITES);
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
    stubFetch(WRITES);
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
});
