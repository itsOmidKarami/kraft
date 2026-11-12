import type { ItemBundle } from "./fixtures";
import { NG_REPOS } from "./ngBoard";
import { chainOf } from "./ngChain";
import { forgesFixture } from "./ngForges";
import { NG_NOW } from "./clock";
import { PLAN, REPAIR } from "./nodeRecipe";

/**
 * What each board item is about (ngBoard.ts ROWS and ARCHIVED, by bead; live motion's refiled items, by title): its
 * brief, the files it changes, its merge request's number, and from those its diff, its documents and their text.
 * `ngBoard.ts` hangs an item's entry on its bundle (`content`) in the default sample only; the item page's samples,
 * the long and many variants, and an item a visitor files carry none, and keep the generic data in fixtures.ts.
 */

/** `late`: verification's round added it; `new` / `deleted`: the change adds or removes the whole file. */
type Flag = "late" | "new" | "deleted";
/** A file the item changes: its path, lines added, lines removed, and its flags. */
export type File = [path: string, insertions: number, deletions: number, ...flags: Flag[]];
/** A file's hunk written out where its template would not hold what a review thread quotes: its first line's number
 *  on both sides, and its lines, each led by its diff mark. */
export type Rows = { at: number; rows: string[] };
/** `question`: what its code review asked, for a row stopped on a question (ngItems.ts, needs-you). `repo`: the repo the
 *  walk files it into (FRESH_CONTENT; a board row has its own, ngBoard.ts). */
export interface Content { description: string; files: File[]; mr: number; hunks?: Record<string, Rows>; question?: string; repo?: string }

export const isLate = (f: File) => f.slice(3).includes("late");
/** A docs path: a page anywhere, or a site file under `docs/` (its pages, navigation, styles, scripts and layouts); a
 *  program under `docs/` (`docs/_nav.py`) is code. */
export const isDocsPath = (p: string) => p.endsWith(".md") || (p.startsWith("docs/") && /\.(md|ya?ml|css|js|html|svg|png)$/.test(p));
const statusOf = (f: File) => (f.slice(3).includes("new") ? "new" : f.slice(3).includes("deleted") ? "deleted" : null);

/** By bead: the 24 board rows. Merge request numbers count up with the item's age; none is in 150-159, which the
 *  walk gives an item it has no entry for (`transitions.mrOpened`). */
