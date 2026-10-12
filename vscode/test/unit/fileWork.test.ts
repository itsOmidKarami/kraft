import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { ChainNode } from "../../../frontend/src/types";
import type { Created, DryRun, Repo, TemplateSummary } from "../../src/core/api";
import {
  abandonRefusal, attachChoices, attachState, chainsFor, cwdFor, createBody, draftNote, draftUrl, fileRefusal, localRefusal, MAX_TITLE, MAX_URL, outcomeMessage,
  pickRepo, previewLine, repoDefaultChain, titleFrom, titleProblem, warningActions, warningsOf,
} from "../../src/core/fileWork";
import { RESTRICTED, VERSION_REFUSAL } from "../../src/core/readOnly";

describe("titleFrom", () => {
  it.each([
    ["first line", "Fix the cache\nmore", "Fix the cache"],
    ["skips blank lines", "\n   \n  Fix it  \nx", "Fix it"],
    ["splits where Python does", "  \u2028Fix it\u2029x", "Fix it"],
    ["tabs become spaces", "\tif (a)\t{", "if (a) {"],
    ["DEL, C1, bidi and a lone surrogate become spaces", "a\x7fb\x9bc\u202ed\ud800e", "a b c d e"],
    ["empty", "  \n ", ""],
  ])("%s", (_, text, want) => expect(titleFrom(text)).toBe(want));
  it("truncates to 500 code points without splitting a surrogate pair", () => {
    const t = titleFrom("a".repeat(499) + "😀😀");
    expect([...t].length).toBe(500);
    expect(t.endsWith("😀")).toBe(true);
  });
});

describe("titleProblem", () => {
  it.each([
    ["", "A work item needs a title."],
    ["a\u2028b", "The title is one line: put the rest in the description."],
    ["a\tb", "The title is plain text: no tab or other control character."],
    ["a\ud800b", "The title is plain text: no tab or other control character."],
    ["x".repeat(501), "The title is 501 characters; the limit is 500."],
    ["😀".repeat(500), undefined],
  ])("%j → %s", (t, want) => expect(titleProblem(t)).toBe(want));
});

describe("createBody", () => {
  const d = { title: " T ", description: " d ", repo: "/r", cwd: "/r/sub" };
  it("sends no chain, attachments or cwd when there are none, and autostart always", () =>
    expect(createBody(d, false)).toEqual({ title: "T", description: "d", repo: "/r", autostart: false }));
  it("sends the chain, absolute attachments and cwd together", () =>
    expect(createBody({ ...d, chain: "docs_only", spec: "/r/a.md", plan: "/r/b.md" }, true)).toEqual({
      title: "T", description: "d", repo: "/r", chain: "docs_only",
      attachments: [{ kind: "spec", path: "/r/a.md" }, { kind: "plan", path: "/r/b.md" }], cwd: "/r/sub", autostart: true }));
});

describe("pickRepo", () => {
  const r = (path: string, over: Partial<Repo> = {}) => ({ path, enabled: true, default_chain: "default", ...over }) as Repo;
  it.each([
    ["the normalized root of a subfolder", ["/r/src", "/r"], [r("/x"), r("/r")], "/r"],
    ["the realpath of a symlinked folder", ["/link", "/real/r"], [r("/real/r")], "/real/r"],
    ["a linked worktree's main checkout", ["/wt", "/r"], [r("/r")], "/r"],
    ["the most specific match", ["/s/sub", "/s"], [r("/s"), r("/s/sub")], "/s/sub"],
    ["a submodule's superproject, past its auto-connected entry", ["/s/mod", "/s"], [r("/s/mod", { managed: false }), r("/s")], "/s"],
  ])("%s", (_, roots, repos, want) => expect(pickRepo(roots, repos, "linux")).toEqual({ repo: expect.objectContaining({ path: want }) }));
  it("refuses an unconnected repo, naming its root", () =>
    expect(pickRepo(["/n/src", "/n"], [r("/r")], "linux")).toEqual({
      refusal: "/n is not a repo Kraft has connected. Connect this repo first: kraft repo connect /n" }));
  it("refuses a disabled repo", () =>
    expect(pickRepo(["/r"], [r("/r", { enabled: false })], "linux")).toEqual({
      refusal: "/r is disabled in Kraft. Enable it in Settings › Repos first." }));
});

const chain = (id: string, addon: string | null = null, error: string | null = null) =>
  ({ id, addon: addon && { id: addon, version: "1" }, nodes: [], gates: 0, error }) as TemplateSummary;

describe("chainsFor", () => {
  it("keeps own, add-on and this repo's library chains; drops other repos' and broken ones", () =>
    expect(chainsFor([chain("default"), chain("acme:x", "x@acme"), chain("web:default", "library@repo:web"),
                      chain("api:default", "library@repo:api"), chain("bad", null, "boom")],
                     { path: "/w", id: "web" } as Repo).map((x) => x.id))
      .toEqual(["default", "acme:x", "web:default"]));
  it("drops every repo-library chain for a repo with no id", () =>
    expect(chainsFor([chain("web:default", "library@repo:web")], { path: "/w", id: null } as Repo)).toEqual([]));
});

