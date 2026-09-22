import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { ChainNode, WorkerSession, WorkItemDocument } from "../../../types";
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

function renderDocs(
  over: {
    scope?: Scope;
    gatePending?: boolean;
    preselectPath?: string | null;
    item?: { title: string; bead_id: string | null };
    sessions?: WorkerSession[];
  } = {},
) {
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
      item={over.item}
      sessions={over.sessions}
    />,
  );
  return { onCount };
}

/** Row ids in the order shown. */
const ids = (sel = ".doc-row:not(.doc-fold)") => [...document.querySelectorAll(sel)].map((r) => r.getAttribute("data-document-id"));
const rowOf = (id: string) => document.querySelector(`[data-document-id="${id}"]`) as HTMLElement;

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
    expect(ids()).toEqual(["g1", "r2"]);
    expect([...document.querySelectorAll(".doc-fold-label")].map((t) => t.textContent)).toEqual(["spec · 1 spec", "verify · 1 review · 2 sessions"]);
    expect([...document.querySelectorAll(".doc-fold-count")].map((t) => t.textContent)).toEqual(["1", "3"]);
    // The gate's row and this node's one document. onCount fires from a
    // passive effect, a tick after the commit findByText anchors on --
    // settle on it rather than assert on a commit that doesn't imply it.
    await waitFor(() => expect(onCount).toHaveBeenLastCalledWith(2));
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
    expect(ids(".doc-row")).toHaveLength(6);
    await waitFor(() => expect(onCount).toHaveBeenLastCalledWith(6));
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
    expect(ids(".doc-row")).toEqual(["g1", "s1", "v1", "v2", "v3", "r2"]);
    await waitFor(() => expect(onCount).toHaveBeenLastCalledWith(6));
  });

  it("an intake attachment keeps its title first, with `hook · time`, a paperclip, no node and no path", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc("a1", "review", "plans", { attachment_kind: "plan", hook_point: "on.plan.requested" })],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    const row = (await screen.findByText("plans a1")).closest(".doc-row") as HTMLElement;
    expect(row.querySelector(".doc-sub")).toHaveTextContent(/^on\.plan\.requested/);
    expect(row.querySelector(".doc-sub")).not.toHaveTextContent("review");
    expect(row.querySelector(".doc-kind")).toHaveTextContent(/^plan$/);
    expect(row).toHaveAttribute("title", ".engineering/plans/a1.md");
    expect(within(row).queryByText(".engineering/plans/a1.md")).toBeNull();
    expect(within(row).getByLabelText("attached at intake")).toHaveAttribute("title", "attached at intake");
  });

  it("never shows a sha on a row: an id-titled summary is its hook, with no second line (W11 · H)", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [
        doc("h1", "review", "sessions", { title: "8cbfe6e27c1044b3e445c0f0d726357d", hook_point: "on.review.local.run" }),
        doc("h2", "review", "sessions", { title: "Session 9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6", hook_point: "on.mr.describe" }),
      ],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    await screen.findByText("on.review.local.run");
    expect([...document.querySelectorAll(".doc-row .doc-title")].map((t) => t.textContent)).toEqual(["on.review.local.run", "on.mr.describe"]);
    expect(document.querySelectorAll(".doc-line2")).toHaveLength(0);
    expect(document.querySelector(".linked-docs")!.textContent).not.toMatch(/[0-9a-f]{8}…?[0-9a-f]{4,}/);
  });

  it("an escalation turn's summary is kind escalation, and its session id is not row text", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc("e1", "review", "sessions", { hook_point: "escalation", worker_session_id: "8cbfe6e27c1044b3e445c0f0d726357d", attempt: 2, round: 0 })],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    const row = (await screen.findByText(/^escalation · turn 2/)).closest(".doc-row") as HTMLElement;
    expect(row.querySelector(".doc-kind")).toHaveTextContent(/^escalation$/);
    expect(within(row).queryByTitle("8cbfe6e27c1044b3e445c0f0d726357d")).toBeNull();
    expect(row.textContent).not.toContain("8cbfe6e2");
  });
});

