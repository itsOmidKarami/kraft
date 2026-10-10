import { describe, expect, it } from "vitest";
import { frameWidth, isScopeTask, otherRounds, reposOf, scopeAt, scopeLead, scopeRuns, scopesView, type Chip, type ScopesView } from "./scopeView";
import { asOfPass } from "./nodeGraph";
import { detail, FROZEN, pendingRun, SCOPE_PATH, scopeChain, scopeRun, scoped, WORKSPACE } from "./testkit";

const NOW = Date.parse("2026-09-13T10:10:00Z");
const PATH = SCOPE_PATH;
const chain = scopeChain;
const run = scopeRun;
const item = scoped;
const names = (v: ReturnType<typeof scopesView>, i = 0) => v.rows[i].chips.map((c) => [c.name, c.state, c.meta, c.fresh ?? false]);

describe("scopesView", () => {
  it("names a single repository's one row for it, and knows the task by its builtin", () => {
    const it = item([run(null, "just test-api", 0, "done", { order: 0 })]);
    expect(reposOf(it)).toEqual([{ id: null, name: "kraft-web" }]);
    expect(isScopeTask(it, PATH)).toBe(true);
    expect(isScopeTask(it, "verification.tests.other")).toBe(false);
    expect(isScopeTask(detail({ materialized_chain: FROZEN }), "verification.checks.lint")).toBe(false);
    expect(scopesView(it, PATH, 1, NOW).rows.map((r) => [r.id, r.name, r.state, r.note])).toEqual([[null, "kraft-web", "done", "done · 24s"]]);
  });

  it("keeps a workspace of only a root as its one row, because its runs name the root", () => {
    const it = item([run("ws", "just test-api", 0, "done", { order: 0 })], { materialized_chain: chain("sequential", { kind: "workspace", root: "ws" }) });
    expect(scopesView(it, PATH, 1, NOW).rows.map((r) => [r.id, r.name, r.state, r.chips.length])).toEqual([["ws", "ws", "done", 1]]);
  });

  it("reads a re-measure after on_failure (round -1) as the round it followed, and lists a workspace's repositories before anything has run", () => {
    const first = run(null, "just test-a", 0, "failed", { order: 0 });
    const again = run(null, "just test-a", -1, "done", { order: 0 });
    again[1].created_at = "2026-09-13T10:09:30Z";
    expect(names(scopesView(item([first, again]), PATH, 1, NOW))).toEqual([["just test-a", "done", "24s", false]]);
    expect(scopesView(item([], { materialized_chain: chain("sequential", WORKSPACE) }), PATH, 1, NOW).rows.map((r) => r.id)).toEqual(["ws", "pkg", "web"]);
  });

  it("lists repositories in fan-out order, root first, and a repository after a failure is not reached", () => {
    const it = item([run("ws", "just test-api", 0, "done", { order: 0 }), run("pkg", "just test-pkg", 0, "failed", { order: 0 })], { materialized_chain: chain("sequential", WORKSPACE) });
    const v = scopesView(it, PATH, 1, NOW);
    expect(v.rows.map((r) => [r.id, r.state, r.note, r.chips.length])).toEqual([
      ["ws", "done", "done · 24s", 1],
      ["pkg", "failed", "failed · 24s", 1],
      ["web", "unreached", "not reached · pkg failed", 0],
    ]);
  });

  it("reads a repository nobody has started as waiting while the round runs, else not reached", () => {
    const frozen = chain("sequential", WORKSPACE);
    const running = item([run("ws", "just test-api", 0, "running", {}, null)], { materialized_chain: frozen, display_status: "running" });
    expect(scopesView(running, PATH, 1, NOW).rows.map((r) => [r.state, r.note])).toEqual([["running", "running"], ["waiting", "waiting"], ["waiting", "waiting"]]);
    expect(names(scopesView(running, PATH, 1, NOW))).toEqual([["just test-api", "running", "running · 1m", false]]);
    const stopped = item([run("ws", "just test-api", 0, "done")], { materialized_chain: frozen });
    expect(scopesView(stopped, PATH, 1, NOW).rows.map((r) => r.state)).toEqual(["done", "unreached", "unreached"]);
  });

  it("follows the task's execution: sequential unless it says parallel, and a parallel repo took its longest scope", () => {
    const runs = [run(null, "just test-a", 0, "done", { order: 0 }, 10_000), run(null, "just test-b", 0, "done", { order: 1 }, 40_000)];
    expect(scopesView(item(runs, { materialized_chain: chain() }), PATH, 1, NOW)).toMatchObject({ execution: "sequential", rows: [{ note: "done · 50s" }] });
    expect(scopesView(item(runs, { materialized_chain: chain("parallel") }), PATH, 1, NOW)).toMatchObject({ execution: "parallel", rows: [{ note: "done · 40s" }] });
  });

  it("tags a scope new when the round before did not run it, and keeps one it no longer picks as 'not picked'", () => {
    const it = item([
      run(null, "just test-a", 0, "done", { order: 0 }), run(null, "just test-c", 0, "done", { order: 2 }),
      run(null, "just test-a", 1, "failed", { order: 0 }, 110_000), run(null, "just test-b", 1, "done", { order: 1 }),
      run(null, "just test-a", 2, "done", { order: 0 }),
    ]);
    // Round 2 against round 1: b is new, c is no longer picked; the table's order holds.
    expect(names(scopesView(it, PATH, 2, NOW))).toEqual([["just test-a", "failed", "failed · 1m", false], ["just test-b", "done", "24s", true], ["just test-c", "skipped", "not picked", false]]);
    // Round 3 against round 2 alone: a stays, b is no longer picked, and c (picked in round 1) is not mentioned.
    expect(names(scopesView(it, PATH, 3, NOW))).toEqual([["just test-a", "done", "24s", false], ["just test-b", "skipped", "not picked", false]]);
    // Round 1 has nothing before it to be new against.
    expect(names(scopesView(it, PATH, 1, NOW)).map((c) => c[3])).toEqual([false, false]);
  });

  it("calls nothing new in a repository the round before did not reach, and drops nothing into one that ran none", () => {
    const it = item([run("ws", "just test-a", 0, "failed"), run("ws", "just test-a", 1, "done"), run("pkg", "just test-b", 1, "done"), run("ws", "just test-a", 2, "done")], { materialized_chain: chain("sequential", WORKSPACE) });
    // pkg's first scope ever is not a scope picked anew; the repo the round before never reached had nothing to compare to.
    expect(names(scopesView(it, PATH, 2, NOW), 1)).toEqual([["just test-b", "done", "24s", false]]);
    // In round 3 pkg ran nothing: no chips, though it ran b in round 2.
    expect(scopesView(it, PATH, 3, NOW).rows.map((r) => [r.name, r.chips.length])).toEqual([["ws", 1], ["pkg", 0], ["web", 0]]);
  });

  it("reads a chip as its command, trimmed to 28 characters, a setup as `setup · <area>`, with the whole command and the paths for the tooltip", () => {
    const long = "pnpm test:e2e --filter search --reporter=dot";
    const it = item([run(null, long, 0, "done", { order: 0, scope: "web/**" }), run(null, "npm ci", 0, "done", { order: -0.5, area: "web", setup: true, scope: undefined })]);
    const [setup, scope] = scopesView(it, PATH, 1, NOW).rows[0].chips;
    expect([setup.name, scope.name]).toEqual(["setup · web", "pnpm test:e2e --filter sear…"]);
    expect(scope.name).toHaveLength(28);
    expect([scope.command, scope.paths]).toEqual([long, "web/**"]);
  });

  it("does not call a scope 'not picked' while its round is still choosing", () => {
    const it = item([run(null, "just test-a", 0, "done", { order: 0 }), run(null, "just test-b", 0, "done", { order: 1 }), run(null, "just test-a", 1, "running", { order: 0 }, null)], { display_status: "running" });
    expect(names(scopesView(it, PATH, 2, NOW)).map((c) => c[1])).toEqual(["running"]);
  });

  it("draws what a round picked and has not started as waiting chips, the repository still running, and one that has started none as waiting", () => {
    const frozen = chain("sequential", WORKSPACE);
    const it = item([
      run("ws", "just test-a", 0, "done", { order: 0 }), run("ws", "just test-b", 0, "running", { order: 1 }, null), pendingRun("ws", "just test-c", 0, { order: 2 }),
      pendingRun("pkg", "just test-p", 0, { order: 0 }),
    ], { materialized_chain: frozen, display_status: "running" });
    const v = scopesView(it, PATH, 1, NOW);
    expect(names(v)).toEqual([["just test-a", "done", "24s", false], ["just test-b", "running", "running · 1m", false], ["just test-c", "waiting", "waiting", false]]);
    expect(v.rows.map((r) => [r.state, r.note])).toEqual([["running", "running"], ["waiting", "waiting"], ["waiting", "waiting"]]);
    expect(v.rows[1].chips.map((c) => [c.name, c.state, c.session ?? null])).toEqual([["just test-p", "waiting", null]]);
  });

  it("knows what a round dropped as soon as it has recorded its picks, not only when it ends", () => {
    const base = [run(null, "just test-a", 0, "done", { order: 0 }), run(null, "just test-d", 0, "done", { order: 1 }), run(null, "just test-a", 1, "running", { order: 0 }, null)];
    // Without a record of the picks the round may still be choosing: nothing is called dropped yet.
    expect(names(scopesView(item(base, { display_status: "running" }), PATH, 2, NOW)).map((c) => c[1])).toEqual(["running"]);
    // With it, d is plainly not picked.
    const picked = base.map(([r, x]) => [r.round === 1 ? { ...r, selected: true as const } : r, x] as (typeof base)[number]);
    expect(names(scopesView(item(picked, { display_status: "running" }), PATH, 2, NOW)).map((c) => c[1])).toEqual(["running", "skipped"]);
  });

  it("draws an area's setup as a chip ahead of the area's first scope", () => {
    const it = item([run(null, "just test-web", 0, "done", { order: 3, area: "web" }), run(null, "npm ci", 0, "done", { order: 2.5, area: "web", setup: true, scope: undefined })]);
    expect(names(scopesView(it, PATH, 1, NOW)).map((c) => c[0])).toEqual(["setup · web", "just test-web"]);
  });
});

