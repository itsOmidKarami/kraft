import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChainNode } from "../../types";
import { detail, SCOPE_PATH, scopeChain, scopeRun, scoped, stubFetch, V1, WORKSPACE } from "./testkit";
import { usePaneMemory, Workspace } from "./Workspace";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));

const nodes: ChainNode[] = V1.map((n) =>
  n.id === "verification"
    ? { ...n, fix_loop: "verification.fix_loop", tasks: ["verification.checks.lint", SCOPE_PATH], steps: [["verification.checks.lint"], [SCOPE_PATH]] }
    : n,
);
const WS = scopeChain("sequential", WORKSPACE);
/** Round 1: ws ran `test-a` and `test-d`, pkg's `test-b` failed, web never started. Round 2: ws ran `test-a` again and `test-c` for the first time; `test-d` is no longer picked, and pkg is not reached. */
const runs = () => [
  scopeRun("ws", "just test-a", 0, "done", { order: 0 }), scopeRun("ws", "just test-d", 0, "done", { order: 1 }), scopeRun("pkg", "just test-b", 0, "failed", { order: 1 }, 110_000),
  scopeRun("ws", "just test-a", 1, "done", { order: 0 }), scopeRun("ws", "just test-c", 1, "done", { order: 2 }),
];
const item = (over = {}) => scoped(runs(), { chain_definition: { template_id: "default", nodes }, materialized_chain: WS, ...over });
function Where() { const l = useLocation(); return <output data-testid="where">{l.pathname + l.search}</output>; }
const mount = (path: string, it = item()) => {
  stubFetch({ "GET /work-items/w1/events": [200, []], "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [] }] });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/work-items/:id/nodes/:node" element={<><Workspace item={it} version="1" reload={() => {}} /><Where /></>} /></Routes>
    </MemoryRouter>,
  );
};
const canvas = () => screen.getByRole("group", { name: "verification" });
const frame = () => screen.queryByRole("group", { name: /repositories and scopes/ });
const where = () => screen.getByTestId("where").textContent;
const pane = (name: string) => screen.getByRole("complementary", { name: `${name} pane` });
const sub = (name: string) => pane(name).querySelector(".pane-sub")!.textContent;
const AT = "/work-items/w1/nodes/verification";
/** The frame goes a moment after it is closed (the animation); waiting for it is the test of it. */
const gone = () => waitFor(() => expect(frame()).toBeNull(), { timeout: 2000 });

describe("an open changed-test-scope task", () => {
  it("opens only while that task is the selection, and draws one row per repository in fan-out order", async () => {
    mount(`${AT}?sel=verification.checks.lint`);
    expect(frame()).toBeNull();
    await userEvent.click(within(canvas()).getByRole("button", { name: /^test_changed_scopes/ }));
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(within(f).getByText("round 2 · repos in order · scopes in order")).toBeInTheDocument();
    // Round 2 is the newest: ws ran, pkg and web were not reached... no failure in it, so the item is stopped: not reached.
    expect([...f.querySelectorAll(".scope-row")].map((r) => [r.querySelector(".scope-name")!.textContent, r.querySelector(".scope-note")!.textContent])).toEqual([
      ["ws", "done · 48s"], ["pkg", "not reached"], ["web", "not reached"],
    ]);
  });

  it("draws an item of one repository as its chips alone: no ring, no name, no note, and no word about repos", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`, scoped([scopeRun(null, "just test-a", 0, "running")], { chain_definition: { template_id: "default", nodes } }));
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(within(f).getByText("round 1 · scopes in order")).toBeInTheDocument();
    expect(f.querySelectorAll(".scope-row.is-solo .scope-chip")).toHaveLength(1);
    expect(f.querySelector(".scope-ring, .scope-rail, .scope-name, .scope-note")).toBeNull();
  });

  it("says why an item of one repository has no chips, where there is nothing else in its row", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`, scoped([], { chain_definition: { template_id: "default", nodes } }));
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(f.querySelector(".scope-row.is-solo")!.textContent).toBe("not reached");
  });

  it("shows a repository after the first failure as not reached, with no chips", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`);
    await userEvent.click(await screen.findByRole("button", { name: /round 2 of 3/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^Round 1/ }));
    const rows = [...(await screen.findByRole("group", { name: /repositories and scopes/ })).querySelectorAll(".scope-row")];
    expect(rows.map((r) => [r.querySelector(".scope-name")!.textContent, r.querySelector(".scope-note")!.textContent, r.querySelectorAll(".scope-chip").length])).toEqual([
      ["ws", "done · 48s", 2], ["pkg", "failed · 1m", 1], ["web", "not reached · pkg failed", 0],
    ]);
  });

  it("tags what the round before did not run, keeps what it no longer picks, and a chip opens the scope's pane", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`);
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    // In the table's order: a, then d (dropped, dashed), then c (new). pkg ran nothing this round, so it has no chips at all.
    expect(within(f).getAllByRole("button").map((b) => b.getAttribute("aria-label")).filter(Boolean)).toEqual(["just test-a, done", "just test-d, not picked", "just test-c, done, new this round"]);
    expect(within(f).getByText("new")).toHaveAttribute("title", "Picked for the first time this round");
    expect(within(f).getByRole("button", { name: "just test-d, not picked" })).toHaveAttribute("title", "just test-d\ncovers d/**\nRan last round; no changed path reaches it this round");
    await userEvent.click(within(f).getByRole("button", { name: "just test-c, done, new this round" }));
    expect(where()).toBe(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-c" })}`);
    expect(sub("just test-c")).toBe("test scope · round 2 of 3 · passed 24s");
    const facts = Object.fromEntries([...pane("just test-c").querySelectorAll("dl > div")].map((d) => [d.querySelector("dt")!.textContent, d.querySelector("dd")!.textContent]));
    expect(facts).toMatchObject({ status: "passed · 24s", command: "just test-c", paths: "c/**", repo: "ws", task: "test_changed_scopes", execution: "sequential" });
    // The paths follow the command, as a row of their own.
    expect([...pane("just test-c").querySelectorAll("dl > div dt")].map((d) => d.textContent).slice(0, 3)).toEqual(["status", "command", "paths"]);
    // Round 1 ran its repository without it; round 3 does not exist yet.
    expect(facts["other rounds"]).toBe("1: not picked");
  });

  it("says on the open task's own Overview how its round went, and what its chips and rings mean", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    const overview = pane("test_changed_scopes");
    const facts = Object.fromEntries([...overview.querySelectorAll("dl > div")].map((d) => [d.querySelector("dt")!.textContent, d.querySelector("dd")!.textContent]));
    expect(facts).toMatchObject({
      "this round": "2 of 2 scopes passed",
      repos: "1 of 3 reached · run in order, stop at the first failure",
      scopes: "one after another within a repo, all run",
      config: "execution: sequential",
    });
    const legend = overview.querySelector(".scope-legend")!;
    expect([...legend.children].map((l) => l.textContent)).toEqual([
      "newpicked for the first time this round: the changed paths now reach it",
      "not pickedran in the previous round, but no changed path reaches it this round",
      "repo not reached: repos run in order and stop at the first one that fails",
    ]);
  });

  it("neither counts nor names the repository of an item that has one", async () => {
    const one = scoped([scopeRun(null, "just test-a", 0, "done", { order: 0 }), scopeRun(null, "just test-a", 1, "done", { order: 0 }), scopeRun(null, "just test-c", 1, "done", { order: 2 })], { chain_definition: { template_id: "default", nodes } });
    mount(`${AT}?sel=${SCOPE_PATH}`, one);
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    const overview = pane("test_changed_scopes");
    expect([...overview.querySelector("dl")!.querySelectorAll(":scope > div > dt")].map((d) => d.textContent)).toEqual(["this round", "scopes", "config"]);
    // "Not reached" is what happens to a repository after one that failed: there is no such repository here.
    expect([...overview.querySelector(".scope-legend")!.children].map((l) => l.textContent?.slice(0, 10))).toEqual(["newpicked ", "not picked"]);
    await userEvent.click(within(f).getByRole("button", { name: "just test-c, done, new this round" }));
    const scope = pane("just test-c");
    expect([...scope.querySelectorAll("dl > div dt")].map((d) => d.textContent)).toEqual(["status", "command", "paths", "task", "execution", "other rounds"]);
    expect(scope.querySelector(".pane-crumbs, nav")!.textContent).not.toContain("kraft-web");
  });

  it.each([
    ["ws:just test-gone", "just test-gone", "test scope · round 2 of 3 · not found", "Not found: this task ran no scope with this command."],
    // pkg ran `test-b` in round 1 and was not reached in round 2.
    ["pkg:just test-b", "just test-b", "test scope · round 2 of 3 · not reached", "Its repository was not reached this round."],
  ])("says why a scope the round has no chip for has none: %s", async (key, title, want, body) => {
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: key })}`);
    expect(sub(title)).toBe(want);
    expect(within(pane(title)).getByText(body)).toBeInTheDocument();
  });

  it("offers the runs of a scope the round ran more than once, and reads the one picked", async () => {
    const [first, again, other] = [scopeRun("ws", "just test-a", 1, "failed", { order: 0 }, 9000), scopeRun("ws", "just test-a", 1, "done", { order: 0 }), scopeRun("ws", "just test-c", 1, "done", { order: 2 })];
    Object.assign(first[1], { attempt: 2, created_at: "2026-09-13T10:08:00Z" });
    Object.assign(again[1], { attempt: 3 });
    const it = scoped([scopeRun("ws", "just test-a", 0, "done", { order: 0 }), again, other], { chain_definition: { template_id: "default", nodes }, materialized_chain: WS });
    it.worker_sessions.push(first[1]);
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-a" })}`, it);
    const scope = () => pane("just test-a");
    const pill = within(scope()).getByRole("button", { name: "attempt 2 of 2" });
    expect(pill.closest(".pane-sub")).toHaveTextContent(/^test scope · round 2 of 3 · attempt 2 of 2▾ · passed 24s$/);
    await userEvent.click(pill);
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^Attempt 1/ }));
    expect(where()).toContain("attempt=2");
    expect(scope().querySelector(".pane-sub")).toHaveTextContent(/^test scope · round 2 of 3 · attempt 1 of 2▾ · failed 9s$/);
    expect(within(scope()).getByText("status").nextElementSibling).toHaveTextContent("failed · 9s");
    // The run picked is this scope's. Back on the task, the pane is the task's again: it has no menu to let a run go.
    await userEvent.click(within(scope()).getAllByRole("button", { name: "test_changed_scopes" })[0]);
    expect(sub("test_changed_scopes")).toMatch(/· done 24s$/);
    // A scope the round ran once has no runs to pick between, and the run picked is not another scope's.
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    await userEvent.click(within(f).getByRole("button", { name: /^just test-c/ }));
    expect(where()).not.toContain("attempt=");
    expect(within(pane("just test-c")).queryByRole("button", { name: /^attempt/ })).toBeNull();
  });

  it("reads a task with a failed scope as failed, though a later scope passed", async () => {
    const it = scoped([scopeRun("ws", "just test-a", 0, "failed", { order: 0 }, 9000), scopeRun("ws", "just test-c", 0, "done", { order: 2 })], { chain_definition: { template_id: "default", nodes }, materialized_chain: WS });
    mount(`${AT}?sel=${SCOPE_PATH}`, it);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(sub("test_changed_scopes")).toMatch(/· failed$/);
  });

  it("says a parallel task's scopes run together within a repo", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`, item({ materialized_chain: scopeChain("parallel", WORKSPACE) }));
    await screen.findByRole("group", { name: /repositories and scopes/ });
    const dds = [...pane("test_changed_scopes").querySelectorAll("dl > div")].map((d) => d.textContent);
    expect(dds).toContain("scopesin parallel within a repo");
    expect(dds).toContain("configexecution: parallel");
  });

  it("counts a changed-test-scope task's sessions as its scopes, not as attempts: no pill in its pane, no ×N on its box", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(within(pane("test_changed_scopes")).queryByRole("button", { name: /^attempt/ })).toBeNull();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(frame()).toBeNull(), { timeout: 2000 });
    expect(within(canvas()).getByRole("button", { name: /^test_changed_scopes/ })).not.toHaveAccessibleName(/attempt/);
  });

  it("reads a running scope's status with one dot before its time, and a failed one's the same", async () => {
    const running = scoped([scopeRun("ws", "just test-a", 0, "running", { order: 0 }, null), scopeRun("ws", "just test-b", 0, "failed", { order: 1 })], { chain_definition: { template_id: "default", nodes }, materialized_chain: WS, display_status: "running" });
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-a" })}`, running);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(pane("just test-a").querySelector("dl > div dd")!.textContent).toMatch(/^running · \d[^·]*$/);
    await userEvent.click(within(frame()!).getByRole("button", { name: "just test-b, failed" }));
    expect(pane("just test-b").querySelector("dl > div dd")!.textContent).toBe("failed · 24s");
  });

  it("goes Esc from a scope to its task, from the task to its box, and then to the pane", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-a" })}`);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(sub("just test-a")).toContain("test scope");
    await userEvent.keyboard("{Escape}");
    expect(where()).toBe(`${AT}?sel=${SCOPE_PATH}`);
    expect(frame()).toBeInTheDocument();
    expect(sub("test_changed_scopes")).toContain("task");
    await userEvent.keyboard("{Escape}");
    await gone();
    expect(where()).toBe(`${AT}?sel=${SCOPE_PATH}`);
    expect(pane("test_changed_scopes")).toBeInTheDocument();
    // The task is its box again, and picking it opens the frame again.
    await userEvent.click(within(canvas()).getByRole("button", { name: /^test_changed_scopes/ }));
    expect(await screen.findByRole("group", { name: /repositories and scopes/ })).toBeInTheDocument();
  });

  it("keeps the keyboard focus on the task through its frame opening, and back on its box when the frame is gone", async () => {
    mount(AT + "?sel=verification.checks.lint");
    const box = within(canvas()).getByRole("button", { name: /^test_changed_scopes/ });
    box.focus();
    await userEvent.keyboard("{Enter}");
    const title = await screen.findByRole("button", { name: "test_changed_scopes" });
    await waitFor(() => expect(title).toHaveFocus());
    await userEvent.keyboard("{Escape}");
    await gone();
    expect(within(canvas()).getByRole("button", { name: /^test_changed_scopes/ })).toHaveFocus();
  });

  it("takes two Escapes that come before the page has rendered the first: the scope, then the task's frame", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-a" })}`);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    const esc = () => document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    // One act: nothing renders between the two, as when the router moves in a transition.
    act(() => { esc(); esc(); });
    await gone();
    expect(where()).toBe(`${AT}?sel=${SCOPE_PATH}`);
  });

  it("closes on its close button, on picking anything else, and on the canvas's background", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`);
    await userEvent.click(within(await screen.findByRole("group", { name: /repositories and scopes/ })).getByRole("button", { name: /close/ }));
    await gone();
    await userEvent.click(within(canvas()).getByRole("button", { name: /^test_changed_scopes/ }));
    await screen.findByRole("group", { name: /repositories and scopes/ });
    await userEvent.click(within(canvas()).getByRole("button", { name: /^lint/ }));
    await gone();
    await userEvent.click(within(canvas()).getByRole("button", { name: /^test_changed_scopes/ }));
    await screen.findByRole("group", { name: /repositories and scopes/ });
    await userEvent.click(canvas());
    await gone();
  });

  it("steps back from a scope to the task on a click on the frame's own background", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-a" })}`);
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    await userEvent.click(f.querySelector(".scope-body")!);
    expect(where()).toBe(`${AT}?sel=${SCOPE_PATH}`);
    expect(frame()).toBeInTheDocument();
  });

  it("lists a scope's other rounds in its pane, and the round it was dropped in", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}&${new URLSearchParams({ scope: "ws:just test-a" })}`);
    await screen.findByRole("group", { name: /repositories and scopes/ });
    const facts = Object.fromEntries([...pane("just test-a").querySelectorAll("dl > div")].map((d) => [d.querySelector("dt")!.textContent, d.querySelector("dd")!.textContent]));
    expect(facts["other rounds"]).toBe("1: passed");
  });

  it("stacks a parallel task's scopes in a fork instead of a line", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`, item({ materialized_chain: scopeChain("parallel", WORKSPACE) }));
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(within(f).getByText("round 2 · repos in order · scopes in parallel")).toBeInTheDocument();
    expect(f.querySelector(".scope-chips.is-fork")).not.toBeNull();
    expect(f.querySelector(".scope-arrow")).toBeNull();
  });

  it("is a line of chips joined by arrows when its scopes run in order", async () => {
    mount(`${AT}?sel=${SCOPE_PATH}`);
    const f = await screen.findByRole("group", { name: /repositories and scopes/ });
    expect(f.querySelectorAll(".scope-arrow")).toHaveLength(2);
    expect(f.querySelector(".scope-chips.is-fork")).toBeNull();
  });

  it("does not open for a task that is not the changed-test-scope builtin", async () => {
    mount(`${AT}?sel=verification.checks.lint`, detail({ ...item(), materialized_chain: scopeChain() }));
    expect(frame()).toBeNull();
  });
});