export const CONTENT: Record<string, Content> = {
  "kraft-cb59": { mr: 142, description: "Search re-embeds every candidate document on every query, so a large repository waits two to four seconds for each search. Cache the embeddings keyed by each document's content hash, drop an entry when reindex sees a new hash, and keep the cache to a fixed size. The search API stays as it is.",
    files: [["plugins/search/cache.py", 86, 0, "new"], ["plugins/search/reindex.py", 22, 9], ["plugins/search/embed.py", 14, 11], ["plugins/search/config.py", 6, 0], ["tests/test_cache.py", 74, 0, "new"], ["docs/search.md", 12, 2]],
    // The new module the review's threads quote (ngItems.ts, needs-gate): a lookup by the document's content hash, and a
    // store that moves its entry to the end (the review's must-fix) and evicts the least recently used past the bound.
    hunks: { "plugins/search/cache.py": { at: 1, rows: [
      "\"\"\"Embeddings for search, cached by each document's content hash.\"\"\"", "from collections import OrderedDict", "", "",
      "class EmbeddingCache:", "    \"\"\"Embeddings keyed by each document's content hash, at most `size` of them.\"\"\"", "",
      "    def __init__(self, size=4096):", "        self._size = size", "        self._cache = OrderedDict()", "",
      "    def lookup(self, doc):", "        \"\"\"The embedding stored for `doc`'s content, or None.\"\"\"", "        key = doc.content_hash", "        if key not in self._cache:", "            return None", "        self._cache.move_to_end(key)", "        return self._cache[key]", "",
      "    def store(self, doc, embedding):", "        self._cache[doc.content_hash] = embedding", "        self._cache.move_to_end(doc.content_hash)", "        if len(self._cache) > self._size:", "            self._cache.popitem(last=False)",
    ].map((l) => `+${l}`) } } },
  "kraft-7d21": { mr: 132, description: "test_retry_after_crash fails about one run in twenty on CI: it sleeps for a fixed 50 ms and expects the pool to have replaced the crashed worker by then. Wait on the pool's restart event instead of a timer, and give the pool a hook the test can await.",
    files: [["kraft/workers/pool.py", 18, 6], ["kraft/workers/events.py", 9, 0], ["tests/workers/test_pool.py", 23, 17]] },
  "kraft-a3f0": { mr: 129, description: "Clients of the public API cannot tell how close they are to the rate limit until a request fails with 429. Send X-RateLimit-Limit, X-RateLimit-Remaining and X-RateLimit-Reset on every response, read from the limiter that already counts requests.",
    question: "The limiter counts requests per worker process, so X-RateLimit-Remaining would be one worker's count, not the client's. Count per client in the shared store, which the plan left out, or send the per-worker numbers and say so on the docs page?",
    files: [["kraft_api/middleware/ratelimit.py", 41, 7], ["kraft_api/limiter.py", 16, 3], ["kraft_api/app.py", 4, 1], ["tests/test_ratelimit_headers.py", 58, 0, "new"], ["docs/api/rate-limits.md", 19, 2], ["tests/test_limiter.py", 9, 2, "late"]] },
  "kraft-58e2": { mr: 121, description: "The config loader still calls the old parse_obj and validator APIs, which warn on every start and are on their way out. Move each model to model_validate and field_validator, keep the error messages the CLI prints today, and drop the compatibility shim.",
    files: [["kraft/config/loader.py", 48, 39], ["kraft/config/models.py", 72, 66], ["kraft/config/compat.py", 0, 41, "deleted"], ["tests/config/test_loader.py", 27, 14], ["pyproject.toml", 1, 1]] },
  "kraft-b108": { mr: 125, description: "A burst of forge calls in a busy hour gets 429 Too Many Requests, and the task fails on the first one. Retry a 429 after the Retry-After the forge sends, up to three times, and fail with the forge's own message after that.",
    files: [["kraft_api/forge/client.py", 37, 8], ["kraft_api/forge/retry.py", 44, 0, "new"], ["kraft_api/forge/errors.py", 7, 1], ["tests/forge/test_retry.py", 66, 0, "new"]] },
  "kraft-4e19": { mr: 123, description: "The docs chain's review prompts repeat the house style guide in full, so each review spends most of its context on rules it already has. Point the prompts at the style guide instead of copying it, and cut each one to what a docs review checks.",
    files: [["docs/chains/docs-review.md", 14, 52], ["docs/chains/prompts/style-check.md", 9, 31], ["docs/chains/prompts/links-check.md", 6, 18], ["docs/style/README.md", 4, 0]] },
  "kraft-91bc": { mr: 140, description: "The extension still reads the board with the old schema, so it shows no slices and misreads a stop's facts. Move its types and its board client to the current schema, and keep reading an older server's answers.",
    files: [["src/schema.ts", 64, 41], ["src/client/board.ts", 28, 17], ["src/views/itemTree.ts", 19, 8], ["src/test/schema.test.ts", 47, 6], ["package.json", 2, 2], ["src/test/board.test.ts", 15, 3, "late"]] },
  "kraft-e410": { mr: 136, description: "The sandbox keys in policy.yaml have no page of their own: people find them by reading the defaults file. Write a reference page that gives each key its default and what it changes, and link it from the policy guide.",
    files: [["docs/reference/policy-sandbox.md", 96, 0, "new"], ["docs/guides/policy.md", 8, 2], ["docs/reference/index.md", 1, 0], ["docs/_nav.yml", 2, 0]] },
  "kraft-2c77": { mr: 127, description: "A chain that fans out across a workspace's repos runs the linter once per member, and a member with no lint config fails the whole fan-out. Skip a member with nothing to lint, run the rest in parallel, and report each member's findings under its own name.",
    files: [["plugins/lint/fanout.py", 52, 18], ["plugins/lint/members.py", 23, 0, "new"], ["plugins/lint/report.py", 17, 9], ["plugins/lint/plugin.yaml", 4, 1], ["tests/lint/test_fanout.py", 61, 0, "new"], ["tests/lint/test_report.py", 10, 2, "late"]] },
  "kraft-f5d3": { mr: 118, description: "The console polls a session's log every second, which is slow to show a fast agent and busy when nothing changes. Try serving the log as server-sent events from the existing tail, and measure what it does to the console before deciding whether to keep it.",
    files: [["kraft/api/routes/log_stream.py", 58, 0, "new"], ["kraft/logs/tail.py", 21, 6], ["kraft/console/log_view.py", 17, 11], ["tests/api/test_log_stream.py", 39, 0, "new"]] },
  "kraft-3a90": { mr: 112, description: "Collect what changed since the last release into its notes: the new board filters, the forge retry and the removed poller flags. Each entry says what a person sees, and links the page that explains it.",
    files: [["docs/releases/latest.md", 61, 0, "new"], ["docs/releases/index.md", 3, 0], ["docs/guides/board.md", 4, 1]] },
  "kraft-1b6e": { mr: 104, description: "The --legacy-poller and --poll-interval flags have done nothing since the poller moved into the server, but the CLI still accepts them and its help still lists them. Remove both, say so once when a script passes them, and drop the code that read them.",
    files: [["kraft/cli/admin.py", 6, 31], ["kraft/cli/flags.py", 9, 22], ["kraft/poller/settings.py", 0, 18, "deleted"], ["tests/cli/test_flags.py", 19, 27], ["docs/cli.md", 2, 9]] },
  "kraft-88ca": { mr: 96, description: "The harness profiles are called deep, strong and fast, which says how hard each thinks but not which model runs. Name each after its model, keep the old names working as aliases, and update the library that names them.",
    files: [["kraft/harness/profiles.py", 24, 19], ["kraft/harness/aliases.py", 18, 0, "new"], ["config/library.yaml", 7, 7], ["tests/harness/test_profiles.py", 21, 9], ["tests/harness/test_aliases.py", 14, 0, "late", "new"]] },
  "kraft-c2d9": { mr: 92, description: "The docs site is light only, and its code blocks glare at night beside the dark dashboard. Add a dark theme that follows the reader's system setting, with a toggle that remembers their choice.",
    files: [["docs/assets/theme.css", 88, 4], ["docs/assets/toggle.js", 31, 0, "new"], ["docs/_layouts/page.html", 6, 1], ["docs/contributing/site.md", 9, 0]] },
  "kraft-0e7a": { mr: 87, description: "The extension takes whatever schema the SDK ships, so an SDK update can change what it reads without anyone noticing. Pin the schema the extension was built against, check it on start, and say plainly when the server answers with another.",
    files: [["src/sdk/pin.ts", 26, 0, "new"], ["src/sdk/check.ts", 34, 0, "new"], ["src/extension.ts", 9, 3], ["src/test/pin.test.ts", 41, 0, "new"], ["package.json", 3, 1]] },
  "kraft-6fb3": { mr: 83, description: "The intake poller runs inside the API process, so a slow forge call holds up requests and a restart of the API drops a poll halfway. Run the poller as its own process with its own health check, and have the API only read what it wrote.",
    files: [["kraft_api/intake/poller.py", 31, 54], ["kraft_api/intake/worker.py", 72, 0, "new"], ["kraft_api/app.py", 3, 19], ["kraft_api/health.py", 14, 2], ["deploy/intake.service", 18, 0, "new"], ["tests/intake/test_worker.py", 58, 0, "new"]] },
  "kraft-41d8": { mr: 79, description: "kraft view logs -n 20 prints 19 lines when the log ends with a newline, because the tail counts the empty string after the last one. Count lines, not separators, and cover both endings in the tests.",
    files: [["kraft/logs/tail.py", 5, 3], ["tests/logs/test_tail.py", 22, 1]] },
  "kraft-7c40": { mr: 144, description: "Review threads hold most of the reasoning about a change, but search never sees them. Index each thread's comments as a document of its item when the thread changes, and drop them when the item is abandoned.",
    files: [["kraft_api/index/threads.py", 49, 0, "new"], ["kraft_api/index/service.py", 12, 2], ["kraft_api/routes/threads.py", 6, 1], ["tests/index/test_threads.py", 53, 0, "new"], ["tests/index/test_service.py", 8, 1, "late"]] },
  "kraft-b2e7": { mr: 138, description: "The console shows a worker's log only once its session ends. Follow the log while the session runs, in a pane that keeps up with the agent and holds its place when you scroll back.",
    files: [["kraft/console/log_pane.py", 64, 12], ["kraft/console/app.py", 8, 2], ["kraft/logs/follow.py", 33, 0, "new"], ["tests/console/test_log_pane.py", 44, 0, "new"], ["tests/console/test_app.py", 6, 1, "late"]] },
  "kraft-e3c1": { mr: 134, description: "In a checkout used in place, the search plugin rebuilds its index into a temp folder on every run and throws it away. Keep the index in the checkout's own .kraft folder, reuse it while the commit it was built from is unchanged, and rebuild only what moved.",
    files: [["plugins/search/index_store.py", 47, 9], ["plugins/search/plugin.py", 11, 4], ["plugins/search/staleness.py", 29, 0, "new"], ["tests/search/test_index_store.py", 52, 0, "new"]] },
  "kraft-6a2f": { mr: 146, description: "The extension's settings are listed in the order they were added, three of them say almost the same thing, and two have no description. Group them, merge the duplicates, and give every setting a line that says what it changes.",
    files: [["package.json", 46, 38], ["src/settings.ts", 21, 14], ["src/migrateSettings.ts", 27, 0, "new"], ["src/test/settings.test.ts", 18, 4], ["README.md", 9, 6, "late"]] },
  "kraft-9a02": { mr: 72, description: "The default chain's prompts grew a paragraph at every fix, and the implementer's now repeats what its skill already says. Cut each prompt back to what its task needs, and move the shared rules into one steering file.",
    files: [["plugins/chains/default/prompts.yaml", 18, 64], ["plugins/chains/steering/shared.md", 27, 0, "new"], ["plugins/chains/default/chain.yaml", 3, 3], ["tests/chains/test_prompts.py", 12, 4]] },
  "kraft-d7e4": { mr: 64, description: "kraft admin reload rereads the library, policy.yaml and intake.yaml without a restart, but the docs mention it only in passing. Give it a section in the CLI reference that says what it rereads, what it leaves alone, and when to restart instead.",
    files: [["docs/cli/admin.md", 34, 1], ["docs/guides/configuration.md", 6, 2], ["docs/cli/index.md", 1, 0]] },
  "kraft-5c31": { mr: 61, description: "The webhook sender still builds the v0 payload beside the current one for receivers that asked for it, and none has in months. Remove the v0 builder and its setting, and refuse a subscription that still names it with a message that says what to send instead.",
    files: [["kraft_api/webhooks/payloads.py", 4, 61], ["kraft_api/webhooks/subscribe.py", 11, 6], ["kraft_api/settings.py", 0, 7, "deleted"], ["tests/webhooks/test_payloads.py", 6, 48]] },
};