describe("a scope task in a node the chain ran again", () => {
  // Each pass measured round 1, and picked differently: the pass before is not this one's round.
  const both = () => {
    const runs = [run(null, "just test-a", 0, "failed", { pass: 1 }), run(null, "just test-b", 1, "done", { pass: 1 }), run(null, "just test-c", 0, "done", { pass: 2 })];
    const passes = [1, 1, 2];
    return item(runs.map(([r, s], i) => [r, { ...s, pass: passes[i] }]), { node_passes: { verification: [{ pass: 1 }, { pass: 2, reason: "retry" }] } });
  };

  it("draws the scopes of the pass the node is on, and of an earlier one when it is the one shown", () => {
    expect(names(scopesView(both(), PATH, 1, NOW)).map((c) => c[0])).toEqual(["just test-c"]);
    const first = asOfPass(both(), "verification", 1);
    expect(names(scopesView(first, PATH, 1, NOW)).map((c) => c[0])).toEqual(["just test-a"]);
    expect(names(scopesView(first, PATH, 2, NOW)).map((c) => [c[0], c[1]])).toEqual([["just test-b", "done"], ["just test-a", "skipped"]]);
  });

  it("says how a scope went in the other rounds of its own pass alone", () => {
    expect(otherRounds(both(), PATH, ":just test-c", 1)).toEqual([]);
    expect(otherRounds(asOfPass(both(), "verification", 1), PATH, ":just test-a", 1)).toEqual(["2: not picked"]);
    // A pass that resumed at round 2 has no round 1 to speak of.
    const resumed = item([run(null, "just test-a", 1, "done"), run(null, "just test-a", 2, "done")]);
    expect(otherRounds(resumed, PATH, ":just test-a", 3, 2)).toEqual(["2: passed"]);
  });
});