describe("repoDefaultChain", () => {
  const lib = [chain("default"), chain("web:default", "library@repo:web")];
  it.each([
    ["the repo's own default_chain", { default_chain: "docs_only", id: "web" }, "docs_only"],
    ["its library's default when it names `default`", { default_chain: "default", id: "web" }, "web:default"],
    ["`default` when its library has none", { default_chain: "default", id: "api" }, "default"],
    ["`default` for a repo with no id", { default_chain: "default", id: null }, "default"],
  ])("%s", (_, repo, want) => expect(repoDefaultChain({ path: "/w", ...repo } as Repo, lib)).toBe(want));
});

describe("localRefusal", () => {
  it.each([
    ["http://127.0.0.1:8765", null],
    ["http://localhost:8765", null],
    ["https://kraft.example:8765", "Filing work needs Kraft on the machine with this window's files, and kraft.url points at kraft.example. File it from the web UI instead."],
  ])("%s", (base, want) => expect(localRefusal(base)).toBe(want));
});

describe("fileRefusal", () => {
  const remote = "https://kraft.example";
  it.each([
    ["Restricted Mode before the remote URL", { trusted: false, version: null }, remote, RESTRICTED],
    ["a refused version before the remote URL", { trusted: true, version: "old" }, remote, VERSION_REFUSAL],
    ["the remote URL", { trusted: true, version: null }, remote, localRefusal(remote)],
    ["nothing, on loopback", { trusted: true, version: null }, "http://127.0.0.1:8765", null],
  ])("%s", (_, state, base, want) => expect(fileRefusal(state, base)).toBe(want));
});

describe("attachState", () => {
  it.each([
    ["a saved file", { scheme: "file", isUntitled: false, isDirty: false }, "file", "ok"],
    ["a dirty file", { scheme: "file", isUntitled: false, isDirty: true }, "file", "save"],
    ["a saved file in a remote window", { scheme: "vscode-remote", isUntitled: false, isDirty: false }, "vscode-remote", "ok"],
    ["a dirty file in a remote window", { scheme: "vscode-remote", isUntitled: false, isDirty: true }, "vscode-remote", "save"],
    ["an untitled buffer", { scheme: "untitled", isUntitled: true, isDirty: true }, "file",
      { refusal: "Save this file into the repo first: an unsaved file cannot be attached." }],
    ["a gate document", { scheme: "kraft-artifact", isUntitled: false, isDirty: false }, "file", { refusal: "Only a file on disk can be attached." }],
    ["a local file in a remote window", { scheme: "file", isUntitled: false, isDirty: false }, "vscode-remote", { refusal: "Only a file on disk can be attached." }],
  ])("%s", (_, doc, folderScheme, want) => expect(attachState(doc, folderScheme)).toEqual(want));
});

describe("attachChoices", () => {
  it("offers the active .md file as spec or plan", () =>
    expect(attachChoices("/r/docs/x.md")).toEqual([
      { label: "No documents" }, { label: "x.md as spec", spec: "/r/docs/x.md" },
      { label: "x.md as plan", plan: "/r/docs/x.md" }, { label: "Pick files…", pick: true }]));
  it("offers none and pick without one", () =>
    expect(attachChoices(undefined).map((c) => c.label)).toEqual(["No documents", "Pick files…"]));
});

it("MAX_TITLE matches the server's", () => {
  const py = readFileSync(join(__dirname, "../../../src/kraft/adapters/beads.py"), "utf8");
  expect(py).toMatch(new RegExp(`^MAX_TITLE = ${MAX_TITLE}$`, "m"));
});

const n = (id: string, kind: "exec" | "gate" = "exec") => ({ id, kind, tasks: [] }) as unknown as ChainNode;

describe("previewLine", () => {
  it.each([
    ["repo default", { nodes: [n("a"), n("g", "gate")], skipped: [], gates: ["g"] }, "repo default · 2 of 2 nodes run · 1 gate"],
    ["docs_only", { nodes: [n("impl")], skipped: [{ node: "spec", why: "covered_by", kind: "spec" }, { node: "lint", why: "skip" }], gates: [] },
      "docs_only · 1 of 3 nodes run · 0 gates · skips spec (spec attached), lint"],
  ])("%s", (label, dry, want) => expect(previewLine(label, { dry_run: true, ...dry } as DryRun)).toBe(want));
});

