import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ESCALATION, NEVER, NO_ENTRY, renderPage, resolved, serve, view } from "./testkit";

afterEach(() => vi.unstubAllGlobals());
const list = () => screen.getByRole("navigation", { name: "Harnesses and profiles" });

describe("HarnessesPage", () => {
  it("says so while loading", () => {
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    renderPage();
    expect(screen.getByText("Loading the harnesses…")).toBeInTheDocument();
  });

  it("says so when the draft cannot be read", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the harnesses");
  });

  it("loads an answer whose `resolved` has no chain (the harnesses draft's has none)", async () => {
    serve(view(resolved()));
    renderPage();
    expect(await screen.findByRole("navigation", { name: "Harnesses and profiles" })).toBeInTheDocument();
  });

  it("lists harnesses and profiles with their access words and task counts", async () => {
    serve(view(resolved()));
    renderPage();
    const nav = await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    expect(within(nav).getByRole("heading", { name: "Harnesses · 5" })).toBeInTheDocument();
    expect(within(nav).getByRole("heading", { name: "Profiles · 3" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "claude-sandbox, Override" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "cursor, Never" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "strong, 2 tasks" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "fast, 1 task" })).toBeInTheDocument();
  });

  it("shows published, or the draft's change count and problem count, in the header", async () => {
    serve(view(resolved()));
    renderPage();
    await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    // Outside the shell the header tail has no host, so the counts are read from the list's problem dots below.
    expect(within(list()).queryByRole("button", { name: /has a problem/ })).toBeNull();
  });

  it("marks a list row red when a task or escalation is wrong there", async () => {
    serve(view(resolved(), { problems: [NO_ENTRY, NEVER, ESCALATION], changes: [{ path: "access.claude", kind: "change", summary: "available -> never" }] }));
    renderPage();
    const nav = await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    const marked = within(nav).getAllByRole("button", { name: /has a problem/ }).map((b) => b.getAttribute("aria-label"));
    expect(marked).toEqual(["claude, Available, has a problem", "codex, Available, has a problem", "strong, 2 tasks, has a problem", "fast, 1 task, has a problem"]);
  });

  it("selects from the query, and an unknown id says so with a way back", async () => {
    serve(view(resolved()));
    renderPage("?harness=nosuch");
    expect(await screen.findByText(/No harness called nosuch/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Back to every harness" }));
    expect(await screen.findByLabelText("claude")).toBeInTheDocument();
  });

  it("filters both groups by id", async () => {
    serve(view(resolved()));
    renderPage();
    await userEvent.type(await screen.findByRole("searchbox", { name: "Search harnesses" }), "stro");
    const nav = list();
    expect(within(nav).queryByRole("button", { name: /^claude/ })).toBeNull();
    expect(within(nav).getByRole("button", { name: /^strong/ })).toBeInTheDocument();
  });

  it("moves through the list with the arrow keys, one tab stop", async () => {
    serve(view(resolved()));
    renderPage();
    const nav = await screen.findByRole("navigation", { name: "Harnesses and profiles" });
    const rows = within(nav).getAllByRole("button").filter((b) => b.hasAttribute("data-row"));
    expect(rows.filter((b) => b.tabIndex === 0)).toHaveLength(1);
    rows[0].focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(rows[1]).toHaveFocus();
    expect(rows[1].tabIndex).toBe(0);
    expect(rows[0].tabIndex).toBe(-1);
  });

  it("a click selects, and the pane opens on that harness", async () => {
    serve(view(resolved()));
    renderPage();
    await userEvent.click(within(await screen.findByRole("navigation", { name: "Harnesses and profiles" })).getByRole("button", { name: "codex, Available" }));
    await waitFor(() => expect(screen.getByRole("complementary", { name: "codex pane" })).toBeInTheDocument());
  });
});

describe("HarnessesPage: the pane's first state", () => {
  it("loads with the area's pane on its rail; a link to one harness opens its pane", async () => {
    serve(view(resolved()));
    const { unmount } = renderPage();
    expect(await screen.findByRole("complementary", { name: "harnesses pane, collapsed" })).toBeInTheDocument();
    unmount();
    renderPage("?harness=claude");
    expect(await screen.findByRole("complementary", { name: "claude pane" })).toBeInTheDocument();
  });
});
