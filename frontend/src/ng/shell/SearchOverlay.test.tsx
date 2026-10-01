import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { App } from "../App";
import { useStore } from "../../store";
import { item } from "../../testFixtures";
import type { SearchResult } from "../../types";
import { ROUTES } from "./routes";
import { Shell } from "./Shell";

const Where = () => {
  const l = useLocation();
  return <><span data-testid="where">{l.pathname}</span><span data-testid="search">{l.search}</span></>;
};
const mount = () =>
  render(
    <MemoryRouter initialEntries={["/analytics"]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="*" element={<><input aria-label="page field" /><Where /></>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

const GATED = item({ id: "wi_gate", title: "Gated work", status: "needs_human", pending_gate: "human_review", repo: "/r/alpha" });
const PLAIN = item({ id: "wi_plain", title: "Plain work", status: "active", repo: "/r/beta", bead_id: "kraft-has" });
const DOC: SearchResult = {
  id: "d1", repo: "/r/alpha", source_kind: "artifact", kind: "spec", title: "Caching spec", path: "spec.md",
  snippet: "the [cache] layer", score: 1, links: [{ work_item_id: "wi_gate" } as never],
};
const DOC_FREE: SearchResult = { ...DOC, id: "d2", title: "Free spec", links: [] };

let search: ReturnType<typeof vi.spyOn>;
let beads: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  useStore.setState({ workItems: { wi_gate: GATED, wi_plain: PLAIN }, connection: "open" } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", bind: "x", port: 1, version: "1" } as never);
  search = vi.spyOn(api, "search").mockResolvedValue({ query: "", mode: "hybrid", results: [DOC, DOC_FREE] });
  beads = vi.spyOn(api, "searchBeads").mockResolvedValue({ query: "", beads: [{ id: "kraft-has", title: "Has item", status: "open", issue_type: "task" }, { id: "kraft-new", title: "New bead", status: "open", issue_type: "task" }] });
});

const open = async () => {
  const user = userEvent.setup();
  await user.keyboard("{Meta>}k{/Meta}");
  return { user, input: await screen.findByRole("combobox", { name: "Search" }) };
};
const headings = () => [...document.querySelectorAll(".ng-search-head")].map((h) => h.textContent);
const options = () => screen.getAllByRole("option");

describe("SearchOverlay", () => {
  it("opens on ⌘K and Ctrl+K even from inside a field, and gives focus back on Escape", async () => {
    mount();
    const user = userEvent.setup();
    const field = screen.getByLabelText("page field");
    await user.click(field);
    await user.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("dialog", { name: "Search" })).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("combobox")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(field).toHaveFocus();
    await user.keyboard("{Meta>}k{/Meta}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("opens from the sidebar's Search row", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: /^Search/ }));
    expect(await screen.findByRole("dialog", { name: "Search" })).toBeInTheDocument();
  });

  it("shows Needs you, Recent and Go to chips with no query, and no counts or Filters", async () => {
    mount();
    await open();
    expect(headings()).toEqual(["Needs you", "Recent", "Go to"]);
    expect(screen.getByRole("tab", { name: "All" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(search).not.toHaveBeenCalled();
  });

  it("lists sections in order with a query, omits the empty ones, counts the tabs and offers Filters", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "work");
    await screen.findByText("Caching spec");
    // "work" also finds the Go to row "New work item".
    expect(headings()).toEqual(["Needs you", "Work items", "Documents", "Beads", "Go to"]);
    expect(screen.getByText("documents come from a lagging index, not live state")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Items 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Documents 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Beads 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
    expect(screen.getByText("Filters narrow Documents only")).toBeInTheDocument();
    expect(screen.queryByText("kraft-has", { exact: false })).toBeNull();
  });

  it("moves with ↑/↓, clamped at both ends, with aria-activedescendant following", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "work");
    await screen.findByText("Caching spec");
    const n = options().length;
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}");
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}".repeat(n + 3));
    expect(options()[n - 1]).toHaveAttribute("aria-selected", "true");
    expect(options().filter((o) => o.getAttribute("aria-selected") === "true")).toHaveLength(1);
    expect(input).toHaveAttribute("aria-activedescendant", options()[n - 1].id);
    expect(input).toHaveFocus();
  });

  it("opens each kind of row where the brief says", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "work");
    await screen.findByText("Caching spec");
    const go = async (name: RegExp | string) => {
      await user.click(within(screen.getByRole("listbox")).getByText(name));
    };
    await go("Gated work");
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/wi_gate");
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox"), "work");
    await screen.findByText("Caching spec");
    await go("Caching spec");
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/wi_gate");
    expect(screen.getByTestId("search")).toHaveTextContent("?doc=d1");

    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox"), "work");
    await screen.findByText("Caching spec");
    await go("New bead");
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/new");
    expect(screen.getByTestId("search")).toHaveTextContent("?title=New+bead&bead=kraft-new");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows a document with no work item in a dialog; Escape closes it and focus goes back to where ⌘K was opened", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).endsWith("/documents/d2") ? { id: "d2", title: "Free spec", path: "/r/alpha/spec.md", content: "# Free spec\n\nThe **cache** has no bound." } : {}), { status: 200 })));
    mount();
    const user = userEvent.setup();
    const field = screen.getByLabelText("page field");
    await user.click(field);
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox"), "free");
    await user.click(await screen.findByRole("option", { name: /Free spec/ }));
    const dialog = await screen.findByRole("dialog", { name: "Free spec" });
    expect(await within(dialog).findByText("cache")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("/analytics");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(field).toHaveFocus();
    vi.unstubAllGlobals();
  });

  it("goes to pages from Go to, Archived included, without a page load", async () => {
    mount();
    const { user } = await open();
    expect(screen.getByRole("option", { name: "Archived" })).toBeInTheDocument();
    for (const r of ROUTES) expect(screen.getByRole("option", { name: r.label })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "Archived" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/archived");
    await user.keyboard("{Meta>}k{/Meta}");
    await user.click(await screen.findByRole("option", { name: "New work item" }));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/);
    expect(screen.getByTestId("search")).toHaveTextContent("?new=1");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does nothing on Enter when nothing matches", async () => {
    search.mockResolvedValue({ query: "", mode: "hybrid", results: [] });
    beads.mockResolvedValue({ query: "", beads: [] });
    mount();
    const { user, input } = await open();
    await user.type(input, "zzzz");
    await screen.findByText(/No matches/);
    await user.keyboard("{Enter}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("opens the active row on Enter", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "plain");
    await screen.findByText("Plain work");
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/wi_plain");
  });

  it("keeps Items and Go to and says so inline when the document search fails", async () => {
    search.mockRejectedValue(new Error("boom"));
    mount();
    const { user, input } = await open();
    await user.type(input, "work");
    expect(await screen.findByText("Documents could not be searched")).toBeInTheDocument();
    expect(headings()).toContain("Work items");
    expect(screen.getByText("Plain work")).toBeInTheDocument();
    expect(screen.getByText("Gated work")).toBeInTheDocument();
  });

  it("sends one request for a burst of typing, not one per key", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "cache");
    await screen.findByText("Caching spec");
    expect(search).toHaveBeenCalledTimes(1);
    expect(search).toHaveBeenCalledWith(expect.objectContaining({ q: "cache", mode: "hybrid", limit: 8 }));
  });

  it("cycles the search mode and sends it", async () => {
    mount();
    const { user, input } = await open();
    await user.click(screen.getByRole("button", { name: /Search mode: hybrid/ }));
    await user.type(input, "cache");
    await screen.findByText("Caching spec");
    expect(search).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "fts" }));
  });

  it("closes when a search call comes back 401 and the app shows sign-in", async () => {
    window.history.pushState({}, "", "/analytics");
    search.mockImplementation(async () => {
      window.dispatchEvent(new CustomEvent("kraft:unauthenticated"));
      throw new Error("Unauthorized");
    });
    render(<App />);
    const user = userEvent.setup();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox"), "work");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});