describe("Documents rows (W13 · B)", () => {
  const item = { title: "Chain review: cover hook configs and escalation logic", bead_id: "Kraft-df4tc" };
  const runs = [
    doc("d1", "review", "sessions", { hook_point: "on.review.requested", attempt: 1, round: 0, title: "Chain-review diff review (Kraft-df4tc)", worker_session_id: "s1", indexed_at: "2026-09-14T10:00:00Z" }),
    doc("d2", "review", "sessions", { hook_point: "on.test.run", attempt: 1, round: 4, title: "Fix-loop judge: verify (round 4 decision)", worker_session_id: "s2", indexed_at: "2026-09-14T09:00:00Z" }),
    doc("d3", "review", "sessions", { hook_point: "on.security.review", attempt: 2, round: 0, title: "Security review — Kraft-df4tc (chain review: hook configs and escalation logic)", worker_session_id: "s3", indexed_at: "2026-09-14T11:00:00Z" }),
    doc("d4", "review", "sessions", { hook_point: "on.review.summary", attempt: 1, round: 0, title: item.title, worker_session_id: "s4", indexed_at: "2026-09-14T08:00:00Z" }),
  ];
  const session = (id: string, created: string) => ({ id, created_at: created }) as WorkerSession;

  beforeEach(() => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: runs });
  });

  it("a summary row is `hook · run label · time`, then its cleaned title as a second line", async () => {
    renderDocs({ gatePending: false, preselectPath: null, item });
    await screen.findByText(/^on\.review\.requested · attempt 1/);
    expect(rowOf("d1").querySelector(".doc-title")).toHaveTextContent(/^on\.review\.requested · attempt 1 · /);
    expect(rowOf("d1").querySelector(".doc-line2")).toHaveTextContent(/^Chain-review diff review$/);
    expect(rowOf("d2").querySelector(".doc-title")).toHaveTextContent(/^on\.test\.run · round 4/);
    expect(rowOf("d2").querySelector(".doc-line2")).toHaveTextContent("Fix-loop judge: verify (round 4 decision)");
    expect(rowOf("d3").querySelector(".doc-title")).toHaveTextContent(/^on\.security\.review · attempt 2/);
    expect(rowOf("d3").querySelector(".doc-line2")).toHaveTextContent(/^Security review$/);
    // only the item's title: no second line
    expect(rowOf("d4").querySelector(".doc-line2")).toBeNull();
    // no node on the row, the path is its tooltip
    expect(rowOf("d1")).toHaveAttribute("title", ".engineering/sessions/d1.md");
    expect(rowOf("d1").textContent).not.toContain("review ·");
  });

  it("an older server with no run info shows just the hook", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc("o1", "review", "sessions", { hook_point: "on.review.requested", title: "Old one" })],
    });
    renderDocs({ gatePending: false, preselectPath: null });
    const title = (await screen.findByText("Old one")).closest(".doc-row")!.querySelector(".doc-title")!;
    expect(title.textContent).toMatch(/^on\.review\.requested( · .*)?$/);
    expect(title.textContent).not.toMatch(/attempt|round|turn/);
  });

  it("puts the newest run on top: the session's created_at when known, indexed_at otherwise", async () => {
    renderDocs({ gatePending: false, preselectPath: null, item });
    await screen.findByText(/^on\.review\.requested/);
    expect(ids()).toEqual(["d3", "d1", "d2", "d4"]);
    // A hand-rolled `document.body.innerHTML = ""` here left the first React
    // root mounted on a detached node instead of unmounting it. cleanup()
    // properly unmounts and removes the container between the two renders.
    cleanup();
    renderDocs({
      gatePending: false,
      preselectPath: null,
      item,
      sessions: [session("s1", "2026-09-14T07:00:00Z"), session("s2", "2026-09-14T12:00:00Z"), session("s3", "2026-09-14T06:00:00Z"), session("s4", "2026-09-14T05:00:00Z")],
    });
    await screen.findAllByText(/^on\.review\.requested/);
    expect(ids()).toEqual(["d2", "d1", "d3", "d4"]);
  });

  it("the filter matches hook, run label, cleaned title and path", async () => {
    renderDocs({ gatePending: false, preselectPath: null, item });
    await screen.findByText(/^on\.review\.requested/);
    const box = screen.getByLabelText("filter documents");
    await userEvent.type(box, "on.test");
    expect(ids()).toEqual(["d2"]);
    await userEvent.clear(box);
    await userEvent.type(box, "round 4");
    expect(ids()).toEqual(["d2"]);
    await userEvent.clear(box);
    await userEvent.type(box, "security review");
    expect(ids()).toEqual(["d3"]);
    await userEvent.clear(box);
    await userEvent.type(box, "sessions/d4");
    expect(ids()).toEqual(["d4"]);
  });
});
