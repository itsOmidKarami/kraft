import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../testkit";
import { CompleteDialog, EscalateDialog } from "./Dialogs";

afterEach(() => vi.unstubAllGlobals());

describe("item dialogs", () => {
  it("sends an escalation on ⌘↵ from its message, a plain ↵ staying a newline", async () => {
    const calls = stubFetch();
    const onDone = vi.fn();
    render(<EscalateDialog id="w1" onClose={() => {}} onDone={onDone} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "Look at{Enter}the lint step");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(calls.find((c) => c.path === "/work-items/w1/escalate")).toMatchObject({ method: "POST", body: { message: "Look at\nthe lint step", new_thread: false } });
    expect(onDone).toHaveBeenCalled();
  });

  it("marks complete on Ctrl+↵ from its reason, and not while the reason is blank", async () => {
    const calls = stubFetch();
    render(<CompleteDialog id="w1" onClose={() => {}} onDone={() => {}} />);
    const reason = screen.getByRole("textbox", { name: /Reason/ });
    await userEvent.click(reason);
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.type(reason, "Landed in #12");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(calls.find((c) => c.path === "/work-items/w1/complete")).toMatchObject({ method: "POST", body: { reason: "Landed in #12" } });
  });
});
