import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChainCanvas } from "./ChainCanvas";
import { EXT, EXTENDING_VIEW } from "./draft/fixture.extending";
import { inheritedResult } from "./draft/fixture.inherited";
import type { Result } from "./draft/types";

const SCOPE = { area: "chains", key: "team" } as const;

function mountCanvas(result: Result) {
  const onAdd = vi.fn(async () => true);
  render(
    <ChainCanvas
      scope={SCOPE}
      result={result}
      pending={null}
      reserve={0}
      onSelect={() => {}}
      onOpen={() => {}}
      onFocusNode={() => {}}
      onEscape={() => {}}
      onBackground={() => {}}
      onAdd={onAdd}
    />,
  );
  return { onAdd };
}

const graph = () => screen.getByRole("group", { name: "team" });
const nodeEl = (id: string) => within(graph()).getByRole("button", { name: new RegExp(`^${id}, (node|gate)`) });
const order = () => within(graph()).getAllByRole("button", { name: /^[a-z_0-9-]+, (node|gate)/ }).map((b) => b.getAttribute("aria-label")!.split(",")[0]);
const seams = () => within(graph()).getAllByRole("button", { name: "Add a node or gate here" });

describe("an extending chain", () => {
  it("marks added and overridden nodes and draws removed ones as ghosts", () => {
    mountCanvas(EXTENDING_VIEW.result);
    expect(nodeEl("security_scan")).toHaveTextContent("added here");
    expect(nodeEl(EXT.overridden)).toHaveTextContent("overridden here");
    expect(nodeEl(EXT.first)).not.toHaveTextContent(/here/);
    expect(nodeEl(EXT.removed)).toHaveClass("is-ghost");
    expect(nodeEl(EXT.removed)).toHaveTextContent("removed");
    // The ghost sits right after the node it followed.
    expect(order()).toEqual([EXT.first, EXT.removed, "security_scan", EXT.overridden, ...EXT.rest]);
  });

  it("keeps a run of removed nodes in the parent's order", () => {
    const ext = EXTENDING_VIEW.result.extending!;
    const removed = [...ext.removed, { id: "gone_too", kind: "exec" as const, after: EXT.first }];
    mountCanvas({ ...EXTENDING_VIEW.result, extending: { ...ext, removed } });
    expect(order().slice(0, 3)).toEqual([EXT.first, EXT.removed, "gone_too"]);
  });

  it("offers no seam before the first node, and sends add_node a live index", async () => {
    const { onAdd } = mountCanvas(EXTENDING_VIEW.result);
    // Seven drawn nodes (six live, one ghost): a seam after each, none before the first.
    expect(seams()).toHaveLength(7);
    // The seam between the ghost and security_scan: one live node before it.
    await userEvent.click(seams()[1]);
    await userEvent.click(await screen.findByRole("menuitem", { name: /Gate/ }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Gate id" })).toHaveFocus());
    await userEvent.keyboard("lint{Enter}");
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith(1, "gate", "lint"));
  });

  it("keeps a removed node's id taken", async () => {
    mountCanvas(EXTENDING_VIEW.result);
    await userEvent.click(seams()[0]);
    await userEvent.click(await screen.findByRole("menuitem", { name: /Gate/ }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Gate id" })).toHaveFocus());
    await userEvent.keyboard(EXT.removed);
    expect(screen.getByRole("alert")).toHaveTextContent(`${EXT.removed} is taken.`);
  });
});

describe("a child add-on's chain", () => {
  const ext = EXTENDING_VIEW.result;
  const marked = (marks: Record<string, { origin: "overridden" | "added"; entry: string }> | undefined): Result => ({ ...ext, resolved: { ...ext.resolved!, inherited_nodes: marks } });
  it.each([
    { id: "task-overridden", result: inheritedResult(), node: "implementation", meta: "security_scan overridden here", tone: "amber" },
    { id: "task-added", result: inheritedResult(), node: "verification", meta: "pci_review added here", tone: "green" },
    // The chain-extends origin ranks above the inheritance mark.
    { id: "extends-origin-wins", result: marked({ security_scan: { origin: "overridden", entry: "tasks.security_scan" } }), node: "security_scan", meta: "added here", tone: "green" },
    { id: "problem-wins", result: { ...inheritedResult(), problems: [{ path: "implementation", field: null, message: "field required", file: "chains/default.yaml", line: null, col: null }] }, node: "implementation", meta: "missing", tone: "red" },
    { id: "local-chain", result: inheritedResult({}), node: "implementation", meta: undefined, tone: undefined },
  ])("$id", ({ result, node, meta, tone }) => {
    mountCanvas(result);
    const el = nodeEl(node);
    if (meta) expect(el.querySelector(".graph-meta")?.textContent).toBe(meta);
    else expect(el).not.toHaveTextContent(/here/);
    if (tone) expect(el.querySelector(".graph-meta")).toHaveClass(`tone-${tone}`);
  });
});