/** By title: what live motion refiles (motion.ts FRESH_TITLES), so a walked item has a story of its own too, in the repo
 *  its files are in. Each filing of a story after its first opens its own merge request (motion.ts `refile`). Only the
 *  first filed item's round of tests fails (motion.ts `red`), so only its story has a file verification's round adds. */
export const FRESH_CONTENT: Record<string, Content> = {
  "Cache the forge's merge request lookups": { mr: 170, repo: "kraft-core", description: "Every forge task looks up the item's merge request again, so one node can ask the forge the same question five times. Cache the lookup per item for the length of a node, and clear it when the merge request changes.",
    files: [["kraft/forge/mr_cache.py", 38, 0, "new"], ["kraft/forge/run.py", 14, 6], ["tests/forge/test_mr_cache.py", 46, 0, "new"], ["tests/forge/test_run.py", 7, 2, "late"]] },
  "Show each session's model in the log header": { mr: 171, repo: "kraft-api", description: "A session's log does not say which model ran it, so comparing two runs means opening each session's details. Put the model and the harness in the log header, beside its start time.",
    files: [["kraft_api/routes/logs.py", 12, 3], ["kraft_api/logs/header.py", 19, 4], ["tests/logs/test_header.py", 27, 0, "new"], ["tests/logs/test_routes.py", 5, 1]] },
  "Retry a flaky CI poll once before stopping": { mr: 172, repo: "kraft-plugins", description: "A CI poll that times out once stops the item, though the next poll would have answered. Retry a timed-out poll once after a short wait, and stop only when the retry fails too.",
    files: [["plugins/ci/poll.py", 23, 7], ["plugins/ci/backoff.py", 16, 0, "new"], ["tests/ci/test_poll.py", 34, 2], ["tests/ci/test_backoff.py", 11, 0, "new"]] },
  "Add a --since filter to kraft view events": { mr: 173, repo: "kraft-core", description: "kraft view events lists an item's whole history, which for a long-running item is hundreds of lines; --after takes only an event number. Add a --since flag that takes a time and lists only the events after it.",
    files: [["kraft/cli/view.py", 18, 2], ["kraft/events/since.py", 24, 0, "new"], ["tests/cli/test_view_events.py", 37, 0, "new"], ["docs/cli/view.md", 6, 1], ["tests/events/test_since.py", 9, 0, "new"]] },
  "Warn when a gate names no artifact": { mr: 174, repo: "kraft-core", description: "A gate whose node produces nothing shows an empty document pane and gives its reviewer nothing to approve. Warn when the chain loads, name the gate, and say which task should produce its document.",
    files: [["kraft/templates/lint.py", 21, 2], ["kraft/executor/gates.py", 9, 3], ["tests/templates/test_lint.py", 31, 0, "new"], ["tests/executor/test_gates.py", 6, 0]] },
};

