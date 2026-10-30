import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChainNodeView, ChainsList, ChainView } from "./Chains";
import { mountAt, posts, view, where } from "./testkit";
import { mergeView } from "../../templates/draft/fixture.merge";

const NODES = [
  { id: "spec", kind: "exec", tasks: ["spec.main.author"], steps: [["spec.main.author"]], gate_after: null },
  { id: "spec_approval", kind: "gate", tasks: [], steps: [], gate_after: null, reject_to: "spec", auto_escalate: true },
  { id: "verification", kind: "exec", tasks: ["verification.checks.lint", "verification.review.code_review"], steps: [["verification.checks.lint"], ["verification.review.code_review"]], gate_after: null, fix_loop: "verification_fix_loop", on_failure: ["verification.repair.fix"] },
];
const CHAINS = [{ id: "default", nodes: NODES, gates: 1 }, { id: "docs_only", nodes: [NODES[0]], gates: 0, addon: { id: "release@acme", version: "1.0.0" } }, { id: "broken", nodes: [], gates: 0, error: "no such library node" }];
const answers = (more: Record<string, [number, unknown]> = {}) => ({ "GET /templates/chains": [200, CHAINS] as [number, unknown], "GET /templates/chains/default": [200, { id: "default", text: "id: default\nnodes: []\n" }] as [number, unknown], ...more });
afterEach(() => vi.unstubAllGlobals());

