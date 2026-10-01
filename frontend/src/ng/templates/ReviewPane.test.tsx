import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigDraft } from "./draft/useConfigDraft";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { Result, StaleBody } from "./draft/types";
import { ReviewPane } from "./ReviewPane";

const SIZE = { width: 380, overlay: false, handle: undefined };
const STALE: StaleBody = {
  detail: "published since this draft began: chains/default.yaml",
  files: { "chains/default.yaml": { published: "a\n", draft: "id: default\nnodes: []\n", diff: "--- published/chains/default.yaml\n+++ draft/chains/default.yaml\n@@ -3 +3 @@\n-    model: sonnet\n+    model: opus\n" } },
};

function mount(o: { result?: Partial<Result>; stale?: StaleBody | null; published?: string | null; answers?: Record<string, { status: number; body?: unknown }> } = {}) {
  const ans = (k: string, d = 200) => Promise.resolve(o.answers?.[k] ?? { status: d, body: {} });
  const draft = {
    view: { ...DEFAULT_VIEW, draft: true, result: { ...DEFAULT_VIEW.result, ...o.result } },
    stale: o.stale ?? null,
    publish: vi.fn(() => ans("publish")),
    keepMine: vi.fn(() => ans("keepMine")),
    discard: vi.fn(() => ans("discard", 204)),
  } as unknown as ConfigDraft;
  const cb = { onCollapse: vi.fn(), onExpand: vi.fn(), onFix: vi.fn(), onHighlight: vi.fn(), onDone: vi.fn() };
  render(<ReviewPane draft={draft} chain="default" published={o.published === undefined ? "id: default\nnodes:\n  - id: spec\n" : o.published} open size={SIZE} {...cb} />);
  return { draft, ...cb };
}
/** A diff line by its exact text (each ends with its newline). */
const line = (text: string) => [...document.querySelectorAll(".rv-line")].find((e) => e.textContent === `${text}\n`);
const CHANGES = [{ path: "implementation.main.implement", kind: "change" as const, summary: "model" }, { path: "security_approval", kind: "add" as const, summary: "added" }];

beforeEach(() => vi.restoreAllMocks());

describe("Review & publish pane", () => {
  it("says the draft resolves, lists its changes and who it affects, and publishes", async () => {
    const { draft, onDone, onHighlight } = mount({ result: { changes: CHANGES, impact: { running: 2, repos: ["/code/kraft"] } } });
    expect(screen.getByRole("heading", { name: "Draft · 2 changes" })).toBeInTheDocument();
    expect(screen.getByText("✓ resolves · ready to publish")).toBeInTheDocument();
    expect(screen.getByText("2 items keep the version they started on")).toBeInTheDocument();
    expect(screen.getByText("/code/kraft default to it")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /security_approval/ }));
    expect(onHighlight).toHaveBeenCalledWith("security_approval");
    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    expect(draft.publish).toHaveBeenCalled();
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("blocks publishing while there are problems or a YAML error, and Fix → goes to the path", async () => {
    const { onFix } = mount({ result: { problems: [{ path: "lint", field: null, message: "Value error, 'steps' must not be empty", file: "f", line: 3, col: 5 }] } });
    expect(screen.getByText("✕ doesn't resolve · 1 problem block publishing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByText("'steps' must not be empty")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Fix →" }));
    expect(onFix).toHaveBeenCalledWith("lint");
    document.body.innerHTML = "";
    mount({ result: { yaml_error: { file: "chains/default.yaml", line: 4, col: 3, message: "expected ']'" } } });
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByText("chains/default.yaml, line 4")).toBeInTheDocument();
  });

  it("confirms a discard in place, then discards", async () => {
    const { draft, onDone } = mount({ result: { changes: CHANGES } });
    await userEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(draft.discard).not.toHaveBeenCalled();
    expect(screen.getByText("Discard 2 changes?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.queryByText("Discard 2 changes?")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(draft.discard).toHaveBeenCalled();
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("on a 409 shows the server's diff, keeps the draft, copies it, or keeps this version and publishes", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    const { draft, onDone } = mount({ stale: STALE });
    const box = screen.getByRole("alert");
    expect(within(box).getByText("Published since this draft began")).toBeInTheDocument();
    expect(line("-     model: sonnet")).toHaveClass("is-del");
    expect(line("+     model: opus")).toHaveClass("is-add");
    await userEvent.click(within(box).getByRole("button", { name: "Copy draft YAML" }));
    expect(writeText).toHaveBeenCalledWith("id: default\nnodes: []\n");
    await userEvent.click(within(box).getByRole("button", { name: "Keep my version and publish" }));
    expect(draft.keepMine).toHaveBeenCalled();
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("lists the problems a 422 publish names", async () => {
    mount({ answers: { publish: { status: 422, body: { detail: "1 problem(s)", problems: [{ path: "verification", field: "skippable", message: "broken by the library", file: "f", line: 1, col: 1 }] } } } });
    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("broken by the library")).toBeInTheDocument();
  });

  it("warns when a canvas edit drops comments (R8), and diffs the YAML against the published file", async () => {
    mount({ result: { warnings: [{ file: "chains/default.yaml", message: "comments in this file will be dropped" }] }, published: "id: default\nnodes:\n  - id: old\n" });
    expect(screen.getByText("chains/default.yaml: comments in this file will be dropped.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "YAML diff" }));
    expect(line("-   - id: old")).toHaveClass("is-del");
    expect(line("+ id: default")).toBeUndefined();
  });
});