describe("a scope the round has no chip for", () => {
  const miss = (it: ReturnType<typeof item>, key: string, round: number) => {
    const { miss, why, command } = scopeAt(it, PATH, key, scopesView(it, PATH, round, NOW));
    return [miss, why, command];
  };
  const three = () => item([run(null, "just test-a", 0, "done"), run(null, "just test-b", 0, "done"), run(null, "just test-a", 1, "done"), run(null, "just test-a", 2, "done")]);

  it.each([
    // Round 3 against round 2 alone: b ran in round 1, and neither since.
    ["a scope another round ran", () => three(), ":just test-b", 3, "not picked", "Not picked: no changed path reaches it this round.", "just test-b"],
    ["a command no round ran", () => three(), ":just test-gone", 3, "not found", "Not found: this task ran no scope with this command.", "just test-gone"],
    ["a key with no repository part", () => three(), "just test-a", 3, "not found", "Not found: this task ran no scope with this command.", "just test-a"],
    // Round 2 runs on and has recorded no picks: what it dropped is not known yet.
    ["a round still choosing", () => item([run(null, "just test-a", 0, "done"), run(null, "just test-b", 0, "done"), run(null, "just test-a", 1, "running")]), ":just test-b", 2, "still choosing", "Still choosing: this round has not picked its scopes yet.", "just test-b"],
    ["a repository the round did not reach", () => item([run("ws", "just test-a", 0, "done"), run("pkg", "just test-p", 0, "done"), run("ws", "just test-a", 1, "failed")], { materialized_chain: chain("sequential", WORKSPACE) }), "pkg:just test-p", 2, "not reached", "Its repository was not reached this round.", "just test-p"],
  ])("says why: %s", (_n, of, key, round, want, why, command) => {
    expect(miss(of(), key, round)).toEqual([want, why, command]);
  });

  it("finds the chip and its row when the round has one, a dropped one included", () => {
    const it = three();
    expect(scopeAt(it, PATH, ":just test-a", scopesView(it, PATH, 2, NOW))).toMatchObject({ chip: { state: "done" }, row: { id: null }, command: "just test-a" });
    expect(scopeAt(it, PATH, ":just test-b", scopesView(it, PATH, 2, NOW)).chip).toMatchObject({ state: "skipped" });
  });
});

