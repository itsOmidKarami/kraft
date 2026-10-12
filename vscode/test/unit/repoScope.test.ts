import { describe, expect, it } from "vitest";
import { ALL_REPOS_KEY, folderScope, REFETCH_MS, refetchRepos, rememberedScope, repoRoot, samePath, worktreeItemId, WorkspaceScope, type ConnectedRepo, type ScopeDeps } from "../../src/core/repoScope";

const TOP = "rev-parse --show-toplevel";
const COMMON = "rev-parse --path-format=absolute --git-common-dir";

/** A fake `git` keyed `cwd|args`, a fake `realpath` from `links` (identity
 *  otherwise; a path in `gone` rejects). */
function deps(git: Record<string, string>, links: Record<string, string> = {}, platform: NodeJS.Platform = "linux", gone: string[] = []): ScopeDeps {
  return {
    git: async (args, cwd) => git[`${cwd}|${args.join(" ")}`],
    realpath: async (p) => {
      if (gone.includes(p)) throw new Error(`ENOENT: ${p}`);
      return links[p] ?? p;
    },
    platform,
  };
}

describe("repoRoot", () => {
  it.each([
    ["plain checkout", "/r", { [`/r|${TOP}`]: "/r", [`/r|${COMMON}`]: "/r/.git" }, {}, "/r"],
    ["subfolder", "/r/src", { [`/r/src|${TOP}`]: "/r", [`/r/src|${COMMON}`]: "/r/.git" }, {}, "/r"],
    ["symlink", "/link", { [`/real/r|${TOP}`]: "/real/r", [`/real/r|${COMMON}`]: "/real/r/.git" }, { "/link": "/real/r" }, "/real/r"],
    ["linked worktree", "/wt", { [`/wt|${TOP}`]: "/wt", [`/wt|${COMMON}`]: "/r/.git" }, {}, "/r"],
    ["submodule", "/s/sub", { [`/s/sub|${TOP}`]: "/s/sub", [`/s/sub|${COMMON}`]: "/s/.git/modules/sub" }, {}, "/s/sub"],
    ["non-repo", "/tmp/x", {}, {}, "/tmp/x"],
    // git < 2.31 echoes the flag it does not know and prints the relative common dir, exit 0.
    ["old git", "/r/src", { [`/r/src|${TOP}`]: "/r", [`/r/src|${COMMON}`]: "--path-format=absolute\n../.git" }, {}, "/r"],
    ["more than one line", "/wt", { [`/wt|${TOP}`]: "/wt", [`/wt|${COMMON}`]: "/a\n/r/.git" }, {}, "/wt"],
    ["relative common dir", "/wt", { [`/wt|${TOP}`]: "/wt", [`/wt|${COMMON}`]: "../r/.git" }, {}, "/wt"],
    ["windows common dir", "C:/wt", { [`C:/wt|${TOP}`]: "C:/wt", [`C:/wt|${COMMON}`]: "C:/r/.git" }, {}, "C:/r"],
  ])("%s", async (_, folder, git, links, want) => {
    expect(await repoRoot(folder, deps(git, links))).toBe(want);
  });

  it("falls back to the folder when realpath throws", async () => {
    const git = { [`/gone|${TOP}`]: "/gone", [`/gone|${COMMON}`]: "/gone/.git" };
    expect(await repoRoot("/gone", deps(git, {}, "linux", ["/gone"]))).toBe("/gone");
  });
});

describe("samePath", () => {
  it.each([
    ["C:/Users/x/repo", "c:\\Users\\x\\repo\\", "win32", true],
    ["/R", "/r", "linux", false],
    ["/r/", "/r", "linux", true],
    ["/r", "/r2", "darwin", false],
    ["//", "/", "linux", true],
  ])("%s ~ %s on %s → %s", (a, b, p, want) => expect(samePath(a, b, p as NodeJS.Platform)).toBe(want));
});

