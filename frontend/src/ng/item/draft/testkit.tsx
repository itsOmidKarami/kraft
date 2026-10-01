import { render } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";
import { detail, stubFetch, V1, type Call } from "../testkit";
import type { ItemDetail } from "../useItem";
import { ItemDraftProvider, useDraft } from "./context";
import type { DraftView, MarkedOp, Problem } from "./types";

/** Test helpers for the item draft (not a test file): a draft answer on the
 *  testkit's chain, a provider around a page, and the toasts it raised. */
export const answer = (ops: MarkedOp[], problems: Problem[] = [], nodes = V1, cap: number | null = null): [number, DraftView] => [200, { ops, problems, checks: { budget: { spent_usd: 1.5, cap_usd: cap } }, nodes, base_seq: ops.length ? 1 : null, updated_at: null }];
export const ov = (path: string, task_config?: Record<string, unknown>, policy?: Record<string, unknown>, passed = false): MarkedOp => ({ op: "override", path, ...(task_config ? { task_config } : {}), ...(policy ? { policy } : {}), passed });
export const add = (id: string, after: string, passed = false): MarkedOp => ({ op: "add_node", after, node: { id, extends: "security" }, passed });

/** Opens Review as soon as the draft has loaded with something in it. */
function Opener() {
  const d = useDraft();
  const ready = !!d?.ops.length;
  useEffect(() => { if (ready) d?.setReviewing(true); }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}
function Where() {
  const l = useLocation();
  return <output data-testid="at">{l.pathname + l.search}</output>;
}

export function mountDraft(ui: ReactNode, answers: Record<string, [number, unknown]>, o: { item?: Partial<ItemDetail>; review?: boolean; reload?: () => void; path?: string } = {}) {
  const calls: Call[] = stubFetch(answers);
  const item = detail(o.item);
  const toasts: string[] = [];
  window.addEventListener("kraft:toast", (e) => void toasts.push((e as CustomEvent<{ message: string }>).detail.message));
  const r = render(
    <MemoryRouter initialEntries={[o.path ?? "/work-items/w1"]}>
      <Routes>
        <Route path="*" element={<ItemDraftProvider item={item} reload={o.reload ?? (() => {})}>{o.review && <Opener />}{ui}<Where /></ItemDraftProvider>} />
      </Routes>
    </MemoryRouter>,
  );
  return { calls, toasts, ...r };
}
export const sent = (calls: Call[], method: string) => calls.filter((c) => c.method === method);
export { V1, vi };
