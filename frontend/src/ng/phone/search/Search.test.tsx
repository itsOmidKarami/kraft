import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../../store";
import type { DisplayStatus, WorkItem } from "../../../types";
import { detail, stubFetch, type Call } from "../../item/testkit";
import { Search } from "./Search";
import { DEBOUNCE_MS } from "./useSearch";

const item = (id: string, display_status: DisplayStatus, over: Partial<WorkItem> = {}): WorkItem => detail({ id, title: `Item ${id}`, display_status, bead_id: `kraft-${id}`, updated_at: `2026-09-13T0${id.slice(-1)}:00:00Z`, ...over });
const gate = (id: string) => item(id, "needs_you", { pending_gate: "plan_approval", stop: { kind: "gate", node: "plan_approval", resume_at: null, reason: null } });
const DOC = { id: "d1", repo: "/code/kraft", source_kind: "artifact", kind: "spec", title: "Spec · cache", path: ".engineering/specs/cache.md", snippet: "the [cache] is bounded", score: 1, links: [{ work_item_id: "w1", node_id: null, hook_point: null, worker_session_id: null }] };

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
function mount(answers: Record<string, [number, unknown]> = {}, path = "/search") {
  const calls = stubFetch({
    "GET /search": [200, { query: "", mode: "hybrid", results: [DOC] }],
    "GET /beads/search": [200, { beads: [{ id: "kraft-zz9", title: "Bound the cache", status: "open", issue_type: "task" }] }],
    ...answers,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/search" element={<><Search /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return calls;
}
const where = () => screen.getByLabelText("where").textContent;
const box = () => screen.getByRole("searchbox", { name: "Search" });
const paths = (calls: Call[]) => calls.map((c) => `${c.method} ${c.path}`);

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  useStore.setState({ workItems: Object.fromEntries([item("a1", "running"), gate("g2"), item("d3", "done"), item("x4", "archived")].map((i) => [i.id, i])) } as never);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const type = async (text: string) => {
  await userEvent.type(box(), text, { advanceTimers: vi.advanceTimersByTime });
  await act(async () => void vi.advanceTimersByTime(DEBOUNCE_MS + 20));
};

describe("Search (I)", () => {
  it("with nothing typed lists the gates waiting on you, then the items most recently updated, newest first, no archived one", () => {
    mount();
    expect(within(screen.getByRole("region", { name: "Needs you" })).getByText("Item g2")).toBeInTheDocument();
    const recent = within(screen.getByRole("region", { name: "Recent" })).getAllByRole("button").map((b) => b.textContent);
    expect(recent).toEqual([expect.stringContaining("Item d3"), expect.stringContaining("Item a1")]);
    expect(screen.queryByText("Item x4")).toBeNull();
  });

  it("shows items, documents and beads in the overlay's order and only what the server returned", async () => {
    const calls = mount();
    await type("cache");
    await screen.findByText("Spec · cache");
    const heads = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(heads).toEqual(["Documents", "Beads"]);
    expect(screen.getByText("Bound the cache")).toBeInTheDocument();
    expect(screen.getByText("Documents come from a lagging index, not live state.")).toBeInTheDocument();
    expect(paths(calls)).toContain("GET /search");
    expect(calls.find((c) => c.path === "/search")).toBeTruthy();
  });

  it("a Needs you row opens the gate and never approves", async () => {
    const calls = mount();
    await type("g2");
    const section = screen.getByRole("region", { name: "Needs you" });
    await userEvent.click(within(section).getByRole("button", { name: /Item g2/ }), { advanceTimers: vi.advanceTimersByTime });
    expect(where()).toBe("/work-items/g2/review?gate=plan_approval");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("a document opens over the search with ?doc= and Back-able", async () => {
    mount({ "GET /documents/d1": [200, { title: "Spec · cache", path: ".engineering/specs/cache.md", content: "## Goal\n\nBounded." }] });
    await type("cache");
    await userEvent.click(await screen.findByRole("button", { name: /Spec · cache/ }), { advanceTimers: vi.advanceTimersByTime });
    expect(where()).toContain("?q=cache");
    expect(where()).toContain("doc=d1");
    expect(await screen.findByText("Bounded.")).toBeInTheDocument();
  });

  it("a bead with no item starts the new-item screen with its title; one with an item is not offered", async () => {
    mount({ "GET /beads/search": [200, { beads: [{ id: "kraft-zz9", title: "Bound the cache", status: "open", issue_type: "task" }, { id: "kraft-a1", title: "Has an item", status: "open", issue_type: "task" }] }] });
    await type("cache");
    expect(screen.queryByText("Has an item")).toBeNull();
    await userEvent.click(await screen.findByRole("button", { name: /Bound the cache/ }), { advanceTimers: vi.advanceTimersByTime });
    expect(where()).toBe("/work-items/new?title=Bound%20the%20cache");
  });

  it("Go to rows are the phone's own screens", async () => {
    mount();
    await type("harness");
    await userEvent.click(await screen.findByRole("button", { name: "Harnesses" }), { advanceTimers: vi.advanceTimersByTime });
    expect(where()).toBe("/templates/harnesses");
  });

  it("drops a slow answer for an older query", async () => {
    let release: (v: Response) => void = () => {};
    stubFetch();
    const fast = (q: string) => new Response(JSON.stringify({ query: q, mode: "hybrid", results: [{ ...DOC, id: q, title: `Doc ${q}` }] }), { status: 200 });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.startsWith("/api/search?q=slow")) return new Promise<Response>((r) => (release = r));
      if (u.startsWith("/api/search")) return fast(new URL(u, "http://x").searchParams.get("q")!);
      if (u.includes("/beads/search")) return new Response(JSON.stringify({ beads: [] }), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
    render(<MemoryRouter initialEntries={["/search"]}><Routes><Route path="/search" element={<Search />} /></Routes></MemoryRouter>);
    await type("slow");
    await userEvent.clear(box());
    await type("fast");
    expect(await screen.findByText("Doc fast")).toBeInTheDocument();
    release(fast("slow"));
    await act(async () => void vi.advanceTimersByTime(50));
    expect(screen.queryByText("Doc slow")).toBeNull();
    expect(screen.getByText("Doc fast")).toBeInTheDocument();
  });

  it("says when a source could not be searched, and when nothing matches", async () => {
    mount({ "GET /search": [500, { detail: "index down" }], "GET /beads/search": [200, { beads: [] }] });
    await type("zzz");
    expect(await screen.findByText("Documents could not be searched.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing matches.")).toBeNull();
  });

  it("says nothing matches when every source answered empty", async () => {
    mount({ "GET /search": [200, { query: "q", mode: "hybrid", results: [] }], "GET /beads/search": [200, { beads: [] }] });
    await type("zzzqx");
    expect(await screen.findByText("Nothing matches.")).toBeInTheDocument();
  });

  it("filters go to the server and stay in the URL", async () => {
    const calls = mount();
    await userEvent.click(screen.getByRole("button", { name: "Any source" }), { advanceTimers: vi.advanceTimersByTime });
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("radio", { name: "Documents" }), { advanceTimers: vi.advanceTimersByTime });
    await waitFor(() => expect(where()).toContain("source=artifact"));
    await type("cache");
    await waitFor(() => expect(calls.some((c) => c.path === "/search")).toBe(true));
    expect(where()).toContain("q=cache");
  });
});
