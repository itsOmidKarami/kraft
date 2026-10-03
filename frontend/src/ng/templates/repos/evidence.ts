import type { MissingTool, ProbeCandidate, ProbeStop } from "../../../types/settings";
import { NO_TESTS } from "./fields";

/** What a probe read its proposal from: the sources of the chosen `role`
 *  commands ("justfile recipe `test`"), or null when it chose none. A test
 *  is the root's; a setup joins every directory's, each named, since the
 *  repo's one setup command prepares them all. */
export function chosenSource(candidates: ProbeCandidate[] | undefined, role: "test" | "setup"): string | null {
  const chosen = (candidates ?? []).filter((c) => c.chosen && c.role === role && (role === "setup" || c.dir === ""));
  return chosen.length ? chosen.map((c) => (c.dir ? `${c.dir}/: ${c.source}` : c.source)).join(" + ") : null;
}

/** Whether `saved` (a test or setup command, as saved) already runs `c`. */
function runs(saved: string, c: ProbeCandidate): boolean {
  if (c.dir) return saved.includes(`cd ${c.dir} && ${c.command}`);
  return saved === c.command || saved.startsWith(`${c.command} && `) || saved.includes(` && ${c.command}`);
}

/** The `role` commands the probe found and did not propose (nor are already
 *  part of a `saved` command), the first `shown` of them named with their
 *  source: "make test (Makefile target `test`) and 2 more". Said here, since
 *  once the repo is connected nothing lists them again. */
export function others(
  candidates: ProbeCandidate[] | undefined,
  role: "test" | "setup",
  saved: (string | null | undefined)[] = [],
  shown = 3,
): string | null {
  const kept = saved.filter((s): s is string => !!s);
  const rest = (candidates ?? []).filter((c) => !c.chosen && c.role === role && !kept.some((s) => runs(s, c)));
  if (!rest.length) return null;
  const named = rest.slice(0, shown).map((c) => `${c.dir ? `${c.dir}/: ` : ""}${c.command} (${c.source})`);
  return named.join(", ") + (rest.length > shown ? ` and ${rest.length - shown} more` : "");
}

/** The setup row: the command with its sources, or, when there is none to
 *  save, why: nothing found, or a setup found while a directory with tests
 *  has nothing to prepare it (which leaves the repo's setup undecided). */
export function setupLine(p: { setup_command?: string | null; candidates?: ProbeCandidate[]; missing_setup?: string[] }): string {
  if (p.setup_command) return withSource(p.setup_command, chosenSource(p.candidates, "setup"));
  if (p.setup_command === "") return "none needed";
  // Each from its own directory: `npm ci && npm ci found` read like a command that fails at the root.
  const found = (p.candidates ?? []).filter((c) => c.chosen && c.role === "setup").map((c) => (c.dir ? `(cd ${c.dir} && ${c.command})` : c.command));
  const missing = (p.missing_setup ?? []).map((d) => (d === "." ? "the root" : `${d}/`));
  if (found.length && missing.length) {
    const has = missing.length > 1 ? "have nothing to prepare them" : "has nothing to prepare it";
    return `${found.join(" && ")} found, but ${missing.join(", ")} ${has}`;
  }
  return "none found";
}

/** The tests row: the root's test command with where it came from, or why there is
 *  none. A monorepo with no tests at the root is said so, rather than headed by its
 *  first scope's `sh -c 'cd backend && …'` as if that were the repo's command. */
export function testsLine(p: { test_command: string | null; test_scopes?: { paths: string[] }[] | null; candidates?: ProbeCandidate[]; stopped?: ProbeStop[] }): string {
  const source = chosenSource(p.candidates, "test");
  if (!source && (p.test_scopes ?? []).some((s) => s.paths.join() !== "**")) return "none at the root: each scope below has its own";
  if (p.test_command) return withSource(p.test_command, source);
  return p.stopped?.length ? "none proposed" : "none found";
}

/** Why a directory proposes no test command: "the root is a pyproject.toml…". */
export function stopLine(s: ProbeStop): string {
  return `${s.dir === "." ? "the root" : `${s.dir}/`} is ${s.reason}`;
}

/** "deno (runtime-tests/deno/), cargo (the root)": what to install before a
 *  work item runs, or null. */
export function missingLine(tools: MissingTool[] | undefined): string | null {
  if (!tools?.length) return null;
  const named = tools.map((t) => `${t.tool} (${t.dir === "." ? "the root" : `${t.dir}/`})`);
  return `${named.join(", ")}: not installed here, so a work item would fail on it`;
}

/** Why a repo with no commit (a probe's `read_from: null`) cannot be connected yet:
 *  `POST /repos` and `add_repo` refuse it, as a work item's branch would be an empty orphan. */
export const NO_COMMIT = "its working copy: the repo has no commit yet, and a work item's branch starts from one. Commit its files, then check it again.";

/** The commit the probe read, as a person names it: `origin/main`, or the
 *  checkout's HEAD when the clone has no origin branch. Edits not committed
 *  and pushed there are not read, so it is said. `null` is a repo with no commit. */
export function readFrom(ref: string | null | undefined): string | null {
  if (ref === null) return NO_COMMIT;
  if (!ref) return null;
  const named = ref.replace(/^refs\/remotes\//, "");
  return named === "HEAD"
    ? "this checkout's HEAD (no origin branch): uncommitted edits are not read"
    : `${named}, where work items start: commits not pushed there are not read`;
}

/** `cmd — from source`, or the command alone when the probe gave no source. */
export function withSource(command: string, source: string | null): string {
  return source ? `${command} — from ${source}` : command;
}

type Tested = { test_command?: unknown; test_scopes?: unknown };

/** A repo's tests in the list: its test command, and how many scopes beside it,
 *  so a monorepo's one command does not read as its whole suite. */
export function testsCell(e: Tested): string {
  const scopes = Array.isArray(e.test_scopes) ? e.test_scopes.length : 0;
  const cmd = typeof e.test_command === "string" ? e.test_command : "";
  if (!scopes) return e.test_command === "" ? NO_TESTS : cmd || "—";
  return `${cmd || "no root command"} + ${scopes} scope${scopes === 1 ? "" : "s"}`;
}

/** Every command it runs, one per line, for the cell's title. */
export function testsTitle(e: Tested): string | undefined {
  const scopes = Array.isArray(e.test_scopes) ? (e.test_scopes as { paths?: string[]; command?: string }[]) : [];
  const lines = scopes.map((s) => `${(s.paths ?? []).join(", ")}: ${s.command ?? ""}`);
  const cmd = typeof e.test_command === "string" ? e.test_command : undefined;
  return lines.length ? lines.join("\n") : cmd;
}