/** The item's merge request on its repo's forge (NG_REPOS): a GitHub pull request, or a GitLab merge request on the
 *  host of the repo's forge account, or of the account that is the default for GitLab when it names none (ngForges.ts),
 *  as the forge's own `web_url` is shaped (`adapters/forge/glab.py`). */
export function mrOf(repo: string, number: number) {
  const name = repo.split("/").pop()!;
  const r = NG_REPOS.find((x) => x.id === name);
  if (r?.forge !== "gitlab") return { number, url: `https://github.com/acme/${name}/pull/${number}` };
  const accounts = Object.values(forgesFixture(new Date(NG_NOW)));
  const account = (r.forge_account && forgesFixture(new Date(NG_NOW))[r.forge_account]) || accounts.find((a) => a.forge === "gitlab" && a.default_for_host)!;
  return { number, url: `${account.web_url}/${r.project}/-/merge_requests/${number}` };
}

/** The plan's tasks, as the plan document lists them and the implementer reports them (`plan_progress`): one for each
 *  file it changes, in its order (not those verification's round added), then a check. */
export const planTasks = (c: Content) => [...c.files.filter((f) => !isLate(f)).map(([p]) => `Change ${p}`), "Check it"];
/** What an item's implementer reports (`plan_progress`): its plan's tasks, the sample's for an item with no content, and
 *  none on a chain with no plan (`frozen`, its frozen chain or nodes, names no task that produces one), where
 *  `report_progress` answers 400 "plan has no '## Task N' headings" (`api/routes/lifecycle.py`). */
export const planOf = (frozen: string, content?: Content): string[] => (!frozen.includes('"produces":"plan"') ? [] : content ? planTasks(content) : [...PLAN]);

const done = (b: ItemBundle, node: string) => b.events.some((e) => e.type === "node_completed" && e.payload.node_id === node);
/** Whether verification's round has landed what it added: a repair of its fix loop has finished. Only the repair commits
 *  inside verification (`config/library.yaml`): its test scopes are a builtin and its code review reads only. */
const fixed = (b: ItemBundle) => b.sessions.some((s) => s.hook_point === REPAIR && s.status === "done");
/** The item's files as far as it has gone: the plan's, and the ones verification's round added once a repair finished.
 *  What its diff shows at its end, and what a brief or merge request written after its implementation lists. */
export const itemFiles = (b: ItemBundle): File[] => b.content!.files.filter((f) => !isLate(f) || fixed(b));
/** The files the item has committed so far, path-sorted as `git diff --numstat` lists them: none before its first plan
 *  task, then each task's file once the implementer has started the next (Kraft commits after every task), and all of
 *  `itemFiles` once the implementation completed (`review.read_change` reads `base..HEAD`). */
