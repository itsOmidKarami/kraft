import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { SearchOverlay } from "./SearchOverlay";
import { SearchView } from "../views/Search";
import { item } from "../testFixtures";

const hit = {
  id: "d1",
  repo: "/r",
  source_kind: "artifact" as const,
  kind: "specs",
  title: "WS transport design",
  path: ".engineering/specs/ws.md",
  snippet: "the [reconnect] [backoff] schedule",
  score: -1.5,
  links: [],
};

const wi = (over: Partial<WorkItem>): WorkItem =>
  item({ title: "Item", chain_template: "quick-task", chain_definition: { template_id: "quick-task", nodes: [] }, current_node_id: null, bead_id: null, ...over });

const renderOverlay = (props: Partial<Parameters<typeof SearchOverlay>[0]> = {}) =>
  render(
    <MemoryRouter>
      <SearchOverlay onClose={props.onClose ?? (() => {})} embedded={props.embedded} />
    </MemoryRouter>,
  );

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
  vi.spyOn(api, "searchBeads").mockResolvedValue({ query: "", beads: [] });
});
afterEach(() => vi.restoreAllMocks());

describe("SearchOverlay", () => {
  it("debounces, queries, and renders results with marks", async () => {
    const spy = vi
      .spyOn(api, "search")
      .mockResolvedValue({ query: "reconnect", mode: "fts", results: [hit] });
    renderOverlay();
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0]).toMatchObject({ q: "reconnect" });
    expect(await screen.findByText("WS transport design")).toBeInTheDocument();
    expect(screen.getByText("reconnect").tagName).toBe("MARK");
  });

  it("makes no request for an empty query", async () => {
    const spy = vi
      .spyOn(api, "search")
      .mockResolvedValue({ query: "", mode: "fts", results: [] });
    renderOverlay();
    await userEvent.type(screen.getByRole("searchbox"), "ab");
    await userEvent.clear(screen.getByRole("searchbox"));
    await new Promise((r) => setTimeout(r, 300));
    expect(spy).not.toHaveBeenCalledWith(expect.objectContaining({ q: "" }));
    expect(screen.getByText(/type to search/i)).toBeInTheDocument();
  });

  it("adds the source_kind filter from the advanced panel", async () => {
    const spy = vi
      .spyOn(api, "search")
      .mockResolvedValue({ query: "x", mode: "fts", results: [] });
    renderOverlay();
    await userEvent.click(screen.getByRole("button", { name: /advanced/i }));
    await userEvent.selectOptions(screen.getByLabelText("source_kind"), "session_summary");
    await userEvent.type(screen.getByRole("searchbox"), "x");
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ q: "x", source_kind: "session_summary" }),
      ),
    );
  });

  it("shows an inline error when the query is rejected", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new Error("bad search query"));
    renderOverlay();
    await userEvent.type(screen.getByRole("searchbox"), '"x');
    expect(await screen.findByText(/bad search query/)).toBeInTheDocument();
  });

  it("opens a document from a result and returns to the list", async () => {
    vi.spyOn(api, "search").mockResolvedValue({ query: "r", mode: "fts", results: [hit] });
    vi.spyOn(api, "getDocument").mockResolvedValue({
      ...hit,
      content: "# body\nfull text here",
      metadata: {},
      source_created_at: null,
      source_updated_at: null,
      indexed_at: "t",
    });
    renderOverlay();
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await userEvent.click(await screen.findByText("WS transport design"));
    expect(await screen.findByText(/full text here/)).toBeInTheDocument();
    const docDialog = screen.getByRole("dialog", { name: "document" });
    await userEvent.click(within(docDialog).getByRole("button", { name: /close/i }));
    expect(await screen.findByText("WS transport design")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "document" })).toBeNull();
  });

  it("a document result with a linked work item navigates to its Documents tab instead of opening a modal", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      query: "r",
      mode: "fts",
      results: [{ ...hit, links: [{ work_item_id: "w1", node_id: null, hook_point: null, worker_session_id: null }] }],
    });
    const onClose = vi.fn();
    renderOverlay({ onClose });
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await userEvent.click(await screen.findByText("WS transport design"));
    expect(onClose).toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "document" })).toBeNull();
  });

  it("renders as a plain page with no backdrop or esc control when embedded", () => {
    renderOverlay({ embedded: true });
    expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "esc" })).toBeNull();
  });

  it("Actions lists a pending gate first, and Enter approves it", async () => {
    useStore.setState({
      workItems: { w1: wi({ id: "w1", status: "needs_human", pending_gate: "plan_approval", title: "fix flaky test" }) },
    } as never);
    const spy = vi.spyOn(api, "approveGate").mockResolvedValue();
    renderOverlay();
    expect(screen.getByText(/approve plan_approval/i)).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox"), "{Enter}");
    expect(spy).toHaveBeenCalledWith("w1", "plan_approval");
  });

  it("ArrowDown moves aria-activedescendant and Enter acts on that row, not the first (W6.5)", async () => {
    useStore.setState({
      workItems: {
        w1: wi({ id: "w1", status: "needs_human", pending_gate: "plan_approval", title: "first gated" }),
        w2: wi({ id: "w2", status: "needs_human", pending_gate: "spec_approval", title: "second gated" }),
      },
    } as never);
    const spy = vi.spyOn(api, "approveGate").mockResolvedValue();
    renderOverlay();
    const box = screen.getByRole("searchbox");
    expect(box).toHaveAttribute("aria-activedescendant", "search-opt-0");
    await userEvent.type(box, "{ArrowDown}");
    expect(box).toHaveAttribute("aria-activedescendant", "search-opt-1");
    expect(document.getElementById("search-opt-1")).toHaveAttribute("data-active", "true");
    await userEvent.type(box, "{Enter}");
    expect(spy).toHaveBeenCalledWith("w2", "spec_approval");
  });

  it("puts repo · node · N of M · title on a work-item row's second line, no bare task noun", async () => {
    useStore.setState({
      workItems: {
        w1: wi({
          id: "w1",
          title: "fix flaky test",
          current_node_id: "implementation",
          progress: { current: 3, total: 6, title: "open_mr refuses a dirty worktree" },
        }),
      },
    } as never);
    renderOverlay();
    await userEvent.type(screen.getByRole("searchbox"), "fix flaky");
    const row = await screen.findByRole("button", { name: /fix flaky test/ });
    expect(row.querySelector(".search-row-sub")?.textContent).toMatch(
      /implementation.*3 of 6.*open_mr refuses/,
    );
    expect(row.querySelector(".search-row-sub")?.textContent).not.toMatch(/\btask\b/i);
  });

  it("sections render in order: Actions, Work items, Documents, Go to", () => {
    useStore.setState({
      workItems: { w1: wi({ id: "w1", status: "needs_human", pending_gate: "plan_approval", title: "fix flaky test" }) },
    } as never);
    renderOverlay();
    const sections = document.querySelectorAll(".search-section");
    const labels = [...sections].map((s) => s.querySelector(".section-label")?.textContent);
    expect(labels).toEqual(["Actions", "Documents", "Go to"]);
  });
});

describe("SearchView", () => {
  it("mounts SearchOverlay embedded, with no dialog role", () => {
    vi.spyOn(api, "search").mockResolvedValue({ query: "", mode: "hybrid", results: [] });
    render(
      <MemoryRouter>
        <SearchView />
      </MemoryRouter>,
    );
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
