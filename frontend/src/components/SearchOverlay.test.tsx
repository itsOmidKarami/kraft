import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
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

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
});
afterEach(() => vi.restoreAllMocks());

describe("SearchOverlay", () => {
  it("debounces, queries, and renders results with marks", async () => {
    const spy = vi
      .spyOn(api, "search")
      .mockResolvedValue({ query: "reconnect", mode: "fts", results: [hit] });
    render(<SearchOverlay onClose={() => {}} />);
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
    render(<SearchOverlay onClose={() => {}} />);
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
    render(<SearchOverlay onClose={() => {}} />);
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
    render(<SearchOverlay onClose={() => {}} />);
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
    render(<SearchOverlay onClose={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await userEvent.click(await screen.findByText("WS transport design"));
    expect(await screen.findByText(/full text here/)).toBeInTheDocument();
    const docDialog = screen.getByRole("dialog", { name: "document" });
    await userEvent.click(within(docDialog).getByRole("button", { name: /close/i }));
    expect(await screen.findByText("WS transport design")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "document" })).toBeNull();
  });

  it("following a document breadcrumb closes the whole search stack", async () => {
    vi.spyOn(api, "search").mockResolvedValue({ query: "r", mode: "fts", results: [hit] });
    vi.spyOn(api, "getDocument").mockResolvedValue({
      ...hit,
      content: "# body\nfull text here",
      metadata: {},
      source_created_at: null,
      source_updated_at: null,
      indexed_at: "t",
      links: [
        { work_item_id: "w1", node_id: null, hook_point: null, worker_session_id: null },
      ],
    });
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <SearchOverlay onClose={onClose} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await userEvent.click(await screen.findByText("WS transport design"));
    await userEvent.click(await screen.findByRole("link", { name: "w1" }));
    expect(onClose).toHaveBeenCalled();
  });
});
