import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { ChainNode, WorkItemDocument } from "../../../types";
import { Documents } from "./Documents";
import type { Scope } from "./Tasks";

const NODES: ChainNode[] = [
  { id: "spec", tasks: [], gate_after: "spec_approval" },
  { id: "implement", tasks: [], gate_after: null },
  { id: "verify", tasks: [], gate_after: null },
  { id: "review", tasks: [], gate_after: "code_review" },
];

const doc = (id: string, node: string, kind: string, over: Partial<WorkItemDocument> = {}): WorkItemDocument => ({
  document_id: id,
  repo: "/r",
  title: `${kind} ${id}`,
  kind,
  source_kind: kind === "sessions" ? "session_summary" : "artifact",
  path: `.engineering/${kind}/${id}.md`,
  node_id: node,
  hook_point: `on.${node}.run`,
  worker_session_id: null,
  attachment_kind: null,
  indexed_at: "t",
  ...over,
});

const GATE_PATH = ".engineering/reviews/gate.md";
const DOCS = [
  doc("g1", "review", "reviews", { path: GATE_PATH }),
  doc("r2", "review", "sessions"),
  doc("v1", "verify", "reviews"),
  doc("v2", "verify", "sessions"),
  doc("v3", "verify", "sessions"),
  doc("s1", "spec", "specs"),
];

function renderDocs(over: { scope?: Scope; gatePending?: boolean; preselectPath?: string | null } = {}) {
  const onCount = vi.fn();
  const gatePending = over.gatePending ?? true;
  render(
    <Documents
      workItemId="w1"
      eventCount={0}
      selected={null}
      onSelect={() => {}}
      nodeId="review"
      nodes={NODES}
      scope={over.scope ?? "node"}
      onScope={() => {}}
      onCount={onCount}
      preselectPath={"preselectPath" in over ? over.preselectPath : GATE_PATH}
      gatePending={gatePending}
      gateArtifactPending={gatePending}
    />,
  );
  return { onCount };
}

const titles = (sel: string) => [...document.querySelectorAll(sel)].map((t) => t.textContent);

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: DOCS });
});

describe("Documents (W11 · G)", () => {
  it.each<Scope>(["node", "all"])("pins the gate document above everything under %s", async (scope) => {
    renderDocs({ scope });
    await screen.findByText("reviews g1");
    const first = document.querySelector(".linked-docs .doc-row");
    expect(first).toHaveAttribute("data-gate");
    expect(first).toHaveTextContent("reviews g1");
  });

  it("this node lists only its own documents, and folds each other node with documents into one counted row", async () => {
    const { onCount } = renderDocs();
    await screen.findByText("reviews g1");
    expect(titles(".doc-row:not(.doc-fold) .doc-title")).toEqual(["reviews g1", "sessions r2"]);
    expect(titles(".doc-fold-label")).toEqual(["spec · 1 spec", "verify · 1 review · 2 sessions"]);
    expect(titles(".doc-fold-count")).toEqual(["1", "3"]);
    // The gate's row and this node's one document.
    expect(onCount).toHaveBeenLastCalledWith(2);
  });

  it("with no node selected, nothing is folded: every document shows and counts", async () => {
    const onCount = vi.fn();
    render(
      <Documents
        workItemId="w1"
        eventCount={0}
        selected={null}
        onSelect={() => {}}
        nodeId={null}
        nodes={NODES}
        scope="node"
        onScope={() => {}}
        onCount={onCount}
        gatePending={false}
        gateArtifactPending={false}
      />,
    );
    await screen.findByText("reviews g1");
    expect(document.querySelectorAll(".doc-fold")).toHaveLength(0);
    expect(titles(".doc-row .doc-title")).toHaveLength(6);
    expect(onCount).toHaveBeenLastCalledWith(6);
  });

  it("a folded node expands inline", async () => {
    renderDocs();
    await screen.findByText("reviews g1");
    const fold = screen.getByRole("button", { name: /verify · 1 review · 2 sessions/ });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(fold);
    expect(fold).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("sessions v3")).toBeInTheDocument();
  });

  it("all opens every node in chain order and counts every document", async () => {
    const { onCount } = renderDocs({ scope: "all" });
    await screen.findByText("reviews g1");
    expect(document.querySelectorAll(".doc-fold")).toHaveLength(0);
    expect(titles(".doc-row .doc-title")).toEqual(["reviews g1", "specs s1", "reviews v1", "sessions v2", "sessions v3", "sessions r2"]);
    expect(onCount).toHaveBeenLastCalledWith(6);
  });

  it("the filter matches title, path, node and hook across every node, whatever the scope", async () => {
    renderDocs();
    await screen.findByText("reviews g1");
    const box = screen.getByLabelText("filter documents");
    await userEvent.type(box, "verify");
    expect(titles(".doc-row:not([data-gate]) .doc-title")).toEqual(["reviews v1", "sessions v2", "sessions v3"]);
    await userEvent.clear(box);
    await userEvent.type(box, "on.spec.run");
    expect(titles(".doc-row:not([data-gate]) .doc-title")).toEqual(["specs s1"]);
    await userEvent.clear(box);
    await userEvent.type(box, "sessions/r2");
    expect(titles(".doc-row:not([data-gate]) .doc-title")).toEqual(["sessions r2"]);
  });

  it("a row is icon · title · node · hook · time · one kind label, a paperclip for an intake attachment, and no path", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc("a1", "review", "plans", { attachment_kind: "plan", hook_point: "on.plan.requested" })],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    const row = (await screen.findByText("plans a1")).closest(".doc-row") as HTMLElement;
    expect(row.querySelector(".doc-sub")).toHaveTextContent("review · on.plan.requested");
    expect(row.querySelector(".doc-kind")).toHaveTextContent(/^plan$/);
    expect(row).toHaveAttribute("title", ".engineering/plans/a1.md");
    expect(within(row).queryByText(".engineering/plans/a1.md")).toBeNull();
    expect(within(row).getByLabelText("attached at intake")).toHaveAttribute("title", "attached at intake");
  });

  it("never shows a sha as a row title: an id-titled summary names its hook (W11 · H)", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [
        doc("h1", "review", "sessions", { title: "8cbfe6e27c1044b3e445c0f0d726357d", hook_point: "on.review.local.run" }),
        doc("h2", "review", "sessions", { title: "Session 9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6", hook_point: "on.mr.describe" }),
      ],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    await screen.findByText("Session · on.review.local.run");
    expect(titles(".doc-row .doc-title")).toEqual(["Session · on.review.local.run", "Session · on.mr.describe"]);
    expect(titles(".doc-title").join(" ")).not.toMatch(/[0-9a-f]{8}…?[0-9a-f]{4,}/);
  });

  it("names an escalation turn's summary escalation, and shows its session id", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc("e1", "review", "sessions", { hook_point: "escalation", worker_session_id: "8cbfe6e27c1044b3e445c0f0d726357d" })],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    const row = (await screen.findByText("sessions e1")).closest(".doc-row") as HTMLElement;
    expect(row.querySelector(".doc-kind")).toHaveTextContent(/^escalation$/);
    expect(within(row).getByTitle("8cbfe6e27c1044b3e445c0f0d726357d")).toHaveTextContent("8cbfe6e2…6357d");
  });
});