export function changedFiles(b: ItemBundle): File[] {
  const files = b.content!.files;
  let out: File[];
  if (done(b, "implementation")) out = itemFiles(b);
  else {
    const started = Math.max(0, ...b.events.filter((e) => e.type === "plan_progress" && e.payload.node_id === "implementation").map((e) => Number(e.payload.task)));
    out = files.filter((f) => !isLate(f)).slice(0, Math.max(0, started - 1));
  }
  return [...out].sort((x, y) => (x[0] < y[0] ? -1 : 1));
}

const camelOf = (snake: string) => snake.replace(/_(\w)/g, (_, c) => c.toUpperCase());
const isTest = (path: string) => /(^|\/)tests?\//.test(path) || /\.test\.[jt]sx?$/.test(path) || /(^|\/)test_[^/]+\.py$/.test(path);
const baseOf = (path: string) => path.split("/").pop()!.replace(/\.[^.]+$/, "").replace(/\.test$/, "");
/** How a test at `from` imports the module at `to`: up from its own folder to the one they share, then down. */
function importPath(from: string, to: string) {
  const [a, b] = [from, to].map((p) => p.split("/").slice(0, -1));
  let k = 0;
  while (k < a.length && a[k] === b[k]) k++;
  return [...(a.length > k ? Array(a.length - k).fill("..") : ["."]), ...b.slice(k), baseOf(to)].join("/");
}
/** A file's lines by its kind: some it keeps (context), some it had, some it has, named after the file so it reads as
 *  a change to that file. A test imports the module of its name among `paths`, the item's files, where it has one; a
 *  page says what the item's brief (`description`) says. */
function template(path: string, title: string, repo: string, paths: string[], description: string): [string[], string[], string[]] {
  const base = baseOf(path);
  const snake = base.replace(/[^A-Za-z0-9]+/g, "_").toLowerCase();
  const camel = camelOf(snake);
  const ext = path.split(".").pop()!;
  const say = title.toLowerCase();
  if (ext === "py" && isTest(path)) {
    const what = snake.replace(/^test_/, "");
    return [[`"""Tests for ${what}."""`, "from acme.testing import run"], [`def test_${what}_todo():`, `    pass`],
      [`def test_${what}(tmp_path):`, `    """${title}."""`, "    got = run(tmp_path, limit=2)", "    assert got.ok", "    assert len(got.items) == 2"]];
  }
  if (ext === "py") return [[`"""${base} for acme."""`, "import logging"], [`def ${snake}(value):`, "    return value", `    # TODO: ${snake} ignores the limit`],
    [`def ${snake}(value, *, limit=None):`, `    """${title}."""`, "    if limit is not None and value > limit:", `        raise ValueError(f"${snake}: {value} > {limit}")`, "    logging.debug(\"%s ok\", value)", "    return value"]];
  const module = paths.find((p) => p !== path && !isTest(p) && baseOf(p) === base && p.split(".").pop() === ext);
  if (["ts", "tsx", "js"].includes(ext) && isTest(path)) return [[`import { describe, expect, it } from "vitest";`, `import { ${camel} } from "${module ? importPath(path, module) : `../${base}`}";`], [`it.todo("${base}");`],
    [`describe("${base}", () => {`, `  it("${say}", () => {`, `    expect(${camel}({ limit: 2 }).items).toHaveLength(2);`, "  });", "});"]];
  if (ext === "js") return [[`// ${base}`, "const root = document.documentElement;"], [`function ${camel}() {}`],
    [`/** ${title}. */`, `export function ${camel}(value) {`, `  root.dataset.${camel} = value;`, `  localStorage.setItem("${camel}", value);`, "}"]];
  if (["ts", "tsx"].includes(ext)) {
    const head = repo.endsWith("/kraft-vscode") ? `import * as vscode from "vscode";` : `import { log } from "./log";`;
    return [[`// ${base}`, head], [`export function ${camel}(value: unknown) {`, "  return value;", "}"],
      [`/** ${title}. */`, `export function ${camel}(value: unknown, limit?: number) {`, `  if (limit !== undefined && Number(value) > limit) throw new Error(\`${camel}: \${value} > \${limit}\`);`, "  return value;", "}"]];
  }
  const [first, ...rest] = description.split(/(?<=\.) /);
  if (ext === "md") {
    // The page a docs item is about says what its brief says; any other page has its heading and a line of its own.
    const words = base.replace(/[-_]/g, " ");
    const principal = paths.every(isDocsPath) && paths.find((p) => p.endsWith(".md")) === path;
    return [[`# ${words}`, ""], [`This page is a stub.`, "See the source for now."], principal ? [first, "", ...rest] : [`The ${words} page, updated for: ${title}.`]];
  }
  if (ext === "css") return [[`/* ${base} */`, ".page {"], ["  color: #222;", "  background: #fff;"],
    ["  color: var(--fg);", "  background: var(--bg);", "}", "", "@media (prefers-color-scheme: dark) {", "  :root { --fg: #e6e6e6; --bg: #111; }", "}"]];
  if (ext === "html") return [["<!doctype html>", `<html lang="en">`], ["<body>"], [`<body class="${snake}">`, `  <!-- ${title}. -->`, "  <main>{{ content }}</main>"]];
  // A manifest: the repo's own package, and a script line (no version: none of the demo's numbers is a release's).
  if (ext === "json") return [["{", `  "name": "${repo.split("/").pop()}",`], [`  "scripts": { "test": "vitest run" }`], [`  "scripts": { "test": "vitest run", "check": "tsc --noEmit" },`, `  "description": "${title}."`]];
  if (ext === "toml") return [["[project]", `name = "acme"`], [`dependencies = ["pydantic<2"]`], [`dependencies = ["pydantic>=2"]`]];
  if (ext === "service") return [["[Unit]", `Description=acme ${base}`], ["Type=simple"],
    ["After=network-online.target", "", "[Service]", `ExecStart=/usr/bin/acme-${base}`, "Restart=on-failure", "", "[Install]", "WantedBy=multi-user.target"]];
  return [[`# ${base}`], [`${snake}: old`], [`${snake}: ${say.replace(/[^a-z0-9]+/g, "-").slice(0, 40)}`, `${snake}_enabled: true`]];
}

