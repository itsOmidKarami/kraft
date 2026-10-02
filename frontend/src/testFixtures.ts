import { vi } from "vitest";
import type { ChainNode, WorkerSession, WorkItem } from "./types";

/** Shared test factories and stubs — not a `.test.*` file itself so
 *  importing it doesn't re-run another file's `describe` blocks. Each
 *  factory takes overrides; a test that depends on a value passes it.
 *
 *  The older shared set: a few tests still import it. New helpers go in the
 *  area's own testkit (`ng/<area>/testkit.tsx` or `fixture.ts`), see
 *  frontend/README.md. */

/** Forces `usePhone()`/every media query to `matches` for the test. */
export function setPhoneWidth(matches = true) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  );
}

export const NODES: ChainNode[] = [
  { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null },
];

export const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    title: "T",
    repo: "/r",
    status: "active",
    chain_template: "default",
    chain_definition: { template_id: "default", nodes: NODES },
    current_node_id: "verify",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

export const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1",
    work_item_id: "w1",
    node_id: "verify",
    hook_point: "on.test.run",
    status: "running",
    attempt: 1,
    thread: 1,
    round: 0,
    created_at: "t",
    started_at: "t",
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
    head_sha: null,
    ...over,
  }) as WorkerSession;
