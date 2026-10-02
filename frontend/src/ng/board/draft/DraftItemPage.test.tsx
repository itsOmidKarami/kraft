import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { ChainNode, TemplateSummary } from "../../../types";
import { Shell } from "../../shell/Shell";
import { DRAFT_KEY, DraftItemPage } from "./DraftItemPage";

const N = (id: string, more: Partial<ChainNode> = {}) => ({ id, kind: "exec", tasks: [], gate_after: null, ...more }) as ChainNode;
const NODES = [N("spec", { covered_by: "spec" }), N("spec_approval", { kind: "gate", covered_by: "spec" }), N("implementation", { fix_loop: "implementation.fix_loop" }), N("final_review", { kind: "gate" })];
const CHAINS: TemplateSummary[] = [{ id: "default", nodes: NODES, gates: 2 }];

type Call = { url: string; method: string; body: any };
function stub(created = "new1") {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(url), method, body });
    if (String(url).includes("dry_run=1")) {
      const covered = (body.attachments ?? []).some((a: { kind: string }) => a.kind === "spec");
      const skipped = [...(covered ? [{ node: "spec", why: "covered_by", kind: "spec" }, { node: "spec_approval", why: "covered_by", kind: "spec" }] : []), ...body.skip_nodes.map((n: string) => ({ node: n, why: "skip" }))];
      return new Response(JSON.stringify({ nodes: NODES.filter((n) => !skipped.some((s) => s.node === n.id)), skipped, gates: [], caps: { budget_usd: 5, budget_source: "policy", nodes: { implementation: { attempts: 3, wall_clock_s: 1800 } } } }), { status: 200 });
    }
    if (method === "POST") return new Response(JSON.stringify({ id: created }), { status: 201 });
    return new Response("{}", { status: 200 });
  }));
  return calls;
}
const creates = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.url.endsWith("/work-items"));