/** `rows`, each led by its diff mark, under the `@@` header their marks count: old = context + removed, new =
 *  context + added. A new file starts from nothing, a deleted one ends at nothing. */
function patch(path: string, status: "new" | "deleted" | null, at: number, rows: string[]): string {
  const ctx = rows.filter((r) => r[0] === " ").length;
  const old = ctx + rows.filter((r) => r[0] === "-").length;
  const neu = ctx + rows.filter((r) => r[0] === "+").length;
  const head = status === "new" ? `new file mode 100644\nindex 0000000..9c8d7e6\n--- /dev/null\n+++ b/${path}`
    : status === "deleted" ? `deleted file mode 100644\nindex 3f2a1b0..0000000\n--- a/${path}\n+++ /dev/null`
    : `index 3f2a1b0..9c8d7e6 100644\n--- a/${path}\n+++ b/${path}`;
  const range = (start: number, n: number) => (n ? `${start},${n}` : "0,0");
  return `diff --git a/${path} b/${path}
${head}
@@ -${range(at, old)} +${range(at, neu)} @@
${rows.join("\n")}
`;
}

/** A file's hunk: its own rows where the item has them (`hunks`), else its template's, named after the file. */
export function hunk(f: File, title: string, repo: string, c: Content): string {
  const [path, ins, del] = f;
  const status = statusOf(f);
  const own = c.hunks?.[path];
  if (own) return patch(path, status, own.at, own.rows);
  const [ctx, olds, news] = template(path, title, repo, c.files.map(([p]) => p), c.description);
  // shortcut: a file shows at most its template's lines, not all it changed; enough to read what it is.
  if (status === "new") return patch(path, status, 1, [...ctx, ...news].slice(0, ins).map((l) => `+${l}`));
  if (status === "deleted") return patch(path, status, 1, [...ctx, ...olds].slice(0, del).map((l) => `-${l}`));
  return patch(path, status, 1, [...ctx.map((l) => ` ${l}`), ...olds.slice(0, del).map((l) => `-${l}`), ...news.slice(0, ins).map((l) => `+${l}`)]);
}

/** GET /diff for an item with content: what it changed is committed (`landed`), nothing is in flight. Its commits
 *  are `git log --oneline`'s, newest first: one for each plan task it finished (on a chain with no plan, one named after
 *  the item), then the fix a repair of verification's round made. */
export function contentDiff(b: ItemBundle) {
  const files = changedFiles(b);
  const sha = (k: number) => ((parseInt(b.item.id.slice(0, 7), 16) + k * 7919) % 0xfffffff).toString(16).padStart(7, "0");
  const tasks = planOf(JSON.stringify(nodesOf(b)), b.content);
  const own = files.filter((f) => !isLate(f));
  // A chain with no plan reports no task, so its implementer's work is one commit, named after the item.
  const work = tasks.length ? own.map((_, k) => `Task ${k + 1}: ${tasks[k]}`) : own.length ? [b.item.title] : [];
  const commits = [...work, ...(files.some(isLate) ? ["Fix what verification found"] : [])].map((subject, k) => `${sha(k + 1)} ${subject}`).reverse();
  return {
    work_item_id: b.item.id, base_ref: "main", files: [], diff: "", untracked: [], truncated: false,
    landed: {
      commits,
      files: files.map(([path, insertions, deletions]) => ({ path, insertions, deletions })),
      // Each patch ends in its own newline, so they follow one another with no blank line between (`artifacts.py`).
      diff: files.map((f) => hunk(f, b.item.title, b.item.repo, b.content!)).join(""),
      truncated: false,
    },
  };
}

/** The item's frozen chain's nodes (its `materialized_chain`, else its template's). */
const nodesOf = (b: ItemBundle): any[] => (b.item.materialized_chain ? JSON.parse(b.item.materialized_chain).chain.nodes : chainOf(b.item.chain_template)?.frozen.nodes ?? []);
/** Every kind the item's chain produces (a task's `produces`): what `GET /artifacts/{kind}` answers
 *  (`artifacts._produced_path`, which takes a gate's `artifact` too: every gate's here is a kind a task produces). */