describe("worktreeItemId", () => {
  it.each([
    ["/k/run/worktrees/abc", "/k/run", "linux", "abc"],
    ["/k/run/worktrees/abc/src", "/k/run", "linux", "abc"],
    ["/k/run/worktrees/abc", "/k/run/", "linux", "abc"],
    ["/k/run/worktrees", "/k/run", "linux", undefined],
    ["/k/run/worktrees/", "/k/run", "linux", undefined],
    ["/k/run/worktreesX/abc", "/k/run", "linux", undefined],
    ["/k/other", "/k/run", "linux", undefined],
    ["/worktrees/abc", "", "linux", undefined],
    ["c:\\K\\run\\worktrees\\abc", "C:/k/run", "win32", "abc"],
  ])("%s under %s → %s", (f, r, p, want) => expect(worktreeItemId(f, r, p as NodeJS.Platform)).toBe(want));
});

describe("folderScope", () => {
  it.each<[string, string, Record<string, string>, Record<string, string>, ConnectedRepo[], string[]]>([
    ["lists folder, realpath and root once each", "/link/src",
      { [`/real/r/src|${TOP}`]: "/real/r", [`/real/r/src|${COMMON}`]: "/real/r/.git" }, { "/link/src": "/real/r/src" }, [],
      ["/link/src", "/real/r/src", "/real/r"]],
    ["plain checkout once", "/r", { [`/r|${TOP}`]: "/r", [`/r|${COMMON}`]: "/r/.git" }, {}, [], ["/r"]],
    ["submodule under a connected superproject", "/s/sub",
      { [`/s/sub|${TOP}`]: "/s/sub", [`/s/sub|${COMMON}`]: "/s/.git/modules/sub" }, {}, [{ path: "/s", managed: true }],
      ["/s/sub", "/s"]],
    ["walks past a nearer unmanaged entry", "/s/sub/src",
      { [`/s/sub/src|${TOP}`]: "/s/sub", [`/s/sub/src|${COMMON}`]: "/s/.git/modules/sub" }, {},
      [{ path: "/s/sub", managed: false }, { path: "/s" }],
      ["/s/sub/src", "/s/sub", "/s"]],
    ["nearest managed ancestor, not the outermost", "/a/s/sub",
      { [`/a/s/sub|${TOP}`]: "/a/s/sub", [`/a/s/sub|${COMMON}`]: "/a/s/.git/modules/sub" }, {},
      [{ path: "/a" }, { path: "/a/s" }],
      ["/a/s/sub", "/a/s"]],
    ["nested non-submodule repo", "/s/nested",
      { [`/s/nested|${TOP}`]: "/s/nested", [`/s/nested|${COMMON}`]: "/s/nested/.git" }, {}, [{ path: "/s" }],
      ["/s/nested", "/s"]],
    ["no connected ancestor", "/s/sub",
      { [`/s/sub|${TOP}`]: "/s/sub", [`/s/sub|${COMMON}`]: "/s/.git/modules/sub" }, {}, [{ path: "/other" }, { path: "/s/sub2" }],
      ["/s/sub"]],
  ])("%s", async (_, folder, git, links, connected, want) => {
    expect((await folderScope(folder, "/k/run", deps(git, links), connected)).roots).toEqual(want);
  });

  it.each([
    ["realpaths run_dir before matching a worktree", "/private/var/k/worktrees/A", "/var/k", { "/var/k": "/private/var/k" }, "A"],
    ["matches the raw paths when realpath moves the folder", "/k/worktrees/A/src", "/k", { "/k/worktrees/A/src": "/elsewhere/src" }, "A"],
    ["not a worktree", "/r", "/k", {}, undefined],
  ])("%s", async (_, folder, runDir, links, want) => {
    expect((await folderScope(folder, runDir, deps({}, links))).worktreeItem).toBe(want);
  });
});

