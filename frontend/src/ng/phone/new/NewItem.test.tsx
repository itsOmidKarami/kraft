import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createBody as desktopBody } from "../../board/Composer";
import { stubFetch, type Call } from "../../item/testkit";
import { Toaster } from "../nav/Toaster";
import { createBody } from "./body";
import { NewItem } from "./NewItem";

const REPOS = { repos: [{ path: "/code/kraft-plugins", enabled: true, default_chain_template: "default" }, { path: "/code/docs-site", enabled: true, default_chain_template: "docs_only" }] };
const CHAINS = [{ id: "default", nodes: new Array(15).fill({}), gates: 5 }, { id: "docs_only", nodes: new Array(5).fill({}), gates: 1 }];

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname}</output>;
}
function mount(answers: Record<string, [number, unknown]> = {}) {
  const calls = stubFetch({ "GET /repos": [200, REPOS], "GET /templates/chains": [200, CHAINS], "GET /work-items": [200, { items: [], cursor: 0 }], ...answers });
  render(
    <MemoryRouter initialEntries={["/work-items/new"]}>
      <Routes>
        <Route path="/work-items/new" element={<><NewItem /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
  return calls;
}
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.path === "/work-items");
const where = () => screen.getByLabelText("where").textContent;
const title = () => screen.getByRole("textbox", { name: "Title" });

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("the new work item screen (G)", () => {
  it("disables both buttons until there is a title", async () => {
    mount();
    expect(await screen.findByRole("button", { name: "Create paused" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create and start" })).toBeDisabled();
    await userEvent.type(title(), "Add retry budget");
    expect(screen.getByRole("button", { name: "Create paused" })).toBeEnabled();
  });

  it("Create paused and Create and start send the same body except autostart", async () => {
    const calls = mount({ "POST /work-items": [201, { id: "abcdef0123456789abcdef0123456789", bead_id: "kraft-a1c7" }] });
    await userEvent.type(title(), "Add retry budget");
    await userEvent.type(screen.getByRole("textbox", { name: "Brief" }), "Give the poller a budget.");
    await screen.findByRole("option", { name: /default · 15 nodes · 5 gates \(repo default\)/ });
    await userEvent.click(screen.getByRole("button", { name: "Create paused" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(await screen.findByText("Created kraft-a1c7. Created paused.")).toBeInTheDocument();
    expect(where()).toBe("/");
  });

  it("starts when asked, with the repo's default chain and the typed text", async () => {
    const calls = mount({ "POST /work-items": [201, { id: "w1", bead_id: null }] });
    await userEvent.type(title(), "Add retry budget");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Repo" }), "/code/docs-site");
    await userEvent.click(screen.getByRole("button", { name: "Create and start" }));
    await waitFor(() => expect(posts(calls)[0].body).toEqual({ title: "Add retry budget", description: "", repo: "/code/docs-site", chain_template: "docs_only", attachments: [], autostart: true }));
  });

  it("keeps a refusal under the buttons and stays", async () => {
    mount({ "POST /work-items": [422, { detail: "repo is not connected" }] });
    await userEvent.type(title(), "x");
    await userEvent.click(screen.getByRole("button", { name: "Create paused" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("repo is not connected");
    expect(where()).toBe("/work-items/new");
  });

  it("shows the server's duplicate warning", async () => {
    mount({ "POST /work-items": [201, { id: "w1", bead_id: "kraft-1", duplicate_warning: "an open item has this title" }] });
    await userEvent.type(title(), "x");
    await userEvent.click(screen.getByRole("button", { name: "Create paused" }));
    expect(await screen.findByText("Created kraft-1. Created paused. an open item has this title")).toBeInTheDocument();
  });
});

describe("the body (G.1): the same one the board's composer sends", () => {
  it("equals the desktop's for the same input, attachments included", () => {
    const d = { title: " T ", brief: " b ", repo: "/r", chain: "default", spec: " s.md ", plan: "" };
    for (const start of [false, true]) expect(createBody(d, start)).toEqual(desktopBody(d, start));
  });
});

describe("spec and plan (G.1)", () => {
  it("attaches a found document through two sheets, and removes it again", async () => {
    mount({ "GET /search": [200, { query: "cache", mode: "hybrid", results: [{ id: "d1", repo: "/r", source_kind: "artifact", kind: "spec", title: "Spec · cache", path: ".engineering/specs/cache.md", snippet: "", score: 1, links: [] }] }] });
    await userEvent.click(await screen.findByRole("button", { name: "+ spec" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Attach a spec" }), "cache{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: /Spec · cache/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Remove the spec" })).toHaveTextContent(".engineering/specs/cache.md");
    await userEvent.click(screen.getByRole("button", { name: "Remove the spec" }));
    expect(screen.getByRole("button", { name: "+ spec" })).toBeInTheDocument();
  });

  it("takes a pasted path without searching", async () => {
    const calls = mount();
    await userEvent.click(await screen.findByRole("button", { name: "+ plan" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Attach a plan" }), ".engineering/plans/x.md{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Remove the plan" })).toHaveTextContent(".engineering/plans/x.md");
    expect(calls.some((c) => c.path === "/search")).toBe(false);
  });

  it("says so, in the sheet, when nothing is found", async () => {
    mount({ "GET /search": [200, { query: "zzz", mode: "hybrid", results: [] }] });
    await userEvent.click(await screen.findByRole("button", { name: "+ spec" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Attach a spec" }), "zzz{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("No spec found. Paste a repo-relative path instead.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("discarding (G.2)", () => {
  it("leaves at once when empty and asks first when something is typed", async () => {
    mount();
    await userEvent.type(title(), "keep me");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const sheet = screen.getByRole("dialog", { name: "Discard this draft?" });
    expect(where()).toBe("/work-items/new");
    await userEvent.click(within(sheet).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(where()).toBe("/"));
    expect(sessionStorage.getItem("kraft.ng.phone.new")).toBeNull();
  });

  it("keeps the unsent draft across a reload", async () => {
    const first = mount();
    await userEvent.type(title(), "half a thought");
    await waitFor(() => expect(sessionStorage.getItem("kraft.ng.phone.new")).toContain("half a thought"));
    void first;
    document.body.innerHTML = "";
    mount();
    expect(await screen.findByRole("textbox", { name: "Title" })).toHaveValue("half a thought");
  });
});