export const chainKinds = (b: ItemBundle): Set<string> => new Set(nodesOf(b).flatMap((n) => [...JSON.stringify(n).matchAll(/"produces":"([^"]+)"/g)].map((m) => m[1])));
/** Whether the item's `kind` document is on disk: the node that produces it has completed, or a gate waits on it. Before
 *  then, with nothing indexed, `GET /artifacts/{kind}` has none (`artifacts.get_produced_artifact`). */
export const written = (b: ItemBundle, kind: string) =>
  b.events.some((e) => e.type === "node_completed" && e.payload.node_id === producedBy(b, kind).node_id) || (!!b.item.pending_gate && String(b.item.gate_artifact).endsWith(`/docs/${kind}.md`));
/** The node whose task produces `kind`, and that task's session's hook point: what the document's front matter names. */
export function producedBy(b: ItemBundle, kind: string): { node_id: string | null; hook_point: string | null } {
  const node = nodesOf(b).find((n) => n.kind !== "gate" && JSON.stringify(n).includes(`"produces":"${kind}"`))?.id ?? null;
  return { node_id: node, hook_point: b.sessions.filter((s) => s.node_id === node).at(-1)?.hook_point ?? null };
}

const LABEL: Record<string, string> = { spec: "Spec", plan: "Plan", chain_revision: "Chain revision", work_brief: "Work brief", review_brief: "Review brief" };
const words = (s: string) => s.replace(/_/g, " ");
/** What a session summary is called: its node and task, `Verification · code review`. */
const taskName = (hook: string | null | undefined, node: string | null | undefined) => {
  const name = words(node ?? "");
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} · ${words(String(hook ?? "").split(".").at(-1) ?? "")}`;
};
/** A gate document's title: its kind's label and the item's title, the heading `docBody` writes. */
export const gateTitle = (b: ItemBundle, kind: string) => `${LABEL[kind]}: ${b.item.title}`;

/** The item's documents, as the indexer has them (`documents_for_work_item`), newest indexed first. A gate's document
 *  (`items/<id>/docs/<kind>.md`) is indexed when the gate is approved (`_ingest_approved_gate_artifact`), at that
 *  approval's time, with no node, task or session: not when its node completes, and never for a chain revision Kraft
 *  passed itself because it changed nothing (`pass_unchanged_revision`, approved `by: kraft`). Each finished agent
 *  session's summary is indexed, one per path (an escalation thread's turns share theirs). A pending gate's document
 *  is not indexed: the documents route adds it (`_pending_gate_doc`). Ids are the item's own. */
export function itemDocs(b: ItemBundle): any[] {
  if (!b.content) return [];
  const it = b.item;
  const id = (k: number) => `${it.id.slice(0, 24)}${k.toString(16).padStart(8, "0")}`;
  const base = { work_item_id: it.id, repo: it.repo, attachment_kind: null };
  const out: any[] = [];
  nodesOf(b).filter((n) => n.kind === "gate" && n.artifact).forEach((g, k) => {
    const e = [...b.events].reverse().find((x) => x.type === "gate_approved" && x.payload.gate === g.id && x.payload.by !== "kraft");
    if (!e) return;
    out.push({ ...base, document_id: id(k + 1), title: gateTitle(b, g.artifact), kind: `${g.artifact}s`, source_kind: "artifact", path: `items/${it.id}/docs/${g.artifact}.md`,
      node_id: null, hook_point: null, worker_session_id: null, attempt: null, round: null, session_status: null, session_work_item_id: null, indexed_at: e.created_at });
  });
  const refs = new Map<string, number>();
  // An escalation turn's summary is indexed at its thread's path when it exits; once a later turn takes that path its
  // own copy moves to its session's folder, which nothing indexes. The server files any session that wrote a summary,
  // whatever its status; the status filter holds because the demo's failed and capped sessions write none.
  const indexed = (s: any) => s.session_summary_ref && ["done", "done_with_concerns"].includes(s.status) && (s.hook_point !== "escalation" || /\/sessions\/escalation-\d+\/out\/summary\.md$/.test(s.session_summary_ref));
  b.sessions.forEach((s, n) => { if (indexed(s) && !refs.has(s.session_summary_ref)) refs.set(s.session_summary_ref, n); });
  for (const [path, first] of refs) {
    const s = b.sessions.filter((x) => x.session_summary_ref === path).at(-1)!;
    out.push({ ...base, document_id: id(0x100 + first), title: `${taskName(s.hook_point, s.node_id)}: ${it.title}`, kind: "sessions", source_kind: "session_summary", path,
      node_id: s.node_id, hook_point: s.hook_point, worker_session_id: s.id, attempt: s.attempt, round: s.round, session_status: s.status, session_work_item_id: it.id, indexed_at: s.exited_at ?? s.created_at });
  }
  return out.sort((x, y) => (x.indexed_at < y.indexed_at ? 1 : x.indexed_at > y.indexed_at ? -1 : 0));
}

/** The document a pending gate is about, as the list shows it before approval indexes it (`_pending_gate_doc`): a
 *  synthetic id, listed now, with no node, task or session; search never finds it. Null when no gate waits on one. */
export function pendingDoc(b: ItemBundle, now: string): any | null {
  const it = b.item;
  const kind = b.content && it.pending_gate && /\/docs\/([^/]+)\.md$/.exec(it.gate_artifact ?? "")?.[1];
  if (!kind) return null;
  return { document_id: `gate:${it.id}:${it.pending_gate}`, repo: it.repo, title: gateTitle(b, kind), kind: `${kind}s`, source_kind: "artifact", path: it.gate_artifact,
    indexed_at: now, node_id: null, hook_point: null, worker_session_id: null, attachment_kind: null, attempt: null, round: null, session_status: null };
}

/** The item's documents after a transition: `itemDocs` again, but a path already filed with a body of its own
 *  (`indexSummary`'s, in `docText`) keeps that document, and one `itemDocs` does not make stays. */
export function mergeDocs(had: any[] = [], fresh: any[], docText: Record<string, string> = {}): any[] {
  const own = had.filter((d) => docText[d.document_id] != null);
  return [...own, ...had.filter((d) => !own.includes(d) && !fresh.some((f) => f.path === d.path)), ...fresh.filter((f) => !own.some((d) => d.path === f.path))];
}

/** A document's text: the template for its kind, written from the item's brief and files. A spec, a plan and a chain
 *  revision are written before anything changed, so they name paths only; the plan leaves out what verification's
 *  round adds. A work brief and a review brief are written after, with each file's counts. `mr_metas` is the merge
 *  request's metadata file, front matter (its title and labels) and description. */
export function docBody(b: ItemBundle, d: { kind: string; hook_point?: string | null; node_id?: string | null }): string {
  const it = b.item;
  const c = b.content!;
  const [first, ...rest] = c.description.split(/(?<=\.) /);
  const files = c.files;
  const docsOnly = files.every(([p]) => isDocsPath(p));
  // A brief or a merge request is written after the implementation: it lists what the item's diff shows.
  const landed = itemFiles(b);
  const counted = landed.map(([p, i, dl]) => `- \`${p}\` (+${i} −${dl})`).join("\n");
  const paths = files.filter((f) => !isLate(f)).map(([p]) => `- \`${p}\``).join("\n");
  const top = [...landed].sort((x, y) => y[1] + y[2] - x[1] - x[2])[0];
  const check = docsOnly ? "The site builds, and every link on a changed page resolves." : "The repo's tests pass, the new behaviour has a test of its own, and the linter is clean.";
  switch (d.kind) {
    case "specs": return `# ${gateTitle(b, "spec")}\n\n## Problem\n\n${first}\n\n## Change\n\n${rest.join(" ") || first}\n\n## Touches\n\n${paths}\n\n## Done when\n\n- ${check}\n`;
    case "plans": return `# ${gateTitle(b, "plan")}\n\n${first}\n\n${planTasks(c).map((task, k) => `### Task ${k + 1}: ${task}`).join("\n\n")}\n\n${check}\n`;
    case "chain_revisions": return `# ${gateTitle(b, "chain_revision")}\n\n${first}\n\n## Nodes\n\nThe rest of the chain fits the approved spec and plan, so it stays as it is.\n\n- Implementation covers what the plan names:\n\n${paths}\n\n- ${docsOnly ? "There is no verification node: the docs check runs in the merge request's CI." : "Verification runs the repo's tests and the docs check."}\n`;
    case "work_briefs": return `# ${gateTitle(b, "work_brief")}\n\n## What changed\n\n${c.description}\n\n## Files\n\n${counted}\n\n## How it was checked\n\n- ${check}\n`;
    case "review_briefs": return `# ${gateTitle(b, "review_brief")}\n\n## Read first\n\n\`${top[0]}\`, the largest change (+${top[1]} −${top[2]}).\n\n## Summary\n\n${c.description}\n\n## Risk\n\n- ${first}\n- Nothing outside the ${landed.length} files below changed.\n\n${counted}\n`;
    case "mr_metas": {
      const label = docsOnly ? "documentation" : /^(Fix|Retry)\b/.test(it.title) ? "bug" : "enhancement";
      return `---\ntitle: ${it.title}\nlabels: [${label}]\nassignees: []\nreviewers: []\n---\n\n${c.description}\n\n## Changes\n\n${counted}\n\n## How it was checked\n\n${check}\n\nCloses ${it.bead_id ?? it.id}.\n`;
    }
    default: {
      const task = words(String(d.hook_point ?? "").split(".").at(-1) ?? "");
      const gate = nodesOf(b).find((n) => n.id === d.node_id && n.kind === "gate");
      // The implementer's own work is the plan's files, not those verification's round added after it.
      const own = files.filter((f) => !isLate(f)).map(([p, i, dl]) => `- \`${p}\` (+${i} −${dl})`).join("\n");
      const tasks = planOf(JSON.stringify(nodesOf(b)), c).length;
      const did = task === "implement" ? `${tasks ? "Worked through the plan task by task and committed each." : "Made the change the spec describes and committed it."}\n\n${own}`
        // A gate's reviewer and the writer of a spec, plan or chain revision run before any code changed.
        : gate ? `Read the ${words(gate.artifact ?? "document")} before the person decides: it says what changes, and how to tell it is done.`
        : ["spec", "plan", "chain_revision"].includes(d.node_id ?? "") ? `Wrote the ${words(d.node_id!)} from the item's brief.`
        : `Ran ${task} for ${d.node_id ?? "the item"} on the ${files.filter((f) => !isLate(f)).length} files the ${tasks ? "plan" : "spec"} names.`;
      return `# ${taskName(d.hook_point, d.node_id)}: ${it.title}\n\n${first}\n\n## What this session did\n\n${did}\n\n## Next\n\n${check}\n`;
    }
  }
}