describe("a changed-test-scope task as one thing", () => {
  it.each([
    ["a scope that failed speaks for it, though a later one passed", [run(null, "just test-a", 0, "failed"), run(null, "just test-b", 0, "done")], "just test-a"],
    ["else a scope still running", [run(null, "just test-a", 0, "running"), run(null, "just test-b", 0, "done")], "just test-a"],
    ["a failure before one still running", [run(null, "just test-a", 0, "running"), run(null, "just test-b", 0, "failed")], "just test-b"],
    ["every scope passed: its last run, as any task", [run(null, "just test-a", 0, "done"), run(null, "just test-b", 0, "done")], undefined],
  ] as const)("%s", (_n, runs, want) => {
    expect(scopeLead(scopesView(item([...runs]), PATH, 1, NOW))?.command).toBe(want);
  });

  it("counts the latest run of each scope: a scope that failed and then passed in the round did not fail", () => {
    const [first, again] = [run(null, "just test-a", 0, "failed"), run(null, "just test-a", 0, "done")];
    Object.assign(again[1], { attempt: 2, created_at: "2026-09-13T10:11:00Z" });
    const it = item([again], { worker_sessions: [first[1], again[1]] });
    expect(scopeLead(scopesView(it, PATH, 1, NOW))).toBeUndefined();
    // Both are runs of the scope in the round, oldest first; another round's are not.
    expect(scopeRuns(it, PATH, ":just test-a", 1).map((s) => s.status)).toEqual(["failed", "done"]);
    expect(scopeRuns(it, PATH, ":just test-a", 2)).toEqual([]);
    expect(scopeRuns(it, PATH, ":just test-b", 1)).toEqual([]);
  });
});

describe("frameWidth", () => {
  const chip = (name: string, meta: string, state: Chip["state"] = "done"): Chip => ({ key: name, name, command: name, state, meta });
  const row = (chips: Chip[]): ScopesView["rows"][number] => ({ id: null, name: "r", state: "running", note: "", chips });
  const of = (chips: Chip[], execution: ScopesView["execution"] = "sequential", repos = 2): ScopesView => ({ path: "verification.tests.test_changed_scopes", round: 1, execution, rows: [row(chips), ...(repos > 1 ? [row([])] : [])] });
  const long = [chip("pnpm --dir docsite '&&' npm…", "0s"), chip("just test-vscode", "2s"), chip("just ci-test", "running · 5s", "running")];

  it.each([
    ["a short row is as wide as the header", of([chip("a/**", "1s")]), (w: number) => w === 480],
    ["a row of chips longer than that widens it", of(long), (w: number) => w > 480],
    ["forked, only the widest chip counts", of(long, "parallel"), (w: number) => w === frameWidth(of([long[0]], "parallel")) && w < frameWidth(of(long))],
    ["one repository's header says less, so a short row is narrower still", of([chip("a/**", "1s")], "sequential", 1), (w: number) => w === 400],
    ["one repository's long row is its chips, without the repository column", of(long, "sequential", 1), (w: number) => w > 400 && w < frameWidth(of(long))],
  ])("%s", (_, view, ok) => expect(ok(frameWidth(view))).toBe(true));

  it("does not widen as a waiting chip starts, nor as a running chip's clock ticks", () => {
    const at = (meta: string, state: Chip["state"] = "running") => frameWidth(of([...long.slice(0, 2), chip("just ci-test", meta, state)]));
    expect(at("waiting", "waiting")).toBe(at("running · 5s"));
    expect(at("running · 5s")).toBe(at("running · 59m 59s"));
  });
});

describe("otherRounds", () => {
  it("says how a scope went in every other round: where it ran, 'not picked' where its repository ran without it, 'repo not reached' where it did not", () => {
    const it = item([run("ws", "just test-a", 0, "done"), run("ws", "just test-a", 1, "failed"), run("ws", "just test-b", 2, "done"), run("pkg", "just test-p", 0, "done")], { materialized_chain: chain("sequential", WORKSPACE) });
    expect(otherRounds(it, PATH, "ws:just test-a", 2)).toEqual(["1: passed", "3: not picked"]);
    expect(otherRounds(it, PATH, "ws:just test-a", 1)).toEqual(["2: failed", "3: not picked"]);
    expect(otherRounds(it, PATH, "pkg:just test-p", 1)).toEqual(["2: repo not reached", "3: repo not reached"]);
    // A command its round picked and has not started waits.
    expect(otherRounds(scoped([pendingRun("ws", "just test-a", 3)]), PATH, "ws:just test-a", 1)).toEqual(["2: repo not reached", "3: repo not reached", "4: waiting"]);
  });
});