describe("Chains (L.1)", () => {
  it("lists the chains with their node and gate counts", async () => {
    const { calls } = mountAt(<ChainsList />, "/templates/chains", "/templates/chains", answers());
    expect(await screen.findByRole("link", { name: /^default/ })).toHaveTextContent("3 nodes · 1 gate");
    expect(screen.getByRole("link", { name: /^docs_only/ })).toHaveTextContent("1 nodes · 0 gates");
    // An add-on's chain says so; a local one does not.
    expect(screen.getByRole("link", { name: /^docs_only/ })).toHaveTextContent("release@acme 1.0.0");
    expect(screen.getByRole("link", { name: /^default/ })).not.toHaveTextContent("release@acme");
    expect(screen.queryByText(/are on desktop/)).toBeNull();
    expect(screen.getByText("no such library node")).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
    await userEvent.click(screen.getByRole("link", { name: /^default/ }));
    expect(where()).toBe("/templates/chains/default");
  });

  it("an inherited chain's row has an `inherited from` chip", async () => {
    const inherited = { ...CHAINS[1], id: "platform:default", addon: { id: "company@corp", version: "3.2.0", inheritance: { origin: "inherited" as const, parent: { id: "company@corp", namespace: "company" }, author: { id: "company@corp", version: "3.2.0" } } } };
    mountAt(<ChainsList />, "/templates/chains", "/templates/chains", answers({ "GET /templates/chains": [200, [CHAINS[0], CHAINS[1], inherited]] }));
    const row = await screen.findByRole("link", { name: /^platform:default/ });
    expect(within(row).getByText("inherited from company")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^docs_only/ })).not.toHaveTextContent("inherited from");
  });

  const src = (value: unknown, source = "default") => ({ value, source });
  const SOURCES = {
    "": {},
    verification: { read_only: src(false), skippable: src(true, "chain"), model: src(null), effort: src("high", "chain"), "policy.time_cap_minutes": src(null), "policy.token_budget": src(5000, "chain"), "policy.timeout_minutes": { value: null, source: "default", recovery: true } },
    spec_approval: { reject_to: src("spec", "chain"), message: src("Review the spec.", "chain"), auto_review_mode: src("checks_first", "chain") },
    "verification.checks.lint": { command: src("just lint", "chain") },
  };
  const DRAFT = (r = {}) => view("chains", "default", { draft: true, files: { "chains/default.yaml": "id: default\n" } }, { sources: SOURCES as never, model: { "chains/default.yaml": { id: "default", nodes: [{ id: "spec", kind: "exec" }, { id: "spec_approval", kind: "gate" }, { id: "verification", kind: "exec" }] } } as never, changes: [{ path: "verification", kind: "change", summary: "effort high", fields: ["effort", "policy"] }, { path: "spec", kind: "change", summary: "x" }], ...r });
  const OPS = [200, { ...DRAFT(), ops: [] }] as [number, unknown];
  const open = (path = "/templates/chains/default", route = "/templates/chains/:chain", more: Record<string, [number, unknown]> = {}) => mountAt(path.includes("/nodes/") ? <ChainNodeView /> : <ChainView />, path, path.includes("/nodes/") ? "/templates/chains/:chain/nodes/:node" : route, answers({ "GET /drafts/chains/default": [200, DRAFT()], "POST /drafts/chains/default/ops": OPS, "GET /harnesses/profiles": [200, { profiles: [{ id: "claude", provider: "anthropic" }], agent_profiles: [{ id: "coder" }] }], "GET /harnesses/providers": [200, { valid: { anthropic: { capabilities: { effort: { values: ["low", "high"] } } } } }], ...more }));

  it("a chain shows its draft, its nodes and caps, and its YAML read only", async () => {
    const { calls } = open();
    expect(await screen.findByText("draft · 2 changes")).toBeInTheDocument();
    // A node row reads its name, with the id under it in mono.
    expect(screen.getByRole("link", { name: /^verification/ })).toHaveTextContent("verification · exec · 2 steps · fix loop");
    const gate = screen.getByRole("link", { name: /^approve spec/ });
    expect(gate).toHaveTextContent("spec_approval · gate · reject to spec");
    expect(within(gate).getByText(/^spec_approval/)).toHaveClass("ph-mono");
    expect(within(gate).getByText("approve spec")).not.toHaveClass("ph-mono");
    expect(screen.getByText("Adding, removing and reordering nodes and steps is on desktop.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Chain-wide time caps" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(await screen.findByText(/id: default/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(posts(calls)).toEqual([]);
  });

  it("an extending chain names its parent, and marks the nodes it adds or overrides as the canvas does", async () => {
    const team = { id: "team", nodes: NODES, gates: 1, extends: "default" };
    const extending = { parent: "default", nodes: [], origins: { spec: "inherited", spec_approval: "overridden", verification: "added" }, removed: [] };
    mountAt(<ChainView />, "/templates/chains/team", "/templates/chains/:chain", answers({ "GET /templates/chains": [200, [...CHAINS, team]], "GET /templates/chains/team": [200, { id: "team", text: "id: team\n" }], "GET /drafts/chains/team": [200, view("chains", "team", {}, { extending: extending as never })] }));
    expect(await screen.findByText("extends default · 3 nodes · 1 gate")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: /^verification/ })).toHaveTextContent("added here"));
    expect(screen.getByRole("link", { name: /^approve spec/ })).toHaveTextContent("overridden here");
    expect(screen.getByRole("link", { name: /^spec.*exec · 1 step/ })).not.toHaveTextContent(/here/);
  });

  it("a chain-wide cap is parsed as the desktop parses it, then sent as set_field", async () => {
    const { calls } = open();
    await userEvent.click(await screen.findByRole("button", { name: /^running cap/ }));
    const input = screen.getByLabelText("running cap", { selector: "input,textarea" });
    await userEvent.type(input, "abc{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("A whole number above 0.");
    expect(posts(calls)).toEqual([]);
    await userEvent.clear(input);
    await userEvent.type(input, "90{Enter}");
    await waitFor(() => expect(posts(calls).map((c) => c.body)).toEqual([{ ops: [{ op: "set_field", path: "", field: "policy.time_cap_minutes", value: 90 }] }]));
  });

  it("a node's settings edit through a switch and a sheet, and a changed one is marked", async () => {
    const { calls } = open("/templates/chains/default/nodes/verification");
    expect(await screen.findByText("effort")).toBeInTheDocument();
    expect(screen.getByText("high").className).toContain("ph-is-changed");
    // A policy key is reported by its group, so each one set shows as changed.
    expect(screen.getByText("5000").className).toContain("ph-is-changed");
    expect(screen.getAllByText("(changed)")).toHaveLength(2);
    // A cap that is its recovery's says so, and is not folded away as unset.
    expect(screen.getByRole("button", { name: /^wall clock.*applies to its on-failure recovery/ })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: /^Not set/ })).queryByText("wall clock")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^effort/ }));
    await userEvent.click(await screen.findByRole("radio", { name: /^low/ }));
    await waitFor(() => expect(posts(calls).map((c) => c.body)).toEqual([{ ops: [{ op: "set_field", path: "verification", field: "effort", value: "low" }] }]));
    await userEvent.click(screen.getByRole("switch", { name: /^skippable/ }));
    await waitFor(() => expect(posts(calls)[1].body).toEqual({ ops: [{ op: "set_field", path: "verification", field: "skippable", value: false }] }));
    expect(screen.getByText("Adding, removing and reordering nodes and steps is on desktop.")).toBeInTheDocument();
  });

  it("a gate's reject target is one of the nodes before it", async () => {
    open("/templates/chains/default/nodes/spec_approval");
    await userEvent.click(await screen.findByRole("button", { name: /^reject to/ }));
    await userEvent.type(screen.getByLabelText("reject to", { selector: "input" }), "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(/exec node before this gate/);
  });

  it("a gate's reviewer mode reads and is picked in the desktop's words, and sends the value", async () => {
    const { calls } = open("/templates/chains/default/nodes/spec_approval");
    const row = await screen.findByRole("button", { name: /^reviewer may/ });
    expect(row).toHaveTextContent("check first (reject, or hand it to you)");
    await userEvent.click(row);
    await userEvent.click(await screen.findByRole("radio", { name: /^decide \(can approve\)/ }));
    await waitFor(() => expect(posts(calls).map((c) => c.body)).toEqual([{ ops: [{ op: "set_field", path: "spec_approval", field: "auto_review_mode", value: "decides" }] }]));
  });

  describe("a merge node's on merge rejected", () => {
    const MERGE = { id: "merge", kind: "exec", tasks: ["merge.main.merge"], steps: [["merge.main.merge"]], gate_after: null };
    const openMerge = (policy: boolean, v = mergeView({ policy })) => mountAt(<ChainNodeView />, "/templates/chains/default/nodes/merge", "/templates/chains/:chain/nodes/:node", answers({ "GET /templates/chains": [200, [{ ...CHAINS[0], nodes: [...NODES, MERGE] }]], "GET /drafts/chains/default": [200, v], "POST /drafts/chains/default/ops": [200, { ...v, ops: [] }] }));
    const pick = async (choice: RegExp) => {
      await userEvent.click(await screen.findByRole("button", { name: /^on merge rejected/ }));
      await userEvent.click(await screen.findByRole("radio", { name: choice }));
    };
    const sent = (calls: ReturnType<typeof openMerge>["calls"]) => posts(calls).map((c) => (c.body as { ops: unknown[] }).ops);

    it("a first restart picked adds the policy whole, its repair empty", async () => {
      const { calls } = openMerge(false);
      await userEvent.click(await screen.findByRole("button", { name: /^on merge rejected/ }));
      expect(screen.getByRole("radio", { name: /^not set/ })).toBeInTheDocument();
      await userEvent.click(screen.getByRole("radio", { name: /^verification/ }));
      await waitFor(() => expect(sent(calls)).toEqual([[{ op: "add_handler", path: "merge", kind: "on_merge_rejected" }, { op: "set_field", path: "merge", field: "on_merge_rejected.restart_from", value: "verification" }]]));
    });

    it.each([
      { id: "another restart moves it", choice: /^verification/, value: "verification" },
      { id: "its empty choice says it drops the repair, and does", choice: /^remove \(drops the repair\)/, value: null },
    ])("one set: $id", async ({ choice, value }) => {
      const { calls } = openMerge(true);
      await pick(choice);
      await waitFor(() => expect(sent(calls)).toEqual([[{ op: "set_field", path: "merge", field: "on_merge_rejected.restart_from", value }]]));
    });

    it("with no exec node before the final gate, it reads, and says why", async () => {
      openMerge(true, mergeView({ tail: true }));
      expect(await screen.findByText("No earlier exec node to restart from.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^on merge rejected/ })).toBeNull();
    });
  });

  it("a task's settings are its own path's", async () => {
    const { calls } = open("/templates/chains/default/nodes/verification?sel=verification.checks.lint");
    expect(await screen.findByRole("heading", { level: 1, name: "lint" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^command/ }));
    await userEvent.type(screen.getByLabelText("command", { selector: "input,textarea" }), "x{Enter}");
    await waitFor(() => expect(posts(calls)[0].body).toEqual({ ops: [{ op: "set_field", path: "verification.checks.lint", field: "command", value: "just lintx" }] }));
  });

  it("Review & publish sits behind the review sheet and publishes the chain's draft", async () => {
    const { calls } = open("/templates/chains/default", "/templates/chains/:chain", { "POST /drafts/chains/default/publish": [200, { published: ["chains/default.yaml"] }] });
    await userEvent.click(await screen.findByRole("button", { name: "Review & publish" }));
    expect(await screen.findByText("effort high")).toBeInTheDocument();
    expect(posts(calls)).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/drafts/chains/default/publish"]));
  });

  it("an add-on's chain edits nothing", async () => {
    open("/templates/chains/docs_only", "/templates/chains/:chain", { "GET /drafts/chains/docs_only": [200, view("chains", "docs_only", { addon: { id: "release@acme", version: "1.0.0" } })] });
    expect(await screen.findByRole("link", { name: /^spec/ })).toBeInTheDocument();
    expect(screen.queryByText(/on desktop/)).toBeNull();
    expect(screen.queryByRole("button", { name: /cap/ })).toBeNull();
  });

  it("a child add-on's chain page says it is inherited in its header", async () => {
    const addon = { id: "company@corp", version: "3.2.0", inheritance: { origin: "inherited" as const, parent: { id: "company@corp", namespace: "company" }, author: { id: "company@corp", version: "3.2.0" } } };
    open("/templates/chains/docs_only", "/templates/chains/:chain", { "GET /drafts/chains/docs_only": [200, view("chains", "docs_only", { addon })], "GET /templates/chains": [200, [CHAINS[0], { ...CHAINS[1], addon }]] });
    expect(await screen.findByText(/^company@corp 3.2.0 · inherited from company · /)).toBeInTheDocument();
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
