import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { ChainNode, TemplateSummary } from "../../types";
import { Composer } from "./Composer";

const N = (id: string, kind: "exec" | "gate" = "exec", covered_by: string | null = null) => ({ id, kind, covered_by, gate_after: null, tasks: [] }) as unknown as ChainNode;
const DEFAULT = [N("spec", "exec", "spec"), N("spec_approval", "gate", "spec"), N("implementation"), N("final_review", "gate")];
const CHAINS: TemplateSummary[] = [{ id: "default", nodes: DEFAULT, gates: 2 }, { id: "docs_only", nodes: [N("implementation")], gates: 0 }];
const REPOS = [
  { path: "/code/kraft-plugins", default_chain_template: "default", enabled: true },
  { path: "/code/kraft-docs", default_chain_template: "docs_only", enabled: true },
] as never;

/** Where More options landed, and what it carried. */
const Carried = () => <pre data-testid="carried">{JSON.stringify(useLocation().state)}</pre>;
type Call = { url: string; method: string; body: unknown };
const DRY_OK = { nodes: DEFAULT, skipped: [], gates: ["spec_approval", "final_review"] };
/** Answers the create, the dry run (told apart by its query) and anything else with {}; records each call. */
function stubFetch(dry: [number, unknown] = [200, DRY_OK], create: [number, unknown] = [201, { id: "new1" }]) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const [status, body] = String(url).includes("dry_run=1") ? dry : method === "POST" && String(url).endsWith("/work-items") ? create : [200, {}];
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}
const creates = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.url.endsWith("/work-items"));
const dryRuns = (calls: Call[]) => calls.filter((c) => c.url.includes("dry_run=1"));

const mount = (dry?: [number, unknown], props: { repoFilter?: string } = {}) => {
  const calls = stubFetch(dry);
  const h = { onClose: vi.fn(), onCreated: vi.fn() };
  render(<MemoryRouter initialEntries={["/?new=1"]}><Routes><Route path="/" element={<Composer repoFilter={props.repoFilter ?? ""} {...h} />} /><Route path="/work-items/new" element={<Carried />} /></Routes></MemoryRouter>);
  return { calls, ...h };
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: REPOS });
  vi.spyOn(api, "getTemplates").mockResolvedValue(CHAINS);
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const settle = async () => {
  for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(350); });
};

