import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LeaveGuard } from "./LeaveDialog";
import { ReviewDialog } from "./ReviewDialog";
import { answer, mountDraft, ov, sent } from "./testkit";

afterEach(() => vi.unstubAllGlobals());
const DRAFT = "GET /work-items/w1/draft";
const ops = [ov("merge_request", undefined, { time_cap_minutes: 9 })];
const stay = (e: React.MouseEvent) => e.preventDefault();
const Links = () => (
  <>
    <a href="/ng/" onClick={stay}>Board</a>
    <a href="/ng/work-items/w1/nodes/plan" onClick={stay}>inside</a>
    <a href="/ng/work-items/w2" onClick={stay}>another item</a>
    <a href="https://example.com/x" onClick={stay}>outside</a>
  </>
);
const show = (answers: Record<string, [number, unknown]>) => mountDraft(<><Links /><LeaveGuard /><ReviewDialog /></>, answers);
const ready = async (calls: { path: string }[]) => { await waitFor(() => expect(calls.some((c) => c.path === "/work-items/w1/draft")).toBe(true)); await new Promise((r) => setTimeout(r, 20)); };
const unload = () => { const e = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; };

describe("LeaveGuard", () => {
  it("opens the dialog on a link out of the item, and does not navigate", async () => {
    const { calls } = show({ [DRAFT]: answer(ops) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    expect(await screen.findByRole("dialog", { name: "You have unapplied changes to this item" })).toBeInTheDocument();
    expect(screen.getByText(/1 change is saved as a draft/)).toBeInTheDocument();
    expect(screen.getByTestId("at")).toHaveTextContent("/work-items/w1");
  });

  it("guards a link to another item too", async () => {
    const { calls } = show({ [DRAFT]: answer(ops) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "another item" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("Stay closes it and goes nowhere", async () => {
    const { calls } = show({ [DRAFT]: answer(ops) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    await userEvent.click(await screen.findByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("at")).toHaveTextContent("/work-items/w1");
  });

  it("Apply & continue applies, then goes", async () => {
    const { calls } = show({ [DRAFT]: answer(ops), "POST /work-items/w1/draft/apply": answer([]) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply & continue" }));
    await waitFor(() => expect(screen.getByTestId("at")).toHaveTextContent(/^\/$/));
    expect(sent(calls, "POST")).toHaveLength(1);
  });

  it("does not go before the apply has answered", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const { calls } = show({ [DRAFT]: answer(ops), "POST /work-items/w1/draft/apply": answer([]) });
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", async (u: string, i?: RequestInit) => { if (i?.method === "POST") await gate; return real(u, i); });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply & continue" }));
    expect(screen.getByTestId("at")).toHaveTextContent("/work-items/w1");
    release();
    await waitFor(() => expect(screen.getByTestId("at")).toHaveTextContent(/^\/$/));
  });

  it("Discard & continue asks, deletes, then goes", async () => {
    const { calls } = show({ [DRAFT]: answer(ops), "DELETE /work-items/w1/draft": [204, undefined] });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    await userEvent.click(await screen.findByRole("button", { name: "Discard & continue" }));
    expect(sent(calls, "DELETE")).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Discard & continue" }));
    await waitFor(() => expect(screen.getByTestId("at")).toHaveTextContent(/^\/$/));
    expect(sent(calls, "DELETE")).toHaveLength(1);
  });

  it("offers Review problems, and stays, when the draft has a problem", async () => {
    const { calls } = show({ [DRAFT]: answer(ops, [{ op: 0, message: "too big" }]) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review problems" }));
    expect(await screen.findByRole("heading", { name: "Apply 1 change to this item?" })).toBeInTheDocument();
    expect(screen.getByTestId("at")).toHaveTextContent("/work-items/w1");
    expect(sent(calls, "POST")).toEqual([]);
  });

  it("lets a link inside the item through", async () => {
    const { calls } = show({ [DRAFT]: answer(ops) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "inside" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lets a modified click and an outside link through", async () => {
    const { calls } = show({ [DRAFT]: answer(ops) });
    await ready(calls);
    fireEvent.click(screen.getByRole("link", { name: "Board" }), { metaKey: true });
    fireEvent.click(screen.getByRole("link", { name: "Board" }), { ctrlKey: true });
    await userEvent.click(screen.getByRole("link", { name: "outside" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not guard anything without a draft", async () => {
    const { calls } = show({ [DRAFT]: answer([]) });
    await ready(calls);
    await userEvent.click(screen.getByRole("link", { name: "Board" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(unload()).toBe(false);
  });

  it("asks the browser before a hard unload only while a draft exists, and stops when the page goes", async () => {
    const { calls, unmount } = show({ [DRAFT]: answer(ops) });
    await ready(calls);
    expect(unload()).toBe(true);
    unmount();
    expect(unload()).toBe(false);
  });
});