describe("outcomeMessage", () => {
  it.each([
    ["filed paused", false, { id: "K-1", status: "paused" }, "Filed K-1 paused. Nothing spends tokens until you start it."],
    ["started", true, { id: "K-1", status: "active" }, "Filed K-1 and started it."],
    ["queued on slots", true, { id: "K-1", status: "queued", slots: { busy: 2, limit: 2 } },
      "Filed K-1, queued: 2 of 2 slots are busy. It starts when one frees."],
    ["queued on storage", true, { id: "K-1", status: "queued", slots: { busy: 0, limit: 2, storage: { used_bytes: 2 * 1024 ** 3, limit_bytes: 2 * 1024 ** 3 } } },
      "Filed K-1, queued: worktrees use 2G of the 2G storage limit. It starts when space frees."],
    ["an answer it does not know, said as it is", true, { id: "K-1", status: "someday" }, "Filed K-1: it is someday."],
  ])("%s", (_, start, c, want) => expect(outcomeMessage(start, c as Created)).toBe(want));
});

describe("warnings", () => {
  const c = { id: "K-1", status: "paused", repo_warning: "R", duplicate_warning: "D" } as Created;
  it("lists them in order", () => expect(warningsOf(c)).toEqual(["D", "R"]));
  it("offers Abandon except on an active item", () => {
    expect(warningActions(c)).toEqual(["Open item", "Abandon"]);
    expect(warningActions({ ...c, status: "active" })).toEqual(["Open item"]);
  });
});

describe("draftUrl", () => {
  it("carries title, brief, repo, chain and attachments", () => {
    const { url, briefDropped, outside } = draftUrl("http://127.0.0.1:8765", { title: "T x", description: "b", repo: "/r", chain: "docs_only", spec: "/r/s.md" });
    const u = new URL(url);
    expect(u.pathname).toBe("/work-items/new");
    expect(Object.fromEntries(u.searchParams)).toEqual({ title: "T x", brief: "b", repo: "/r", chain: "docs_only", spec: "/r/s.md" });
    expect([briefDropped, outside]).toEqual([false, []]);
  });
  it("drops a brief that would make the link too long", () => {
    const { url, briefDropped } = draftUrl("http://h", { title: "T", description: "x".repeat(MAX_URL), repo: "/r" });
    expect(new URL(url).searchParams.has("brief")).toBe(false);
    expect(briefDropped).toBe(true);
  });
  it.each([
    ["another worktree's document", "/wt/s.md", ["/wt/s.md"]],
    ["a sibling whose name starts like the repo", "/r2/s.md", ["/r2/s.md"]],
    ["a document in the repo", "/r/docs/s.md", []],
  ])("leaves out %s, which the page cannot attach", (_, spec, want) => {
    const { url, outside } = draftUrl("http://h", { title: "T", description: "", repo: "/r", spec });
    expect(outside).toEqual(want);
    expect(new URL(url).searchParams.has("spec")).toBe(want.length === 0);
  });
});

describe("draftNote", () => {
  it.each([
    ["nothing left out", { briefDropped: false, outside: [] }, undefined],
    ["the brief", { briefDropped: true, outside: [] }, "The description is too long for a link: it is on your clipboard. Paste it into Brief."],
    ["a document", { briefDropped: false, outside: ["/wt/s.md"] }, "s.md is outside /r, which the web page cannot attach: it is left out."],
  ])("%s", (_, link, want) => expect(draftNote({ url: "", ...link }, "/r")).toBe(want));
});

describe("cwdFor", () => {
  it.each([
    ["nothing when every document is in the repo", [{ path: "/r/s.md", top: "/r" }], {}],
    ["the worktree holding the one outside it", [{ path: "/r/s.md", top: "/r" }, { path: "/wt/p.md", top: "/wt" }], { cwd: "/wt" }],
    ["one worktree holding both", [{ path: "/wt/a/s.md", top: "/wt" }, { path: "/wt/b/p.md", top: "/wt" }], { cwd: "/wt" }],
    ["a refusal for two worktrees", [{ path: "/w1/s.md", top: "/w1" }, { path: "/w2/p.md", top: "/w2" }],
      { refusal: "s.md and p.md are in different worktrees (/w1, /w2). Kraft takes documents from /r and one of its worktrees: move one of them first." }],
  ])("%s", (_, atts, want) => expect(cwdFor(atts, "/r")).toEqual(want));
});

describe("abandonRefusal", () => {
  it.each([
    ["asks the server about an item the board has not read", undefined, undefined],
    ["offers it while the server lists abandon", { status: "paused", display_status: "paused", actions: ["resume", "abandon"] }, undefined],
    ["refuses once the server no longer lists it", { status: "active", display_status: "running", actions: ["pause"] },
      "K-1 can no longer be abandoned: it is running. Open it to see what it allows now."],
    ["takes the server's actions over the status", { status: "paused", display_status: "paused", actions: ["resume"] },
      "K-1 can no longer be abandoned: it is paused. Open it to see what it allows now."],
    ["refuses an active item from a server without actions", { status: "active", display_status: "running" },
      "K-1 can no longer be abandoned: it is running. Open it to see what it allows now."],
    ["offers a queued one from a server without actions", { status: "queued", display_status: "queued" }, undefined],
  ])("%s", (_, it_, want) => expect(abandonRefusal("K-1", it_ as never)).toBe(want));
});
