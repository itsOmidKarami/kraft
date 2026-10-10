import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChainNodeView, ChainsList, ChainView } from "./Chains";
import { mountAt, posts, where } from "./testkit";

const NODES = [
  { id: "spec", kind: "exec", tasks: ["spec.main.author"], steps: [["spec.main.author"]], gate_after: null },
  { id: "spec_approval", kind: "gate", tasks: [], steps: [], gate_after: null, reject_to: "spec", auto_escalate: true },
  { id: "verification", kind: "exec", tasks: ["verification.checks.lint", "verification.review.code_review"], steps: [["verification.checks.lint"], ["verification.review.code_review"]], gate_after: null, fix_loop: "verification_fix_loop", on_failure: ["verification.repair.fix"] },
];
const CHAINS = [{ id: "default", nodes: NODES, gates: 1 }, { id: "docs_only", nodes: [NODES[0]], gates: 0, plugin: { id: "release@acme", version: "1.0.0" } }, { id: "broken", nodes: [], gates: 0, error: "no such library node" }];
const answers = (more: Record<string, [number, unknown]> = {}) => ({ "GET /templates/chains": [200, CHAINS] as [number, unknown], "GET /templates/chains/default": [200, { id: "default", text: "id: default\nnodes: []\n" }] as [number, unknown], ...more });
afterEach(() => vi.unstubAllGlobals());

describe("Chains, read only (L.1)", () => {
  it("lists the chains with their node and gate counts, and the desktop footnote", async () => {
    const { calls } = mountAt(<ChainsList />, "/templates/chains", "/templates/chains", answers());
    expect(await screen.findByRole("link", { name: /^default/ })).toHaveTextContent("3 nodes · 1 gate");
    expect(screen.getByRole("link", { name: /^docs_only/ })).toHaveTextContent("1 nodes · 0 gates");
    // A plugin's chain says so; a local one does not.
    expect(screen.getByRole("link", { name: /^docs_only/ })).toHaveTextContent("release@acme 1.0.0");
    expect(screen.getByRole("link", { name: /^default/ })).not.toHaveTextContent("release@acme");
    expect(screen.getByText(/The graph canvas, drag to reorder, adding steps, drafts and publish are on desktop/)).toBeInTheDocument();
    expect(screen.getByText("no such library node")).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
    await userEvent.click(screen.getByRole("link", { name: /^default/ }));
    expect(where()).toBe("/templates/chains/default");
  });

  it("a chain lists its nodes, says a desktop draft is open, shows its YAML, and sends nothing", async () => {
    const { calls } = mountAt(<ChainView />, "/templates/chains/default", "/templates/chains/:chain", answers({ "GET /drafts": [200, [{ area: "chains", key: "default", files: ["chains/default.yaml"], changes: 2, problems: 0, updated_at: "x" }]] }));
    expect(await screen.findByRole("heading", { level: 1, name: "default" })).toBeInTheDocument();
    expect(await screen.findByText("draft · 2 changes")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^verification/ })).toHaveTextContent("exec · 2 steps · fix loop");
    expect(screen.getByRole("link", { name: /^spec_approval/ })).toHaveTextContent("gate · reject to spec");
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(await screen.findByText(/id: default/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Review & publish|Discard/ })).toBeNull();
    expect(posts(calls)).toEqual([]);
  });

  it("a node page reads its fields, a gate's reject target included", async () => {
    mountAt(<ChainNodeView />, "/templates/chains/default/nodes/spec_approval", "/templates/chains/:chain/nodes/:node", answers());
    expect(await screen.findByText("an agent reads it first")).toBeInTheDocument();
    expect(screen.getByText("spec")).toBeInTheDocument();
  });

  it("an exec node lists its steps, fix loop and failure handler", async () => {
    mountAt(<ChainNodeView />, "/templates/chains/default/nodes/verification", "/templates/chains/:chain/nodes/:node", answers());
    expect(await screen.findByText("step checks")).toBeInTheDocument();
    expect(screen.getByText("lint")).toBeInTheDocument();
    expect(screen.getByText("verification_fix_loop")).toBeInTheDocument();
    expect(screen.getByText("fix")).toBeInTheDocument();
  });
});
