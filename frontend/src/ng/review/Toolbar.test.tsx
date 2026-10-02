import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CompareFile, WorkItem } from "../../types";
import { NO_ATTEMPTS } from "./model";
import { DEFAULT_PREFS } from "./prefs";
import { DiffSettings, NodesFilter, Toolbar } from "./Toolbar";
import type { ReviewPlace } from "./url";

const at = (n: number) => ({ n, sha: `s${n}`, base_sha: "b", at: "t" });
const item = (o: Partial<WorkItem> = {}) => ({ attempts: [at(1), at(2), at(3)], last_review_sha: "h1", chain_definition: { nodes: [{ id: "implementation" }, { id: "local_review" }, { id: "verification" }] }, ...o }) as unknown as WorkItem;
const place: ReviewPlace = { from: "base", to: "latest", nodes: null, file: null, gate: "final_review", doc: false };
const file = (path: string, touched_by: string[]): CompareFile => ({ path, insertions: 1, deletions: 0, touched_by, viewed: false });
const FILES = [file("a.py", ["implementation"]), file("b.py", ["implementation"]), file("c.toml", ["verification"])];

const bar = (o: Partial<WorkItem> = {}, setPlace = vi.fn()) => {
  render(<Toolbar item={item(o)} place={place} setPlace={setPlace} files={FILES} treeOpen onTree={() => {}} onCollapseAll={() => {}} onExpandAll={() => {}} prefs={DEFAULT_PREFS} setPrefs={() => {}} />);
  return setPlace;
};
const options = (name: RegExp) => {
  fireEvent.click(screen.getByRole("button", { name }));
  return screen.getAllByRole("menuitemradio");
};

describe("compare pickers", () => {
  it("offers base, each superseded attempt and the last review; latest, then earlier attempts", () => {
    bar();
    expect(options(/^Compare from base/).map((o) => o.textContent)).toEqual([
      "✓basethe item's starting point",
      "attempt 1first pass, superseded",
      "attempt 2superseded by attempt 3",
      "your last reviewchanges since you started reviewing",
    ]);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(options(/^Compare to latest \(attempt 3\)/).map((o) => o.textContent)).toEqual([
      "✓latest (attempt 3)the current state of the branch",
      "attempt 2superseded by attempt 3",
      "attempt 1superseded by attempt 2",
    ]);
  });

  it("draws both empty cases (R43): no gate pending, no review submitted", () => {
    bar({ attempts: [], last_review_sha: null });
    const from = options(/^Compare from/);
    expect(from.map((o) => [o.textContent, (o as HTMLButtonElement).disabled])).toEqual([
      ["✓basethe item's starting point", false],
      [`attempts${NO_ATTEMPTS}`, true],
      ["your last reviewNo review submitted yet", true],
    ]);
  });

  it("writes the pick to the URL", () => {
    const setPlace = bar();
    fireEvent.click(options(/^Compare from/)[1]);
    expect(setPlace).toHaveBeenCalledWith({ from: "attempt:1" });
  });
});

describe("nodes filter", () => {
  const filter = (nodes: string[] | null, onChange = vi.fn()) => {
    render(<NodesFilter files={FILES} chainOrder={["implementation", "local_review", "verification"]} nodes={nodes} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Nodes:/ }));
    return { onChange, rows: screen.getAllByRole("menuitemcheckbox") };
  };

  it("lists the nodes that touched files, in chain order, with counts; All is ticked", () => {
    const { rows } = filter(null);
    expect(rows.map((r) => [r.textContent, r.getAttribute("aria-checked")])).toEqual([
      ["✓All nodes3 of 3 files", "true"],
      ["✓implementation2 files", "true"],
      ["✓verification1 file", "true"],
    ]);
  });

  it("goes mixed with some off, and writes the node list; All then turns them all back on", () => {
    const { rows, onChange } = filter(["verification"]);
    expect(screen.getByRole("button", { name: "Nodes: 1 of 2 nodes" })).toBeInTheDocument();
    expect(rows[0]).toHaveAttribute("aria-checked", "mixed");
    expect(within(rows[0]).getByText("1 of 3 files")).toBeInTheDocument();
    fireEvent.click(rows[1]);
    expect(onChange).toHaveBeenLastCalledWith(null);
    fireEvent.click(rows[0]);
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("unticks every node from All", () => {
    const { rows, onChange } = filter(null);
    fireEvent.click(rows[0]);
    expect(onChange).toHaveBeenLastCalledWith([]);
    fireEvent.click(rows[2]);
    expect(onChange).toHaveBeenLastCalledWith(["implementation"]);
  });
});

describe("diff settings", () => {
  it("sends each switch as a patch of the diff preferences", () => {
    const set = vi.fn();
    render(<DiffSettings prefs={DEFAULT_PREFS} set={set} />);
    fireEvent.click(screen.getByRole("button", { name: "Diff settings" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Side-by-side/ }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Show whitespace changes/ }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Wrap long lines/ }));
    expect(set.mock.calls).toEqual([[{ layout: "split" }], [{ show_whitespace: false }], [{ wrap_lines: true }]]);
    expect(screen.getByRole("menuitemradio", { name: /Inline/ })).toHaveAttribute("aria-checked", "true");
  });

  it("is a menu the keyboard reaches: Enter opens it on its first item, ↓ moves, Escape comes back", async () => {
    render(<DiffSettings prefs={DEFAULT_PREFS} set={() => {}} />);
    const button = screen.getByRole("button", { name: "Diff settings" });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    button.focus();
    fireEvent.click(button);
    expect(screen.getByRole("menu", { name: "Diff settings" })).toBeInTheDocument();
    const first = screen.getByRole("menuitemradio", { name: /Side-by-side/ });
    await waitFor(() => expect(first).toHaveFocus());
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(screen.getByRole("menuitemradio", { name: /Inline/ })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button).toHaveFocus();
  });
});

describe("nodes filter from the keyboard", () => {
  it("opens on its first box, and ↓ reaches the nodes", async () => {
    render(<NodesFilter files={FILES} chainOrder={["implementation", "local_review", "verification"]} nodes={null} onChange={() => {}} />);
    const button = screen.getByRole("button", { name: /^Nodes:/ });
    button.focus();
    fireEvent.click(button);
    const all = screen.getByRole("menuitemcheckbox", { name: /All nodes/ });
    await waitFor(() => expect(all).toHaveFocus());
    fireEvent.keyDown(all, { key: "ArrowDown" });
    expect(screen.getByRole("menuitemcheckbox", { name: /^implementation/ })).toHaveFocus();
  });
});