describe("Composer", () => {
  it("creates and starts on ⌘↵ and creates paused from the button, sending the create body", async () => {
    const { calls, onCreated } = mount();
    await settle();
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Cache the embeddings" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Brief" }), { target: { value: "Key by content hash." } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Title" }), { key: "Enter", metaKey: true });
    await settle();
    expect(creates(calls).at(-1)?.body).toEqual({ title: "Cache the embeddings", description: "Key by content hash.", repo: "/code/kraft-plugins", chain_template: "default", attachments: [], autostart: true });
    expect(onCreated).toHaveBeenCalledWith("new1");
    fireEvent.click(screen.getByRole("button", { name: "Create paused" }));
    await settle();
    expect((creates(calls).at(-1)?.body as { autostart: boolean }).autostart).toBe(false);
  });

  it("is disabled without a title, and opens Create and start from the ▾ by keyboard", async () => {
    mount();
    await settle();
    expect(screen.getByRole("button", { name: "Create paused" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "x" } });
    fireEvent.keyDown(screen.getByRole("button", { name: "More ways to create" }), { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "Create and start" })).toBeInTheDocument();
  });

  it("asks before discarding a draft with text, and closes at once without", async () => {
    const { onClose } = mount();
    await settle();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "half an idea" } });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByText("Discard this draft? Nothing has been created.")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByText(/Discard this draft/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("starts on the board's repo filter and resets the chain to a picked repo's default", async () => {
    mount(undefined, { repoFilter: "/code/kraft-docs" });
    await settle();
    expect(screen.getByRole("button", { name: /^kraft-docs/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^docs_only/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^kraft-docs/ }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /kraft-plugins/ }));
    expect(screen.getByRole("button", { name: /^default/ })).toBeInTheDocument();
  });

  it("previews what will run from one debounced dry run, covered nodes left empty", async () => {
    const dry = { nodes: [DEFAULT[2], DEFAULT[3]], skipped: [{ node: "spec", why: "covered_by" }, { node: "spec_approval", why: "covered_by" }], gates: ["final_review"] };
    const calls = mount([200, dry]).calls;
    await settle();
    expect(dryRuns(calls)).toHaveLength(1);
    expect(screen.getByText("2 of 4 nodes run · 1 gate")).toBeInTheDocument();
    const ticks = [...document.querySelectorAll(".composer-ticks .tick")].map((t) => t.className.includes("is-run"));
    expect(ticks).toEqual([false, false, true, true]);
    // The title does not change what runs: no second dry run.
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "x" } });
    await settle();
    expect(dryRuns(calls)).toHaveLength(1);
  });

  it("shows the dry run's refusal in the server's words and holds Create", async () => {
    mount([422, { detail: "attachment not found: docs/missing.md" }]);
    await settle();
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "x" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("attachment not found: docs/missing.md");
    expect(screen.getByRole("button", { name: "Create paused" })).toBeDisabled();
  });

  it("attaches a spec found by search (the shipped composer's call), or a pasted path", async () => {
    const search = vi.spyOn(api, "search").mockResolvedValue({ query: "", mode: "hybrid", results: [{ id: "d1", path: "docs/specs/cache.md" } as never] });
    const { calls } = mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "+ spec" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search specs/ }), { target: { value: "cache" } });
    await settle();
    expect(search).toHaveBeenLastCalledWith({ q: "cache", repo: "/code/kraft-plugins", kind: "spec", source_kind: "artifact", limit: 5 });
    fireEvent.click(screen.getByRole("button", { name: "docs/specs/cache.md" }));
    expect(screen.getByTitle("docs/specs/cache.md")).toHaveTextContent("spec · docs/specs/cache.md");
    fireEvent.click(screen.getByRole("button", { name: "+ plan" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search plans/ }), { target: { value: "docs/plans/x.md" } });
    // A hit is there, but a pasted path is what the person means.
    await settle();
    fireEvent.keyDown(screen.getByRole("textbox", { name: /Search plans/ }), { key: "Enter" });
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "t" } });
    fireEvent.click(screen.getByRole("button", { name: "Create paused" }));
    await settle();
    expect((creates(calls).at(-1)?.body as { attachments: unknown }).attachments).toEqual([{ kind: "spec", path: "docs/specs/cache.md" }, { kind: "plan", path: "docs/plans/x.md" }]);
  });

  it("hands title, brief, repo, chain and both attachments to the draft page with More options", async () => {
    vi.spyOn(api, "search").mockResolvedValue({ query: "", mode: "hybrid", results: [] });
    const { onClose } = mount();
    await settle();
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Cache it" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Brief" }), { target: { value: "By hash." } });
    for (const [k, p] of [["spec", "docs/specs/a.md"], ["plan", "docs/plans/a.md"]]) {
      fireEvent.click(screen.getByRole("button", { name: `+ ${k}` }));
      fireEvent.change(screen.getByRole("textbox", { name: new RegExp(`Search ${k}s`) }), { target: { value: p } });
      await settle();
      fireEvent.keyDown(screen.getByRole("textbox", { name: new RegExp(`Search ${k}s`) }), { key: "Enter" });
    }
    fireEvent.click(screen.getByRole("button", { name: "More options ⤢" }));
    expect(onClose).toHaveBeenCalled();
    expect(JSON.parse(screen.getByTestId("carried").textContent!)).toEqual({
      draft: { title: "Cache it", brief: "By hash.", repo: "/code/kraft-plugins", chain: "default", spec: "docs/specs/a.md", plan: "docs/plans/a.md" }, members: false,
    });
  });
});
