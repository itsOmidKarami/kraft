import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { ChainNode, WorkItem, WorkerSession } from "../../types";
import { Shell } from "../shell/Shell";
import type { ItemDetail } from "./useItem";

/** Test helpers for ng/item: a V1 chain, an item on it, a fetch that records
 *  every call and answers by route, and rendering inside the Shell (the
 *  header's actions portal into it). Not a test file itself. */
export const V1: ChainNode[] = [
  { id: "plan", kind: "exec", gate_after: null, tasks: ["plan.write.plan"], steps: [["plan.write.plan"]] },
  { id: "plan_approval", kind: "gate", gate_after: null, tasks: [], steps: [], reject_to: "plan" },
  { id: "verification", kind: "exec", gate_after: null, tasks: ["verification.checks.lint", "verification.review.code_review"], steps: [["verification.checks.lint"], ["verification.review.code_review"]] },
  { id: "merge_request", kind: "exec", gate_after: null, tasks: ["merge_request.open.open_draft"], steps: [["merge_request.open.open_draft"]] },
];

export const detail = (over: Partial<ItemDetail> = {}): ItemDetail =>
  ({
    id: "w1", title: "Design the cache", description: "Cache embeddings by content hash.", repo: "/code/kraft-plugins", status: "active",
    chain_template: "default", chain_definition: { template_id: "default", nodes: V1 }, current_node_id: "verification",
    bead_id: "kraft-cb59", created_at: "2026-09-13T08:00:00Z", updated_at: "2026-09-13T09:00:00Z",
    display_status: "running", stop: null, worker_sessions: [] as WorkerSession[],
    ...over,
  }) as ItemDetail;

export type Call = { method: string; path: string; body: unknown };

/** Stub fetch: `answers` maps "METHOD /path" (no /api) to [status, body]; anything else answers 200 {}. */
export function stubFetch(answers: Record<string, [number, unknown]> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^\/api/, "").split("?")[0];
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const [status, body] = answers[`${method} ${path}`] ?? [200, {}];
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}

export const inShell = (ui: ReactElement, path = "/work-items/w1") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="*" element={ui} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

export type { WorkItem };
