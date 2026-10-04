// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The phone is a separate app shape (W17 brief 0.2): it shares data hooks and
// pure models with the desktop, never a desktop pane. This list is the one door.
// A new shared module needs a line here and a reason.
const ALLOWED = [
  "ui/", // shared primitives, not restyled (R59)
  "theme/",
  "graph/NodeGlyph",
  "graph/types",
  "graph/layout", // the ChainNode type only
  "icons",
  "http",
  "live",
  "shell/aliases", // the shipped addresses that moved, and the #hash fixer (W18 PR 1)
  "item/useItem",
  "item/useEvents",
  "item/useDocuments",
  "item/status",
  "item/actions",
  "item/url",
  "item/paths",
  "item/limitPolicy", // the Raise cap PATCH body that keeps the rest of the override, one for both apps
  "item/chainName",
  "item/chainValues", // the frozen chain and the caps it resolves to: a node's attempts, wall clock and YAML, read as the desktop reads them
  "item/nodeGraph",
  "item/graph",
  "item/cause", // failedFix, the forge login words and the work-kept line: a failed card says one thing on both layouts (R15b-01)
  "item/TestsLine", // the gate's last test run, one line, as the desktop's gate pane reads it (R15b-01)
  "item/events", // gateSkipped: a skipped gate reads as skipped on both layouts (R14b-05)
  "item/draft/applied", // appliedRows: what an applied draft set, as the desktop's Config lists it (R14b-04)
  "item/draft/useApplied", // the applied drafts, folded from the whole event log
  "item/draft/view", // lines(): the draft's changes as the desktop's Start dialog lists them (R10b-12)
  "board/model",
  "board/counts", // the board's counts, one definition for every screen that shows one
  "board/rowText",
  "board/bulk",
  "board/prefs",
  "board/draft/draftBody",
  "review/useReview",
  "review/url",
  "review/finish",
  "review/patch",
  "review/range", // rangeName and threadRange: a thread's lines named as the desktop names them
  "review/FinishReview", // useSubmit only: the one review submit the desktop sends
  "review/model",
  "templates/draft/useConfigDraft",
  "templates/draft/draftApi",
  "templates/draft/types",
  "templates/draft/view", // counts(), the pure helpers
  "templates/problems", // problemText
  "review/prefs", // DEFAULT_PREFS and the DiffPrefs type: the diff choices Appearance saves
  "settings/accessWords", // what Access says, on both layouts
  "settings/policy/ctx", // the policy sections' shared context type and problemAt
  "settings/policy/keys", // the set_value keys the Policy page sends
  "settings/policy/sections", // which section a key or a problem belongs to
  "settings/policy/types", // the policy draft's resolve
  "settings/policy/units", // how a number reads and parses
  "settings/intake/checks", // the check sentence and its time
  "settings/intake/cron", // a cron in words
  "settings/intake/types", // the intake draft's resolve and a check row
  "settings/intake/units", // minutes on the page, seconds on the wire
  "harnesses/model", // the harnesses draft's resolve, as types and pure helpers
  "harnesses/ops", // entryOp: the one set_profile patch
  "harnesses/useProviders", // the providers' accepted efforts and models
  "templates/repos/fields", // the Repos rows: label, parse, patch, source
  "templates/repos/types", // reposOf, runningOf, problemsOf
  "templates/repos/RepoConfig", // setRepo only: the preview-checked set_repo
  "templates/choices", // the steering profiles and harnesses a typed list takes, as data
  "templates/useLibrary", // the published library's components, read once
  "templates/panes/useHarnessOptions", // the harness profiles, read once
  "library/rows", // listRows: the library draft's rows, as data
  "library/types", // the four sections and the ref grammar
  "shell/useDraftCounts", // the draft dots of More
  "apply/store", // the apply state; Restart has one call site, behind the phone's own confirm
  "shell/routes", // the list of pages, as data: Search's Go to rows
  "shell/health", // olderServer and useHealth: the restart banner over the board, one test for both layouts
];

const ng = join(dirname(fileURLToPath(import.meta.url)), "..");
const phone = join(ng, "phone") + sep;
const src = join(ng, "..");

const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !e.name.includes(".test.") && !e.name.startsWith("testkit") ? [join(dir, e.name)] : [],
  );

/** Every relative import of a file that leaves `ng/phone` for another part of `ng/`, or reaches `deriveState`. */
export function violations(files: { path: string; text: string }[]): string[] {
  const bad: string[] = [];
  for (const f of files) {
    for (const m of f.text.matchAll(/(?:from|import)\s*\(?\s*["'](\.[^"']*)["']/g)) {
      const target = resolve(dirname(f.path), m[1]);
      const rel = relative(src, target);
      if (/(^|\/)deriveState$/.test(rel)) bad.push(`${relative(ng, f.path)}: ${m[1]} (deriveState)`);
      if (!target.startsWith(ng + sep) || target.startsWith(phone)) continue;
      const inNg = relative(ng, target).split(sep).join("/").replace(/\.(css|tsx?)$/, "");
      if (!ALLOWED.some((a) => (a.endsWith("/") ? inNg.startsWith(a) : inNg === a))) bad.push(`${relative(ng, f.path)}: ${m[1]}`);
    }
  }
  return bad;
}

describe("ng/phone imports", () => {
  it("take only data hooks, models and shared primitives from the rest of ng/, and never deriveState", () => {
    const files = sources(join(ng, "phone")).map((path) => ({ path, text: readFileSync(path, "utf-8") }));
    expect(files.length).toBeGreaterThan(5);
    expect(violations(files)).toEqual([]);
  });

  it("catches a desktop pane and deriveState, and lets a data hook through", () => {
    const at = join(ng, "phone", "x", "File.tsx");
    const text = (s: string) => [{ path: at, text: s }];
    expect(violations(text('import { StateCard } from "../../item/StateCard";'))).toHaveLength(1);
    expect(violations(text('import { Row } from "../../board/Row";'))).toHaveLength(1);
    expect(violations(text('import { deriveState } from "../../../deriveState";'))).toHaveLength(1);
    expect(violations(text('import { useItem } from "../../item/useItem";\nimport { Button } from "../../ui/Button";'))).toEqual([]);
  });
});

describe("the apply restart call", () => {
  it("is made from two places in ng/phone, More and Access, each behind its own confirm", () => {
    const hits = sources(join(ng, "phone")).filter((f) => /runRestart\(/.test(readFileSync(f, "utf-8")));
    expect(hits.map((f) => relative(ng, f)).sort()).toEqual([join("phone", "areas", "Access.tsx"), join("phone", "more", "More.tsx")]);
    expect(sources(join(ng, "phone")).filter((f) => readFileSync(f, "utf-8").includes("/apply/restart"))).toEqual([]);
  });
});
