import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { DEFAULT_PREFS, useDiffPrefs } from "./prefs";
import { ReviewPage } from "./ReviewPage";

const ITEM = { id: "w1", title: "Cache embeddings", repo: "/r/x", status: "needs_human", display_status: "needs_you", worker_sessions: [], chain_definition: { nodes: [] }, attempts: [], last_review_sha: "h1", head_sha: "h2", pending_gate: "final_review", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" };

let search = "";
const Where = () => {
  search = useLocation().search;
  return null;
};

beforeEach(() => {
  vi.spyOn(api, "getWorkItem").mockResolvedValue(ITEM as never);
  vi.spyOn(api, "getTheme").mockResolvedValue({ diff: DEFAULT_PREFS, code_scheme: { light: "auto", dark: "auto" } } as never);
});
afterEach(() => vi.restoreAllMocks());

describe("ReviewPage", () => {
  it("shows a stale comparison's refusal and offers to compare from the base", async () => {
    vi.spyOn(http, "request").mockImplementation(async (p) =>
      String(p).includes("/compare") ? { status: 404, body: { detail: "no review has been submitted for this gate" } } : { status: 200, body: [] },
    );
    render(
      <MemoryRouter initialEntries={["/work-items/w1/review?to=attempt:2"]}>
        <Routes><Route path="/work-items/:id/review" element={<><ReviewPage /><Where /></>} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("no review has been submitted for this gate")).toBeInTheDocument();
    // A re-review: from defaults to the last review.
    expect(vi.mocked(http.request).mock.calls.map(([p]) => String(p)).find((p) => p.includes("/compare"))).toBe("/work-items/w1/compare?from=last_review&to=attempt%3A2");
    fireEvent.click(screen.getByRole("button", { name: "Compare from base" }));
    expect(search).toBe("?from=base");
  });
});

describe("useDiffPrefs", () => {
  it("saves the whole diff object on each change, and takes it back on a refusal", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    const { result } = renderHook(() => useDiffPrefs());
    await act(async () => {});
    await act(() => result.current.set({ layout: "split" }));
    expect(req).toHaveBeenCalledWith("/theme", expect.objectContaining({ method: "PUT", body: JSON.stringify({ diff: { ...DEFAULT_PREFS, layout: "split" } }) }));
    req.mockResolvedValue({ status: 422, body: { detail: "theme.yaml: diff.layout: bad" } });
    await act(() => result.current.set({ wrap_lines: true }));
    expect(result.current.prefs).toEqual({ ...DEFAULT_PREFS, layout: "split" });
    expect(result.current.error).toBe("theme.yaml: diff.layout: bad");
  });
});
