import { render } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { stubFetch, type Call } from "../../item/testkit";
import type { Area, DraftView, Problem, Result } from "../../templates/draft/types";
import { Toaster } from "../nav/Toaster";

/** Test helpers for the phone areas: a draft view as W9's routes answer it, and a screen mounted at a route with the toaster. Not a test file itself. */
export const result = (over: Partial<Result> = {}): Result => ({
  model: {}, resolved: null, problems: [], sources: {}, changes: [], impact: {}, warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 }, ...over,
});
export const view = (area: Area, key: string, over: Partial<DraftView> = {}, r: Partial<Result> = {}): DraftView => ({
  area, key, draft: false, files: { [`${key}.yaml`]: `${key}: published\n` }, base: {}, updated_at: null, result: result(r), ...over,
});
export const problem = (path: string, message: string, over: Partial<Problem> = {}): Problem => ({ path, field: null, message, file: "policy.yaml", line: null, col: null, ...over });

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
export const where = () => (document.querySelector('output[aria-label="where"]') as HTMLElement).textContent;

export function mountAt(ui: ReactElement, path: string, route: string, answers: Record<string, [number, unknown]> = {}): { calls: Call[] } {
  const calls = stubFetch({ "GET /drafts": [200, []], "GET /apply": [200, { restart: [], reload: [], managed: false }], ...answers });
  render(
    createElement(
      MemoryRouter,
      { initialEntries: [path] },
      createElement(Routes, null, createElement(Route, { path: route, element: createElement("div", null, ui, createElement(Where)) }), createElement(Route, { path: "*", element: createElement(Where) })),
      createElement(Toaster),
    ),
  );
  return { calls };
}
export const posts = (calls: Call[]) => calls.filter((c) => c.method !== "GET");
