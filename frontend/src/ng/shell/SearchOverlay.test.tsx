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

const GATED = item({ id: "wi_gate", title: "Gated work", status: "needs_human", display_status: "needs_you", stop: { kind: "gate" } as never, pending_gate: "human_review", repo: "/r/alpha" });
const PLAIN = item({ id: "wi_plain", title: "Plain work", status: "active", display_status: "running", current_node_id: "implementation", repo: "/r/beta", bead_id: "kraft-has" });
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

  it("shows Needs you, Recent and Go to chips with no query, no counts, and Filters at the right of the tab row", async () => {
    mount();
    await open();
    expect(headings()).toEqual(["Needs you", "Recent", "Go to"]);
    expect(screen.getByRole("tab", { name: "All" })).toBeInTheDocument();
    expect(document.querySelector(".ng-search-tabrow")!.lastElementChild).toBe(screen.getByRole("button", { name: "Filters" }));
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
    expect(screen.queryByText("kraft-has", { exact: false })).toBeNull();
  });

  it("shows the filters as chips, label and value, under the tab row, and counts the ones on", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "cache");
    await screen.findByTitle("Caching spec");
    expect(document.querySelector(".ng-search-filters")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Filters" }));
    const chips = [...document.querySelectorAll(".ng-search-fchip")];
    expect(chips.map((c) => c.firstElementChild!.textContent)).toEqual(["source", "kind"]);
    expect(screen.getByText("Filters narrow Documents only")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "source" }), "artifact");
    await user.type(screen.getByRole("textbox", { name: "kind" }), "spec");
    const button = screen.getByRole("button", { name: "Filters, 2 on" });
    expect(button).toHaveTextContent("Filters2");
    await waitFor(() => expect(search).toHaveBeenLastCalledWith(expect.objectContaining({ source_kind: "artifact", kind: "spec" })));
    // Folded, the chips of filters that are on stay in view.
    await user.click(button);
    expect(document.querySelectorAll(".ng-search-fchip")).toHaveLength(2);
  });

  it("says what the board says of an item, and puts a failed one under Needs you with the gates", async () => {
    const FRESH = item({ id: "wi_new", title: "Fresh work", status: "paused", display_status: "paused", current_node_id: null, repo: "/r/beta" });
    const BLOCKED = item({ id: "wi_after", title: "Later work", status: "blocked", display_status: "blocked", current_node_id: null, repo: "/r/beta" });
    const FAILED = item({ id: "wi_fail", title: "Failed work", status: "needs_human", display_status: "failed", current_node_id: "plan", repo: "/r/beta" });
    useStore.setState({ workItems: { wi_gate: GATED, wi_plain: PLAIN, wi_new: FRESH, wi_after: BLOCKED, wi_fail: FAILED } } as never);
    mount();
    const { user, input } = await open();
    await user.type(input, "work");
    await screen.findByText("Caching spec");
    const section = (name: string) => [...document.querySelectorAll(".ng-search-head")].find((h) => h.textContent === name)!.parentElement!;
    // Waiting at a gate, it is an action: review that gate.
    const gate = within(section("Needs you")).getByTitle("Review human review").closest("[role=option]")!;
    expect(gate).toHaveTextContent("Review human reviewGated work · wi_gate · alpha");
    // The item's title is the part of the sub that shortens.
    expect(gate.querySelector(".ng-search-sublead")).toHaveTextContent(/^Gated work$/);
    expect(gate.querySelector(".ng-search-sublead")).toHaveAttribute("data-allow-ellipsis");
    expect(gate.querySelector(".lucide-diamond")).not.toBeNull();
    const says = (row: string) => {
      const o = screen.getByTitle(row).closest("[role=option]")!;
      return [o.querySelector(".ng-search-sub")?.textContent, o.querySelector(".ng-search-where")?.textContent];
    };
    expect(within(section("Needs you")).getByTitle("Failed work")).toBeInTheDocument();
    expect(says("Failed work")).toEqual(["failed at plan", "beta"]);
    expect(says("Fresh work")).toEqual(["not started", "beta"]);
    expect(says("Later work")).toEqual(["waiting on another item", "beta"]);
    expect(says("Plain work")).toEqual(["implementation", "beta"]);
    expect(screen.getByRole("listbox")).not.toHaveTextContent(/needs_human|· paused|· active/);
  });

  it("gives each kind of row its icon, a status tag and where it lives, and ⏎ on the active row only", async () => {
    mount();
    const { user, input } = await open();
    await user.type(input, "r");
    await screen.findByText("Caching spec");
    const row = (name: string) => within(screen.getByRole("listbox")).getByText(name).closest("[role=option]")!;
    const parts = (name: string) => {
      const o = row(name);
      return [o.querySelector("svg")!.getAttribute("class")!.match(/lucide-([a-z-]+)/g)!.at(-1), o.querySelector(".ng-search-tag")?.textContent, o.querySelector(".ng-search-where")?.textContent];
    };
    expect(parts("Review human review")).toEqual(["lucide-diamond", "NEEDS YOU", undefined]);
    expect(parts("Plain work")).toEqual(["lucide-box", "RUNNING", "beta"]);
    expect(parts("Caching spec")).toEqual(["lucide-file-text", "spec", "alpha"]);
    expect(parts("New bead")).toEqual(["lucide-circle-dot", "open", undefined]);
    expect(parts("Appearance")).toEqual(["lucide-palette", undefined, "Settings"]);
    expect(parts("Library")).toEqual(["lucide-library-big", undefined, "Templates"]);
    expect(screen.getAllByText("⏎", { selector: ".ng-search-key" })).toHaveLength(1);
    expect(options()[0].querySelector(".ng-search-key")).not.toBeNull();
    await user.keyboard("{ArrowDown}");
    expect(options()[0].querySelector(".ng-search-key")).toBeNull();
    expect(options()[1].querySelector(".ng-search-key")).not.toBeNull();
  });

  it("marks the query in titles as well as in snippets", async () => {
    mount();
    const { user, input } = await open();
    const marks = (name: string) => [...within(screen.getByRole("listbox")).getByTitle(name).querySelectorAll("mark")].map((m) => m.textContent);
    await user.type(input, "plain");
    expect(marks(await screen.findByTitle("Plain work").then((e) => e.title))).toEqual(["Plain"]);
    await user.clear(input);
    await user.type(input, "caching spec");
    await screen.findByTitle("Caching spec");
    expect(marks("Caching spec")).toEqual(["Caching", "spec"]);
    expect(screen.getByTitle("Caching spec").closest("[role=option]")!.querySelector(".ng-search-snippet mark")).toHaveTextContent("cache");
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
    await go("Review human review");
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/wi_gate/review");
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox"), "work");
    await screen.findByText("Caching spec");
    await go("Caching spec");
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/wi_gate");
    expect(screen.getByTestId("search")).toHaveTextContent("?doc=d1&q=work");

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

  // #502 review: with nothing focused when ⌘K opened, closing the document left focus on <body>.
  it("hands focus to the page when a document opened from search closes and nothing had focus before ⌘K", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).endsWith("/documents/d2") ? { id: "d2", title: "Free spec", path: "/r/alpha/spec.md", content: "# Free spec\n\nThe **cache** has no bound." } : {}), { status: 200 })));
    mount();
    const user = userEvent.setup();
    (document.activeElement as HTMLElement | null)?.blur();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByRole("combobox"), "free");
    await user.click(await screen.findByRole("option", { name: /Free spec/ }));
    expect(await within(await screen.findByRole("dialog", { name: "Free spec" })).findByText("cache")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("main")).toHaveFocus();
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

  it.each([
    ["plain", "Plain work", "/work-items/wi_plain"],
    ["gated", "Review human review", "/work-items/wi_gate/review"],
  ])("opens the active row on Enter: %s", async (query, row, to) => {
    mount();
    const { user, input } = await open();
    await user.type(input, query);
    await screen.findByTitle(row);
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent(new RegExp(`^${to}$`));
  });

  it("keeps Items and Go to and says so inline when the document search fails", async () => {
    search.mockRejectedValue(new Error("boom"));
    mount();
    const { user, input } = await open();
    await user.type(input, "work");
    expect(await screen.findByText("Documents could not be searched")).toBeInTheDocument();
    expect(headings()).toContain("Work items");
    expect(screen.getByTitle("Plain work")).toBeInTheDocument();
    expect(screen.getByTitle("Review human review")).toBeInTheDocument();
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
