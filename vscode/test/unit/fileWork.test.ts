import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Repo, TemplateSummary } from "../../src/core/api";
import {
  attachChoices, attachState, chainsFor, createBody, fileRefusal, localRefusal, MAX_TITLE, pickRepo, repoDefaultChain, titleFrom, titleProblem,
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