const Where = () => {
  const l = useLocation();
  return <span data-testid="where">{l.pathname + l.search}</span>;
};
const mount = (state?: unknown, path = "/work-items/new") =>
  render(
    <MemoryRouter initialEntries={[{ pathname: path.split("?")[0], search: path.includes("?") ? `?${path.split("?")[1]}` : "", state }]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/work-items/new" element={<DraftItemPage />} />
          <Route path="*" element={<Where />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
const settle = async () => { for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(350); }); };

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  sessionStorage.clear();
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/code/kraft-plugins", default_chain_template: "default", enabled: true }] as never });
  vi.spyOn(api, "getTemplates").mockResolvedValue(CHAINS);
  vi.spyOn(api, "getTemplate").mockResolvedValue({ id: "default", file: "default.yaml", text: "nodes:\n  - id: spec\n", chain: {} });
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("DraftItemPage", () => {
  it("carries the composer's draft, says DRAFT · NOT CREATED, and creates it, landing on the board with it selected", async () => {
    const calls = stub();
    mount({ draft: { title: "Cache it", brief: "By hash.", repo: "/code/kraft-plugins", chain: "default", spec: "docs/spec.md", plan: "" } });
    await settle();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Cache it");
    expect(screen.getByText("DRAFT · NOT CREATED")).toBeInTheDocument();
    expect(screen.getByText("skips spec, spec_approval")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create paused" }));
    await settle();
    expect(creates(calls).at(-1)?.body).toEqual({
      title: "Cache it", description: "By hash.", repo: "/code/kraft-plugins", chain_template: "default",
      attachments: [{ kind: "spec", path: "docs/spec.md" }], skip_nodes: [], auto_gate: true, autostart: false,
    });
    expect(screen.getByTestId("where")).toHaveTextContent("/?sel=new1");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it("creates and starts on ⌘↵ from the brief, as the composer does, and not before it has a title", async () => {
    const calls = stub();
    mount({ draft: { title: "", brief: "By hash.", repo: "/code/kraft-plugins", chain: "default", spec: "", plan: "" } });
    await settle();
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Brief" }), { key: "Enter", metaKey: true });
    await settle();
    expect(creates(calls)).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Cache it" } });
    await settle();
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Brief" }), { key: "Enter", metaKey: true });
    await settle();
    expect(creates(calls).at(-1)?.body).toMatchObject({ title: "Cache it", description: "By hash.", autostart: true });
  });

  it("opens its chain readable, as the Chains editor does: no smaller than 80% (W10's editor fit)", async () => {
    stub();
    mount({ draft: { title: "Cache it", brief: "", repo: "/code/kraft-plugins", chain: "default", spec: "", plan: "" } });
    await settle();
    // jsdom has no layout, so a plain fit would floor at 30%.
    expect((document.querySelector(".draft-canvas .canvas-world") as HTMLElement).style.transform).toContain("scale(0.8)");
  });

  it("asks for a title before it can create", async () => {
    stub();
    mount();
    await settle();
    expect(screen.getByText("ADD A TITLE TO CREATE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create paused" })).toBeDisabled();
  });

  it("will not skip or run a node an attachment covers; any other node can be skipped from its pane", async () => {
    const calls = stub();
    mount({ draft: { title: "t", repo: "/code/kraft-plugins", chain: "default", spec: "docs/spec.md" } });
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^spec,/ }));
    expect(screen.getByText("skipped · covered by the attached spec")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip on this item" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Config" }));
    expect(screen.getByRole("switch", { name: "Skip on this item" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /^final_review,/ }));
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip on this item" }));
    await settle();
    expect(calls.filter((c) => c.url.includes("dry_run=1")).at(-1)?.body.skip_nodes).toEqual(["final_review"]);
  });

  it("asks before leaving a draft with text by the Board crumb or Esc, and leaves at once without", async () => {
    stub();
    mount({ draft: { title: "half an idea", repo: "/code/kraft-plugins", chain: "default" } });
    await settle();
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    fireEvent.click(within(nav).getByRole("link", { name: "Board" }));
    expect(screen.getByRole("alertdialog", { name: "Discard this draft?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("half an idea");
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/);
  });

  it("keeps the draft in sessionStorage, so a reload keeps it", async () => {
    stub();
    const { unmount } = mount({ draft: { title: "kept", repo: "/code/kraft-plugins", chain: "default" } });
    await settle();
    fireEvent.change(screen.getByRole("textbox", { name: "Brief" }), { target: { value: "and this" } });
    unmount();
    mount();
    await settle();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("kept");
    expect(screen.getByRole("textbox", { name: "Brief" })).toHaveValue("and this");
  });

  it("starts from a bead in the URL: its title prefilled, and Create names it as implemented", async () => {
    const calls = stub();
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ title: "an older draft", repo: "/code/kraft-plugins", chain: "default" }));
    mount(undefined, "/work-items/new?title=Fix%20the%20cache&bead=kraft-ab1");
    await settle();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Fix the cache");
    expect(screen.getByText("kraft-ab1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create paused" }));
    await settle();
    expect(creates(calls).at(-1)?.body).toMatchObject({ title: "Fix the cache", implements_beads: ["kraft-ab1"] });
  });

  it("keeps a bead draft's edits across a reload, and lets the person drop the bead", async () => {
    const calls = stub();
    const { unmount } = mount(undefined, "/work-items/new?title=Fix&bead=kraft-ab1");
    await settle();
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Fix it properly" } });
    unmount();
    mount(undefined, "/work-items/new?title=Fix&bead=kraft-ab1");
    await settle();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Fix it properly");
    fireEvent.click(screen.getByRole("button", { name: "Do not implement kraft-ab1" }));
    fireEvent.click(screen.getByRole("button", { name: "Create paused" }));
    await settle();
    expect("implements_beads" in (creates(calls).at(-1)?.body ?? {})).toBe(false);
  });

  it("shows the chain's published YAML, with the draft's own settings as rows under it", async () => {
    stub();
    mount({ draft: { title: "t", repo: "/code/kraft-plugins", chain: "default", attempts: "4" } });
    await settle();
    fireEvent.click(screen.getByRole("tab", { name: "YAML" }));
    await settle();
    expect(screen.getByLabelText("default chain YAML")).toHaveTextContent("nodes: - id: spec");
    expect(screen.getByText("4 fix attempts")).toBeInTheDocument();
  });
});
