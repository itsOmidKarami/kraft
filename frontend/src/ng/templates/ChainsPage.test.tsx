import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import { ChainsPage } from "./ChainsPage";
import * as d from "./draft/draftApi";
import { DEFAULT_VIEW } from "./draft/fixture.default";
import type { DraftView, Result } from "./draft/types";

const view = (extra: Partial<Result> = {}, draft = true): DraftView => ({ ...DEFAULT_VIEW, draft, result: { ...DEFAULT_VIEW.result, ...extra } });
const ok = <T,>(body: T) => Promise.resolve({ status: 200, body });

let where = "";
function Where() {
  where = useLocation().pathname;
  return null;
}

const mount = (path = "/templates/chains/default") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/chains/:chain" element={<ChainsPage />} />
          <Route path="/templates/chains/:chain/nodes/:node" element={<ChainsPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

const canvas = () => screen.findByRole("group", { name: "default" });

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(view({}, false)));
  useStore.setState({ workItems: {}, connection: "open" } as never);
});

describe("Chains page: the chain canvas", () => {
  it("draws the chain's nodes in order, with marks and problem rings from the draft", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view({
      changes: [{ path: "implementation.main.implement", kind: "change", summary: "model" }, { path: "local_review", kind: "add", summary: "added" }, { path: "verification", kind: "change", summary: "moved" }],
      problems: [{ path: "verification.review.code_review", field: "model", message: "Field required", file: "chains/default.yaml", line: 30, col: 9 }],
    })));
    mount();
    const g = await canvas();
    const names = within(g).getAllByRole("button", { pressed: false }).map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["spec, node", "spec_approval, gate", "implementation, node", "verification, node", "local_review, gate", "merge_request_feedback, node"]);
    const verification = within(g).getByRole("button", { name: "verification, node" });
    expect(verification).toHaveTextContent("missing");
    expect(verification.querySelector(".is-prob")).not.toBeNull();
    expect(within(g).getByRole("button", { name: "local_review, gate" }).querySelector(".mark-add")).not.toBeNull();
    // A change inside a node marks the node itself.
    expect(within(g).getByRole("button", { name: "implementation, node" }).querySelector(".mark-change")).not.toBeNull();
    expect(within(g).getByRole("button", { name: "spec, node" }).querySelector(".mark-change, .mark-add")).toBeNull();
    expect(screen.getByText("DRAFT · 3 CHANGES")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1 PROBLEM" })).toBeInTheDocument();
  });

  it("opens the chain's pane on load, and a node's on a click", async () => {
    mount();
    await canvas();
    expect(screen.getByRole("complementary", { name: "default pane" })).toBeInTheDocument();
    expect(screen.getByText("published")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "implementation, node" }));
    expect(screen.getByRole("complementary", { name: "implementation pane" })).toBeInTheDocument();
  });

  it("adds a node from a seam by keyboard: type, then id, then Create & open", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => ok({ ...view(), ops: [] }));
    mount();
    const g = await canvas();
    const seams = within(g).getAllByRole("button", { name: "Add a node or gate here" });
    expect(seams).toHaveLength(7);
    act(() => seams[2].focus());
    await userEvent.keyboard("{Enter}");
    const exec = await screen.findByRole("menuitem", { name: /Exec node/ });
    expect(exec).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /Gate/ })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    const id = await screen.findByRole("textbox", { name: "Gate id" });
    expect(id).toHaveFocus();
    await userEvent.keyboard("security_approval{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][2]).toEqual([{ op: "add_node", at: 2, id: "security_approval", kind: "gate" }]);
    await waitFor(() => expect(screen.getByRole("complementary", { name: "security_approval pane" })).toBeInTheDocument());
  });

  it("refuses a taken id before sending, and shows the server's refusal on the field", async () => {
    const post = vi.spyOn(d, "postOps").mockImplementation(() => Promise.resolve({ status: 422, body: { detail: "'lint-x' is reserved here", op: 0 } }) as never);
    mount();
    const g = await canvas();
    await userEvent.click(within(g).getAllByRole("button", { name: "Add a node or gate here" })[0]);
    await userEvent.click(await screen.findByRole("menuitem", { name: /Exec node/ }));
    await userEvent.keyboard("spec");
    expect(screen.getByRole("alert")).toHaveTextContent("spec is taken.");
    expect(screen.getByRole("button", { name: "Create & open →" })).toBeDisabled();
    await userEvent.keyboard("{Backspace>4}lint-x{Enter}");
    expect(post).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("'lint-x' is reserved here");
    expect(screen.getByRole("textbox", { name: "Node id" })).toHaveAttribute("aria-invalid", "true");
  });

  it("steps through the problems from the header badge", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view({
      problems: [
        { path: "spec_approval", field: "artifact", message: "unknown document", file: "f", line: 1, col: 1 },
        { path: "local_review", field: "reject_to", message: "no node x", file: "f", line: 2, col: 1 },
      ],
    })));
    mount();
    await canvas();
    const badge = screen.getByRole("button", { name: "2 PROBLEMS" });
    await userEvent.click(badge);
    expect(screen.getByRole("complementary", { name: "spec_approval pane" })).toBeInTheDocument();
    await userEvent.click(badge);
    expect(screen.getByRole("complementary", { name: "local_review pane" })).toBeInTheDocument();
    await userEvent.click(badge);
    expect(screen.getByRole("complementary", { name: "spec_approval pane" })).toBeInTheDocument();
  });

  it("goes into the node view on a double-click, and back on Escape", async () => {
    mount();
    const g = await canvas();
    await userEvent.dblClick(within(g).getByRole("button", { name: "verification, node" }));
    expect(where).toBe("/templates/chains/default/nodes/verification");
    expect(screen.getByText("verification", { selector: ".tpl-crumb-node" })).toBeInTheDocument();
  });

  it("undoes with ⌘Z, but not while typing in a field", async () => {
    const undo = vi.spyOn(d, "undo").mockImplementation(() => ok(view()));
    mount();
    await canvas();
    await userEvent.keyboard("{Meta>}z{/Meta}");
    await waitFor(() => expect(undo).toHaveBeenCalledTimes(1));
    const ta = document.createElement("textarea");
    document.body.appendChild(ta);
    ta.focus();
    await userEvent.keyboard("{Meta>}z{/Meta}");
    expect(undo).toHaveBeenCalledTimes(1);
    ta.remove();
  });

  it("reviews on the chain canvas: unchanged nodes fade, changed ones say what, removed ones are ghosts where they stood", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => ok(view({
      changes: [{ path: "implementation.main.implement", kind: "change", summary: "model" }, { path: "plan", kind: "remove", summary: "removed" }],
    })));
    vi.spyOn(api, "getTemplate").mockResolvedValue({ id: "default", file: "chains/default.yaml", text: "id: default\n", chain: { nodes: [{ id: "spec", kind: "exec" }, { id: "spec_approval", kind: "gate" }, { id: "plan", kind: "exec" }, { id: "implementation" }] } });
    mount();
    const g = await canvas();
    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));
    await waitFor(() => expect(within(g).getByRole("button", { name: "plan, node, removed" })).toBeInTheDocument());
    const names = within(g).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(names.slice(0, 4)).toEqual(["spec, node", "spec_approval, gate", "plan, node, removed", "implementation, node"]);
    expect(within(g).getByRole("button", { name: "spec, node" })).toHaveClass("is-faded");
    const impl = within(g).getByRole("button", { name: "implementation, node" });
    expect(impl).not.toHaveClass("is-faded");
    expect(impl).toHaveTextContent("main.implement · model");
    expect(within(g).queryAllByRole("button", { name: "Add a node or gate here" })).toHaveLength(0);
    expect(screen.getByRole("complementary", { name: "Draft · 2 changes pane" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    act(() => within(g).getByRole("button", { name: "spec, node" }).focus());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "Draft · 2 changes pane" })).toBeNull();
  });
});