describe("WorkspaceScope", () => {
  const none = () => undefined;

  it("includes nothing before the first update settles", () => {
    expect(new WorkspaceScope("linux").includes({ id: "x", repo: "/r" }, () => "/r")).toBe(false);
  });

  it.each<[string, string, Record<string, string>, { id: string; repo: string }, (id: string) => string | undefined, boolean]>([
    ["subfolder window: its repo", "/r/src", { [`/r/src|${TOP}`]: "/r", [`/r/src|${COMMON}`]: "/r/.git" }, { id: "x", repo: "/r" }, none, true],
    ["subfolder window: another repo", "/r/src", { [`/r/src|${TOP}`]: "/r", [`/r/src|${COMMON}`]: "/r/.git" }, { id: "x", repo: "/q" }, none, false],
    ["worktree window: its item's repo", "/k/worktrees/A", {}, { id: "B", repo: "/r" }, (id) => (id === "A" ? "/r" : undefined), true],
    ["worktree window: another repo", "/k/worktrees/A", {}, { id: "C", repo: "/q" }, (id) => (id === "A" ? "/r" : undefined), false],
    ["worktree window: its own item, repo unknown", "/k/worktrees/A", {}, { id: "A", repo: "/q" }, none, true],
  ])("%s", async (_, folder, git, item, repoOf, want) => {
    const scope = new WorkspaceScope("linux");
    await scope.update([folder], "/k", deps(git));
    expect(scope.ready).toBe(true);
    expect(scope.includes(item, repoOf)).toBe(want);
  });

  it("toggleAll widens to every repo and fires onChange once", async () => {
    const scope = new WorkspaceScope("linux");
    await scope.update(["/r"], "/k", deps({}));
    let fired = 0;
    scope.onChange(() => fired++);
    scope.toggleAll();
    expect([scope.showAll, fired, scope.includes({ id: "x", repo: "/q" }, none)]).toEqual([true, 1, true]);
  });

  it("setShowAll fires onChange only when the choice changes", () => {
    const scope = new WorkspaceScope("linux");
    let fired = 0;
    scope.onChange(() => fired++);
    scope.setShowAll(true);
    scope.setShowAll(true);
    expect([scope.showAll, fired]).toEqual([true, 1]);
  });

  it.each<[string, boolean | undefined, boolean]>([
    ["remembered all repos: shows all before any update", true, true],
    ["nothing remembered: this repo only", undefined, false],
  ])("%s", (_, stored, want) => {
    const scope = rememberedScope("linux", { get: (k, d) => (k === ALL_REPOS_KEY ? stored ?? d : d), update: () => {} });
    expect([scope.showAll, scope.includes({ id: "x", repo: "/q" }, none)]).toEqual([want, want]);
  });

  it("writes the choice back each time it changes, and not on a rescope", async () => {
    const writes: [string, boolean][] = [];
    const scope = rememberedScope("linux", { get: (_k, d) => d, update: (k, v) => writes.push([k, v]) });
    await scope.update(["/r"], "/k", deps({}));
    scope.setShowAll(true);
    scope.setShowAll(true);
    scope.setShowAll(false);
    expect(writes).toEqual([["kraft.allRepos", true], ["kraft.allRepos", false]]);
  });

  it("fires onChange when an update applies, until unsubscribed", async () => {
    const scope = new WorkspaceScope("linux");
    let fired = 0;
    const off = scope.onChange(() => fired++);
    await scope.update(["/r"], "/k", deps({}));
    off();
    await scope.update(["/r"], "/k", deps({}));
    expect(fired).toBe(1);
  });

  it("the last update started wins when an earlier one resolves later", async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    const slow: ScopeDeps = { ...deps({}), realpath: async (p) => { await held; return p; } };
    const scope = new WorkspaceScope("linux");
    let fired = 0;
    scope.onChange(() => fired++);
    const first = scope.update(["/a"], "/k", slow);
    await scope.update(["/b"], "/k", deps({}));
    release();
    await first;
    expect([scope.includes({ id: "x", repo: "/b" }, none), scope.includes({ id: "x", repo: "/a" }, none), fired]).toEqual([true, false, 1]);
  });

  it("a folder whose scope throws still scopes to the folder", async () => {
    const scope = new WorkspaceScope("linux");
    const broken: ScopeDeps = { ...deps({}), git: async () => { throw new Error("spawn git ENOENT"); } };
    await scope.update(["/r", "/k/worktrees/A"], "/k", broken);
    expect([scope.ready, scope.includes({ id: "x", repo: "/r" }, none), scope.includes({ id: "A", repo: "/elsewhere" }, none)]).toEqual([true, true, true]);
  });
});

