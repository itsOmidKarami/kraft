import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { SearchOverlay } from "./SearchOverlay";

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
  ({
    id: over.id ?? "w1",
    title: over.title ?? "Item",
    repo: over.repo ?? "/r",
    status: over.status ?? "active",
    chain_template: "quick-task",
    chain_definition: { template_id: "quick-task", nodes: [] },
    current_node_id: null,
    bead_id: null,
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

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
