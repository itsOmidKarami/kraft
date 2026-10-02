import { render as rtlRender, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KraftEvent } from "../../../types";
import { detail, stubFetch, V1 } from "../testkit";
import { GateBody, GateFooter } from "./GatePane";

afterEach(() => vi.unstubAllGlobals());
const gate = V1[1];
const pending = detail({ current_node_id: "plan_approval", pending_gate: "plan_approval", gate_artifact: ".engineering/plans/p.md", display_status: "needs_you" });
const approved = (by: string): KraftEvent => ({ seq: 1, work_item_id: "w1", type: "gate_approved", payload: { gate: "plan_approval", by }, node_id: "plan_approval", created_at: "t" });

let where = "";
const Where = () => {
  const l = useLocation();
  where = l.pathname + l.search;
  return null;
};
// The footer navigates (W8's review page), so it renders inside a router.
const render = (ui: ReactElement) => rtlRender(<MemoryRouter initialEntries={["/work-items/w1"]}>{ui}<Where /></MemoryRouter>);

describe("GateFooter", () => {
  it("approves the gate", async () => {
    const calls = stubFetch();
    const reload = vi.fn();
    render(<GateFooter item={pending} gate={gate} reload={reload} onRead={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/gates/plan_approval/approve", body: {} }]);
  });

  it("rejects only with a note, naming where it goes back to", async () => {
    const calls = stubFetch();
    render(<GateFooter item={pending} gate={gate} reload={() => {}} onRead={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    const card = screen.getByRole("group", { name: "Reject plan_approval" });
    expect(card).toHaveTextContent("goes back to plan");
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why (the next agent reads it)"), "Add the invalidation story.");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/gates/plan_approval/reject", body: { note: "Add the invalidation story." } }]));
  });

  it("rejects on ⌘↵ from the note, and not before there is one", async () => {
    const calls = stubFetch();
    render(<GateFooter item={pending} gate={gate} reload={() => {}} onRead={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    const note = screen.getByLabelText("Why (the next agent reads it)");
    await userEvent.click(note);
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    await userEvent.type(note, "Add the invalidation story.");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/gates/plan_approval/reject", body: { note: "Add the invalidation story." } }]));
  });

  it("opens the gate's document from Read", async () => {
    const onRead = vi.fn();
    render(<GateFooter item={pending} gate={gate} reload={() => {}} onRead={onRead} />);
    await userEvent.click(screen.getByRole("button", { name: /Read p\.md/ }));
    expect(onRead).toHaveBeenCalled();
  });

  it("reviews changes on the review page: the gate's document first while it is pending (W8)", async () => {
    const { unmount } = render(<GateFooter item={pending} gate={gate} reload={() => {}} onRead={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Review changes" }));
    expect(where).toBe("/work-items/w1/review?gate=plan_approval&doc=1");
    unmount();
    render(<GateFooter item={detail({ gate_artifact: null })} gate={gate} reload={() => {}} onRead={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Review changes" }));
    expect(where).toBe("/work-items/w1/review?gate=plan_approval");
  });
});

describe("GateBody", () => {
  it("says who passed a gate, and what a pending one decides on", async () => {
    stubFetch();
    const { unmount } = render(<GateBody item={detail()} version="1" gate={gate} events={[approved("agent")]} />);
    expect(screen.getByText("passed · auto")).toBeInTheDocument();
    expect(screen.queryByText("decides on")).toBeNull();
    unmount();
    render(<GateBody item={pending} version="1" gate={gate} events={[]} />);
    expect(screen.getByText("waiting for you")).toBeInTheDocument();
    expect(screen.getByText("p.md")).toBeInTheDocument();
  });

  it("lists the open threads, not the resolved ones", async () => {
    stubFetch({ "GET /work-items/w1/threads": [200, [{ id: "a", state: "open", comments: [{ body: "no size bound" }] }, { id: "b", state: "resolved", comments: [{ body: "fixed already" }] }]] });
    render(<GateBody item={pending} version="1" gate={gate} events={[]} />);
    expect(await screen.findByText("no size bound")).toBeInTheDocument();
    expect(screen.queryByText("fixed already")).toBeNull();
  });
});
