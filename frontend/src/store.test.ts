import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "./store";
import { item } from "./testFixtures";
import type { KraftEvent, WorkItem } from "./types";

const baseItem = (over: Partial<WorkItem> = {}): WorkItem =>
  item({
    title: "t", chain_template: "quick-task", current_node_id: "env_setup", bead_id: "B-1",
    chain_definition: {
      template_id: "quick-task",
      nodes: [
        { id: "env_setup", tasks: ["on.env.prepare"], gate_after: null },
        { id: "verify", tasks: ["on.test.run"], gate_after: null },
      ],
    },
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
  // Kraft-5fx.17: vitest 5's restoreAllMocks() only reassigns the spied
  // object's property back to the original function; it no longer clears the
  // mock's resolved-value config too. Zustand's setState copies the *current*
  // hydrateItem reference forward via Object.assign, so a spy on
  // useStore.getState().hydrateItem outlives the property reassignment and
  // keeps returning its old resolved value in later tests. resetAllMocks()
  // clears the config directly, so the shared mock wrapper falls through to
  // its captured original regardless of which object holds the reference.
  vi.resetAllMocks();
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

  it("worker_session_created makes the row appear before anything runs", () => {
    // A builtin hook never emits worker_session_started, so without this the row
    // only existed after a REST hydrate — and CurrentNodePanel, seeing no session
    // for the current node, rendered no gate at all (Kraft-dce).
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_created", payload: { session_id: "s7", node_id: "chain_review", hook_point: "on.chain.review_ready" } }));
    const rows = useStore.getState().sessionsByItem.w1;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: "s7", node_id: "chain_review", hook_point: "on.chain.review_ready", status: "pending" });
  });

  it("a created session survives the exit event that follows it", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_created", payload: { session_id: "s7", node_id: "chain_review", hook_point: "on.chain.review_ready" } }));
    st.applyEvent(ev({ seq: 3, type: "worker_session_exited", payload: { session_id: "s7", status: "done" } }));
    const rows = useStore.getState().sessionsByItem.w1;
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("done");
  });

  it("worker_session_created carries the attempt", () => {
    // The row is numbered per (work item, node, hook point) in the DB; without the
    // field on the event the live board fell back to blankSession's hardcoded 1
    // and showed "attempt 1" for every re-run until the next hydrate (Kraft-kq8m).
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_created", payload: { session_id: "s7", node_id: "mr_checks", hook_point: "on.ci.poll", attempt: 3 } }));
    st.applyEvent(ev({ seq: 3, type: "worker_session_created", payload: { session_id: "s8", node_id: "mr_checks", hook_point: "on.mr.open" } }));
    const rows = useStore.getState().sessionsByItem.w1;
    expect(rows.find((r) => r.id === "s7")!.attempt).toBe(3);
    expect(rows.find((r) => r.id === "s8")!.attempt).toBe(1);
  });

  it("worker_session_created and _started carry thread onto the row", () => {
    // Both events default to blankSession's thread: 1 unless the payload says
    // otherwise -- without `thread` on the wire, a new escalation thread's
    // session was grouped under thread 1 until the next hydrate, and
    // worker_session_started's upsert reset an already-hydrated thread 2 back
    // to 1 (Kraft-atdbw).
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_created", payload: { session_id: "s7", node_id: "escalation", hook_point: "escalation", thread: 2 } }));
    expect(useStore.getState().sessionsByItem.w1[0].thread).toBe(2);
    st.applyEvent(ev({ seq: 3, type: "worker_session_started", payload: { session_id: "s7", node_id: "escalation", hook_point: "escalation", thread: 2 } }));
    expect(useStore.getState().sessionsByItem.w1[0].thread).toBe(2);
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

  it("gate_requested / gate_approved toggle pending_gate", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "gate_requested", payload: { gate: "spec_approval" } }));
    expect(useStore.getState().workItems.w1.pending_gate).toBe("spec_approval");
    // store.request_gate sets needs_human in the DB; the client must mirror it,
    // or the board badge reads "active" while the item waits on a human (Kraft-fnx).
    expect(useStore.getState().workItems.w1.status).toBe("needs_human");
    st.applyEvent(ev({ seq: 3, type: "gate_approved", payload: { gate: "spec_approval" } }));
    expect(useStore.getState().workItems.w1.pending_gate).toBeNull();
    expect(useStore.getState().workItems.w1.status).toBe("active");
  });

  it("gate_rejected keeps the note", () => {
    useStore.getState().applyEvent(ev({ type: "gate_rejected", payload: { gate: "spec_approval", note: "nope" } }));
    expect(useStore.getState().workItems.w1.rejectNote).toBe("nope");
    expect(useStore.getState().workItems.w1.pending_gate).toBeNull();
  });

  it("fix_cycle_started sets the badge", () => {
    useStore.getState().applyEvent(ev({ type: "fix_cycle_started", payload: { node_id: "verify", cycle: 2 } }));
    expect(useStore.getState().workItems.w1.fixCycle).toBe(2);
  });

  it("plan_progress sets the hero task bar's progress", () => {
    useStore
      .getState()
      .applyEvent(ev({ type: "plan_progress", payload: { node_id: "env_setup", task: 3, total: 6, title: "wire the thing" } }));
    expect(useStore.getState().workItems.w1.progress).toEqual({
      current: 3,
      total: 6,
      title: "wire the thing",
      tasks: [],
    });
  });

  it("plan_progress recomputes an existing plan list's states", () => {
    useStore.setState((s) => ({
      workItems: {
        ...s.workItems,
        w1: {
          ...s.workItems.w1,
          progress: {
            current: 1,
            total: 3,
            title: "a",
            tasks: [
              { n: 1, title: "a", state: "current" },
              { n: 2, title: "b", state: "pending" },
              { n: 3, title: "c", state: "pending" },
            ],
          },
        },
      },
    }));
    useStore.getState().applyEvent(ev({ type: "plan_progress", payload: { node_id: "env_setup", task: 2, total: 3, title: "b" } }));
    expect(useStore.getState().workItems.w1.progress?.tasks?.map((t) => t.state)).toEqual([
      "done",
      "current",
      "pending",
    ]);
  });

  it("work_item_completed sets status", () => {
    useStore.getState().applyEvent(ev({ type: "work_item_completed", payload: {} }));
    expect(useStore.getState().workItems.w1.status).toBe("completed");
  });

  it("work_item_needs_human sets status", () => {
    useStore.getState().applyEvent(ev({ type: "work_item_needs_human", payload: { node_id: "verify", reason: "x" } }));
    expect(useStore.getState().workItems.w1.status).toBe("needs_human");
  });
  it("work_item_abandoned sets status", () => {
    // A live board holding a row for an item someone abandoned elsewhere would
    // keep offering actions on a worktree that no longer exists.
    useStore.getState().applyEvent(ev({ type: "work_item_abandoned", payload: {} }));
    expect(useStore.getState().workItems.w1.status).toBe("abandoned");
  });

  it("work_item_needs_human carries the needs_context question off the live stream", () => {
    // Without this the question arrived only on the next hydrate: the status
    // flipped, no answer box rendered, and on a chain with no fix-loop node the
    // control row's Steer is hard-disabled — a stop with no way to answer it.
    useStore.getState().applyEvent(
      ev({
        type: "work_item_needs_human",
        payload: { node_id: "verify", reason: "needs_context: which database?" },
      }),
    );
    expect(useStore.getState().workItems.w1.needs_context_question).toBe("which database?");
  });

  it("a needs_human stop of any other kind carries no question", () => {
    useStore.getState().applyEvent(
      ev({ type: "work_item_needs_human", payload: { node_id: "verify", reason: "capped out" } }),
    );
    expect(useStore.getState().workItems.w1.needs_context_question).toBeNull();
  });

  it.each(["work_item_resumed", "work_item_retried", "node_started"])(
    "%s clears a stale question",
    (type) => {
      // The field is sticky otherwise: a later, unrelated stop re-renders the
      // old question, and its Answer button posts a /resume the API 409s.
      useStore.getState().applyEvent(
        ev({
          type: "work_item_needs_human",
          payload: { node_id: "verify", reason: "needs_context: which database?" },
        }),
      );
      useStore.getState().applyEvent(ev({ type, payload: { node_id: "verify" } }));
      expect(useStore.getState().workItems.w1.needs_context_question).toBeNull();
    },
  );

  it("work_item_rate_limited patches status and retry_at", () => {
    useStore.getState().applyEvent(
      ev({ type: "work_item_rate_limited", payload: { retry_at: "2026-01-01T00:00:00Z", node_id: "n" } }),
    );
    expect(useStore.getState().workItems.w1.status).toBe("rate_limited");
    expect(useStore.getState().workItems.w1.retry_at).toBe("2026-01-01T00:00:00Z");
  });

  it("work_item_waiting patches status and retry_at", () => {
    useStore.getState().applyEvent(
      ev({ type: "work_item_waiting", payload: { retry_at: "2026-01-01T00:00:00Z", node_id: "n" } }),
    );
    expect(useStore.getState().workItems.w1.status).toBe("waiting");
    expect(useStore.getState().workItems.w1.retry_at).toBe("2026-01-01T00:00:00Z");
  });

  it("work_item_archived sets archived_at/archived_by", () => {
    useStore.setState({ workItems: { w1: baseItem({ status: "completed" }) } });
    useStore.getState().applyEvent(ev({ type: "work_item_archived", payload: { by: "you" } }));
    expect(useStore.getState().workItems.w1.archived_by).toBe("you");
    expect(useStore.getState().workItems.w1.archived_at).toBeTruthy();
  });

  it("work_item_restored clears archived_at/archived_by", () => {
    useStore.setState({
      workItems: { w1: baseItem({ status: "completed", archived_at: "t", archived_by: "you" }) },
    });
    useStore.getState().applyEvent(ev({ type: "work_item_restored", payload: {} }));
    expect(useStore.getState().workItems.w1.archived_at).toBeNull();
    expect(useStore.getState().workItems.w1.archived_by).toBeNull();
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

  it("gate_requested re-hydrates so a stale deferred_findings roll-up isn't left showing", async () => {
    const spy = vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue(undefined);
    useStore.getState().applyEvent(ev({ type: "gate_requested", payload: { gate: "human_review_approval" } }));
    await new Promise((r) => setTimeout(r));
    expect(spy).toHaveBeenCalledWith("w1");
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

  it("hydrates the needs_human items it lists, so a running escalation is known off the board (W11 · J)", async () => {
    vi.spyOn(await import("./api"), "listWorkItems").mockResolvedValue({
      items: [baseItem({ id: "wa", status: "needs_human" }), baseItem({ id: "wb" })],
      cursor: 1,
    });
    const hydrate = vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue();
    await useStore.getState().bootstrap();
    expect(hydrate).toHaveBeenCalledWith("wa");
    expect(hydrate).not.toHaveBeenCalledWith("wb");
  });
});
