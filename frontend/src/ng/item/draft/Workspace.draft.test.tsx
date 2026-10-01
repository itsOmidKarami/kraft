import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detail, stubFetch, V1 } from "../testkit";
import { usePaneMemory, Workspace } from "../Workspace";
import type { ItemDetail } from "../useItem";
import { ItemDraftProvider } from "./context";
import type { DraftView, MarkedOp } from "./types";

beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));
afterEach(() => vi.unstubAllGlobals());

const scan = { id: "scan", kind: "exec" as const, gate_after: null, tasks: ["scan.main.run"], steps: [["scan.main.run"]] };
const withScan = [...V1.slice(0, 3), scan, V1[3]];
const draft = (ops: MarkedOp[]): [number, DraftView] => [200, { ops, problems: [], checks: { budget: { spent_usd: 0, cap_usd: null } }, nodes: withScan, base_seq: 1, updated_at: null }];
const mount = (path: string, answer: [number, DraftView], over: Partial<ItemDetail> = {}, more: Record<string, [number, unknown]> = {}) => {
  stubFetch({ "GET /work-items/w1/draft": answer, ...more });
  const item = detail(over);
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        {["/work-items/:id", "/work-items/:id/nodes/:node"].map((p) => (
          <Route key={p} path={p} element={<ItemDraftProvider item={item} reload={() => {}}><Workspace item={item} reload={() => {}} /></ItemDraftProvider>} />
        ))}
      </Routes>
    </MemoryRouter>,
  );
};

describe("Workspace with a draft", () => {
  it("draws the chain the draft makes, the added node dashed green", async () => {
    mount("/work-items/w1", draft([{ op: "add_node", after: "verification", node: { id: "scan", extends: "security" }, passed: false }]));
    const node = await screen.findByRole("button", { name: "scan, node, not started" });
    expect(node.className).toContain("is-add");
    expect(screen.getByRole("button", { name: "merge_request, node, not started" }).className).not.toContain("is-add");
  });

  it("marks the overridden task in its node view, and only it", async () => {
    mount("/work-items/w1/nodes/verification", draft([{ op: "override", path: "verification.checks.lint", task_config: { model: "opus" }, passed: false }]));
    const lint = await screen.findByRole("button", { name: /^lint,/ });
    await vi.waitFor(() => expect(lint.querySelector(".mark-change")).not.toBeNull());
    expect(screen.getByRole("button", { name: /^code_review,/ }).querySelector(".mark-change")).toBeNull();
  });

  it("draws the item's own chain when there is no draft", async () => {
    mount("/work-items/w1", { ...draft([]), 1: { ...draft([])[1], nodes: V1 } });
    expect(await screen.findByRole("button", { name: "merge_request, node, not started" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^scan,/ })).toBeNull();
  });

  it("puts a + seam after every node after the current one, and none at or before it", async () => {
    mount("/work-items/w1", draft([]));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Add a library node here" })).toHaveLength(2));
  });

  it("offers every seam but the first before the item starts", async () => {
    mount("/work-items/w1", draft([]), { current_node_id: null, display_status: "paused" });
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Add a library node here" })).toHaveLength(4));
  });

  it("offers no seam on an ended item", async () => {
    mount("/work-items/w1", draft([]), { display_status: "done" });
    await screen.findByRole("button", { name: /^plan, node/ });
    expect(screen.queryByRole("button", { name: "Add a library node here" })).toBeNull();
  });

  it("opens the library's nodes from a seam", async () => {
    mount("/work-items/w1", draft([]), {}, { "GET /templates/library": [200, { components: [{ kind: "nodes", name: "security", definition: { steps: [{ id: "scan", tasks: [{ id: "sast" }] }] } }] }] });
    await userEvent.click((await screen.findAllByRole("button", { name: "Add a library node here" }))[0]);
    expect(await screen.findByRole("option", { name: /security/ })).toBeInTheDocument();
    expect(screen.getByText(/Runs after verification\./)).toBeInTheDocument();
  });
});
