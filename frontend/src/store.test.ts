import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "./store";
import type { KraftEvent, WorkItem } from "./types";

const baseItem = (over: Partial<WorkItem> = {}): WorkItem => ({
  id: "w1",
  title: "t",
  repo: "/r",
  status: "active",
  chain_template: "quick-task",
  chain_definition: {
    template_id: "quick-task",
    nodes: [
      { id: "env_setup", tasks: ["on.env.prepare"], gate_after: null },
      { id: "verify", tasks: ["on.test.run"], gate_after: null },
    ],
  },
  current_node_id: "env_setup",
  bead_id: "B-1",
  created_at: "t",
  updated_at: "t",
  ...over,
});

const ev = (over: Partial<KraftEvent>): KraftEvent => ({
  seq: 1,
  work_item_id: "w1",
  type: "node_started",
  payload: {},
  created_at: "t",
  ...over,
});

beforeEach(() => {
  useStore.setState({
    workItems: { w1: baseItem() },
    sessionsByItem: {},
    eventsByItem: {},
    lastSeq: 0,
    connection: "connecting",
  });
  vi.restoreAllMocks();
});

describe("applyEvent", () => {
  it("node_started sets current_node_id and bumps lastSeq", () => {
    useStore.getState().applyEvent(ev({ seq: 5, type: "node_started", payload: { node_id: "verify" } }));
    const s = useStore.getState();
    expect(s.workItems.w1.current_node_id).toBe("verify");
    expect(s.lastSeq).toBe(5);
    expect(s.eventsByItem.w1).toHaveLength(1);
  });

  it("node_completed records the node as completed", () => {
    useStore.getState().applyEvent(ev({ type: "node_completed", payload: { node_id: "env_setup" } }));
    expect(useStore.getState().workItems.w1.completedNodes).toContain("env_setup");
  });

  it("worker_session_started then _exited upsert one session row", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_started", payload: { session_id: "s1", node_id: "env_setup", hook_point: "on.env.prepare" } }));
    st.applyEvent(ev({ seq: 3, type: "worker_session_exited", payload: { session_id: "s1", status: "done" } }));
    const rows = useStore.getState().sessionsByItem.w1;
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("done");
  });

  it("session_unknown sets the session row status to unknown", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_started", payload: { session_id: "s1", node_id: "env_setup", hook_point: "on.env.prepare" } }));
    st.applyEvent(ev({ seq: 3, type: "session_unknown", payload: { session_id: "s1" } }));
    const rows = useStore.getState().sessionsByItem.w1;
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("unknown");
  });

  it("session_reattached leaves store state unchanged but records the event", () => {
    const before = useStore.getState().workItems.w1;
    useStore.getState().applyEvent(ev({ seq: 4, type: "session_reattached", payload: { session_id: "s1", pid: 123 } }));
    const after = useStore.getState();
    expect(after.workItems.w1).toBe(before);
    expect(after.sessionsByItem.w1 ?? []).toEqual([]);
    expect(after.eventsByItem.w1).toHaveLength(1);
  });

  it("gate_requested / gate_approved toggle pendingGate", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "gate_requested", payload: { gate: "spec_approval" } }));
    expect(useStore.getState().workItems.w1.pendingGate).toBe("spec_approval");
    // store.request_gate sets needs_human in the DB; the client must mirror it,
    // or the board badge reads "active" while the item waits on a human (Kraft-fnx).
    expect(useStore.getState().workItems.w1.status).toBe("needs_human");
    st.applyEvent(ev({ seq: 3, type: "gate_approved", payload: { gate: "spec_approval" } }));
    expect(useStore.getState().workItems.w1.pendingGate).toBeNull();
    expect(useStore.getState().workItems.w1.status).toBe("active");
  });

  it("gate_rejected keeps the note", () => {
    useStore.getState().applyEvent(ev({ type: "gate_rejected", payload: { gate: "spec_approval", note: "nope" } }));
    expect(useStore.getState().workItems.w1.rejectNote).toBe("nope");
    expect(useStore.getState().workItems.w1.pendingGate).toBeNull();
  });

  it("fix_cycle_started sets the badge", () => {
    useStore.getState().applyEvent(ev({ type: "fix_cycle_started", payload: { node_id: "verify", cycle: 2 } }));
    expect(useStore.getState().workItems.w1.fixCycle).toBe(2);
  });

  it("work_item_completed sets status", () => {
    useStore.getState().applyEvent(ev({ type: "work_item_completed", payload: {} }));
    expect(useStore.getState().workItems.w1.status).toBe("completed");
  });

  it("work_item_needs_human sets status", () => {
    useStore.getState().applyEvent(ev({ type: "work_item_needs_human", payload: { node_id: "verify", reason: "x" } }));
    expect(useStore.getState().workItems.w1.status).toBe("needs_human");
  });

  it("unknown type is stored in the timeline, no throw", () => {
    expect(() =>
      useStore.getState().applyEvent(ev({ type: "some_future_event", payload: {} })),
    ).not.toThrow();
    expect(useStore.getState().eventsByItem.w1).toHaveLength(1);
  });

  it("work_item_created for an unknown id triggers hydrateItem", async () => {
    const spy = vi
      .spyOn(useStore.getState(), "hydrateItem")
      .mockResolvedValue(undefined);
    useStore.getState().applyEvent(ev({ work_item_id: "w2", type: "work_item_created", payload: {} }));
    await new Promise((r) => setTimeout(r));
    expect(spy).toHaveBeenCalledWith("w2");
  });

  it("chain_loaded for an unknown id triggers hydrateItem", async () => {
    const spy = vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue(undefined);
    useStore.getState().applyEvent(ev({ work_item_id: "w2", type: "chain_loaded", payload: {} }));
    await new Promise((r) => setTimeout(r));
    expect(spy).toHaveBeenCalledWith("w2");
  });

  it("lastSeq never goes backward", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 10 }));
    st.applyEvent(ev({ seq: 4 }));
    expect(useStore.getState().lastSeq).toBe(10);
  });
});

describe("hydrateItem", () => {
  it("derives completedNodes and fixCycle from the events GET", async () => {
    const api = await import("./api");
    vi.spyOn(api, "getWorkItem").mockResolvedValue({
      ...baseItem(),
      worker_sessions: [],
    } as never);
    vi.spyOn(api, "getEvents").mockResolvedValue([
      ev({ seq: 1, type: "node_completed", payload: { node_id: "env_setup" } }),
      ev({ seq: 2, type: "node_completed", payload: { node_id: "verify" } }),
      ev({ seq: 3, type: "fix_cycle_started", payload: { cycle: 2 } }),
    ]);
    await useStore.getState().hydrateItem("w1");
    const w = useStore.getState().workItems.w1;
    expect(w.completedNodes).toEqual(
      expect.arrayContaining(["env_setup", "verify"]),
    );
    expect(w.fixCycle).toBe(2);
  });
});

describe("bootstrap", () => {
  it("fills workItems and sets lastSeq to the cursor", async () => {
    vi.spyOn(await import("./api"), "listWorkItems").mockResolvedValue({
      items: [baseItem({ id: "wa" })],
      cursor: 42,
    });
    await useStore.getState().bootstrap();
    const s = useStore.getState();
    expect(s.workItems.wa).toBeDefined();
    expect(s.lastSeq).toBe(42);
  });
});