describe("WorkspaceScope runs git once per folder", () => {
  it("a new connected list or run_dir redoes only the cheap steps; a new folder asks git", async () => {
    const calls: string[] = [];
    const base = deps({ [`/s/sub|${TOP}`]: "/s/sub", [`/s/sub|${COMMON}`]: "/s/.git/modules/sub", [`/t|${TOP}`]: "/t", [`/t|${COMMON}`]: "/t/.git" });
    const counted: ScopeDeps = { ...base, git: (args, cwd) => (calls.push(cwd), base.git(args, cwd)) };
    const scope = new WorkspaceScope("linux");
    await scope.update(["/s/sub"], "/k", counted);
    await scope.update(["/s/sub"], "/k2", counted, [{ path: "/s" }]);
    const afterRepeat = calls.length;
    await scope.update(["/s/sub", "/t"], "/k2", counted, [{ path: "/s" }]);
    expect([afterRepeat, calls.length, scope.includes({ id: "x", repo: "/s" }, () => undefined)]).toEqual([2, 4, true]);
  });
});

describe("FolderScope root and git", () => {
  it.each([
    ["a checkout: git answered", "/r/src", { [`/r/src|${TOP}`]: "/r", [`/r/src|${COMMON}`]: "/r/.git" }, { root: "/r", git: true }],
    ["a plain folder: git did not", "/tmp/x", {}, { root: "/tmp/x", git: false }],
  ])("%s", async (_, folder, git, want) => {
    const { root, git: isGit } = await folderScope(folder, "/k", deps(git));
    expect({ root, git: isGit }).toEqual(want);
  });
});

describe("WorkspaceScope.unconnected", () => {
  const repo = (p: string, top = p) => ({ [`${p}|${TOP}`]: top });
  type Row = [string, string[], Record<string, string>, NodeJS.Platform, ConnectedRepo[] | undefined, string[]];
  it.each<Row>([
    ["its repo connected", ["/a"], repo("/a"), "linux", [{ path: "/a" }], []],
    ["one of two folders connected", ["/a", "/b"], { ...repo("/a"), ...repo("/b") }, "linux", [{ path: "/a" }], ["/b"]],
    ["win32 compares case and separators", ["C:\\Repo"], repo("C:\\Repo", "C:/Repo"), "win32", [{ path: "c:\\repo" }], []],
    ["linux paths differ by case", ["/A"], repo("/A"), "linux", [{ path: "/a" }], ["/A"]],
    ["a submodule under a managed superproject", ["/super/sub"],
      { [`/super/sub|${TOP}`]: "/super/sub", [`/super/sub|${COMMON}`]: "/super/.git/modules/sub" }, "linux",
      [{ path: "/super", managed: true }], []],
    ["two folders of one repo, once", ["/r/src", "/r/lib"], { ...repo("/r/src", "/r"), ...repo("/r/lib", "/r") }, "linux", [], ["/r"]],
    ["a folder that is not a git repo", ["/tmp/x"], {}, "linux", [], []],
    ["no /repos read has succeeded", ["/b"], repo("/b"), "linux", undefined, []],
  ])("%s", async (_, folders, git, platform, connected, want) => {
    const scope = new WorkspaceScope(platform);
    await scope.update(folders, "/k", deps(git, {}, platform), connected ?? []);
    expect(scope.unconnected(connected as { path: string; managed: boolean }[] | undefined)).toEqual(want);
  });

  it("offers nothing before the scope is ready", () =>
    expect(new WorkspaceScope("linux").unconnected([])).toEqual([]));
});

describe("refetchRepos", () => {
  const T = 100_000;
  const known: ConnectedRepo[] = [{ path: "/r" }];
  it.each([
    ["every item's repo is known", [{ repo: "/r" }], known, T - REFETCH_MS, false],
    ["an item in an unknown repo", [{ repo: "/r" }, { repo: "/new" }], known, T - REFETCH_MS, true],
    ["an unknown repo, read less than 30s ago", [{ repo: "/new" }], known, T - REFETCH_MS + 1, false],
    ["the last read failed", [], undefined, T - REFETCH_MS, true],
    ["the last read failed less than 30s ago", [], undefined, T - 1, false],
  ])("%s", (_, items, k, lastRead, want) => {
    expect(refetchRepos(items, k as ConnectedRepo[] | undefined, lastRead, T, "linux")).toBe(want);
  });
});
