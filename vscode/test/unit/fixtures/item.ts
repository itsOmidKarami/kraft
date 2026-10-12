import type { StopKind } from "../../../../frontend/src/types";
import type { WorkItem } from "../../../src/core/api";

/** A running list row on `implementation`, as the server sends it; `over` replaces fields. */
export function item(over: Partial<WorkItem> = {}): WorkItem {
  return {
    id: "K-1",
    title: "t",
    repo: "/r",
    status: "active",
    display_status: "running",
    stop: null,
    chain_template: null,
    chain_definition: { template_id: "t", nodes: [] },
    current_node_id: "implementation",
    bead_id: null,
    pending_gate: null,
    archived_at: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...over,
  };
}

/** A `needs_you` row stopped on `implementation` for `kind`, with no reason; `over` replaces fields. */
export function stopped(kind: StopKind, over: Partial<WorkItem> = {}): WorkItem {
  return item({
    status: "needs_human",
    display_status: "needs_you",
    stop: { kind, node: "implementation", reason: null, resume_at: null },
    ...over,
  });
}
