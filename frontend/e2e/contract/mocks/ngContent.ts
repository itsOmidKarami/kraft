import type { ItemBundle } from "./fixtures";

/**
 * What each board item is about (ngBoard.ts ROWS and ARCHIVED, by bead; live motion's refiled items, by title): its
 * brief, the files it changes, its merge request's number, and from those its diff, its documents and their text.
 * `ngBoard.ts` hangs an item's entry on its bundle (`content`) in the default sample only; the item page's samples,
 * the long and many variants, and an item a visitor files carry none, and keep the generic data in fixtures.ts.
 */

/** A file the item changes: its path, lines added, lines removed, and `late` when verification's round added it. */
export type File = [path: string, insertions: number, deletions: number, late?: "late"];
export interface Content { description: string; files: File[]; mr: number }

/** By bead: the 24 board rows. Merge request numbers count up with the item's age; none is in 150-159, which the
 *  walk gives an item it has no entry for (`transitions.mrOpened`). */
export const CONTENT: Record<string, Content> = {
  "kraft-cb59": { mr: 142, description: "Search re-embeds every candidate document on every query, so a large repository waits two to four seconds for each search. Cache the embeddings keyed by each document's content hash, drop an entry when reindex sees a new hash, and keep the cache to a fixed size. The search API stays as it is.",
    files: [["search/cache.py", 86, 4], ["search/reindex.py", 22, 9], ["search/embed.py", 14, 11], ["search/config.py", 6, 0], ["tests/test_cache.py", 74, 0], ["tests/test_reindex.py", 18, 3, "late"], ["docs/search.md", 12, 2]] },
  "kraft-7d21": { mr: 132, description: "test_retry_after_crash fails about one run in twenty on CI: it sleeps for a fixed 50 ms and expects the pool to have replaced the crashed worker by then. Wait on the pool's restart event instead of a timer, and give the pool a hook the test can await.",
    files: [["kraft/workers/pool.py", 18, 6], ["kraft/workers/events.py", 9, 0], ["tests/workers/test_pool.py", 23, 17], ["tests/workers/conftest.py", 11, 2, "late"]] },
  "kraft-a3f0": { mr: 129, description: "Clients of the public API cannot tell how close they are to the rate limit until a request fails with 429. Send X-RateLimit-Limit, X-RateLimit-Remaining and X-RateLimit-Reset on every response, read from the limiter that already counts requests.",
    files: [["kraft_api/middleware/ratelimit.py", 41, 7], ["kraft_api/limiter.py", 16, 3], ["kraft_api/app.py", 4, 1], ["tests/test_ratelimit_headers.py", 58, 0], ["docs/api/rate-limits.md", 19, 2], ["tests/test_limiter.py", 9, 2, "late"]] },
  "kraft-58e2": { mr: 121, description: "The config loader still calls the old parse_obj and validator APIs, which warn on every start and are on their way out. Move each model to model_validate and field_validator, keep the error messages the CLI prints today, and drop the compatibility shim.",
    files: [["kraft/config/loader.py", 48, 39], ["kraft/config/models.py", 72, 66], ["kraft/config/compat.py", 0, 41], ["tests/config/test_loader.py", 27, 14], ["pyproject.toml", 1, 1], ["tests/config/test_errors.py", 12, 5, "late"]] },
  "kraft-b108": { mr: 125, description: "A burst of forge calls in a busy hour gets 429 Too Many Requests, and the task fails on the first one. Retry a 429 after the Retry-After the forge sends, up to three times, and fail with the forge's own message after that.",
    files: [["kraft_api/forge/client.py", 37, 8], ["kraft_api/forge/retry.py", 44, 0], ["kraft_api/forge/errors.py", 7, 1], ["tests/forge/test_retry.py", 66, 0], ["tests/forge/test_client.py", 12, 4, "late"]] },
  "kraft-4e19": { mr: 123, description: "The docs chain's review prompts repeat the house style guide in full, so each review spends most of its context on rules it already has. Point the prompts at the style guide instead of copying it, and cut each one to what a docs review checks.",
    files: [["docs/chains/docs-review.md", 14, 52], ["docs/chains/prompts/style-check.md", 9, 31], ["docs/chains/prompts/links-check.md", 6, 18], ["docs/style/README.md", 4, 0]] },
  "kraft-91bc": { mr: 140, description: "The extension still reads the board with the old schema, so it shows no slices and misreads a stop's facts. Move its types and its board client to the current schema, and keep reading an older server's answers.",
    files: [["src/schema.ts", 64, 41], ["src/client/board.ts", 28, 17], ["src/views/itemTree.ts", 19, 8], ["src/test/schema.test.ts", 47, 6], ["package.json", 2, 2], ["src/test/board.test.ts", 15, 3, "late"]] },
  "kraft-e410": { mr: 136, description: "The sandbox keys in policy.yaml have no page of their own: people find them by reading the defaults file. Write a reference page that gives each key its default and what it changes, and link it from the policy guide.",
    files: [["docs/reference/policy-sandbox.md", 96, 0], ["docs/guides/policy.md", 8, 2], ["docs/reference/index.md", 1, 0], ["docs/_nav.yml", 2, 0]] },
  "kraft-2c77": { mr: 127, description: "A chain that fans out across a workspace's repos runs the linter once per member, and a member with no lint config fails the whole fan-out. Skip a member with nothing to lint, run the rest in parallel, and report each member's findings under its own name.",
    files: [["plugins/lint/fanout.py", 52, 18], ["plugins/lint/members.py", 23, 0], ["plugins/lint/report.py", 17, 9], ["plugins/lint/plugin.yaml", 4, 1], ["tests/lint/test_fanout.py", 61, 0], ["tests/lint/test_report.py", 10, 2, "late"]] },
  "kraft-f5d3": { mr: 118, description: "The console polls a session's log every second, which is slow to show a fast agent and busy when nothing changes. Try serving the log as server-sent events from the existing tail, and measure what it does to the console before deciding whether to keep it.",
    files: [["kraft/api/routes/log_stream.py", 58, 0], ["kraft/logs/tail.py", 21, 6], ["kraft/console/log_view.py", 17, 11], ["tests/api/test_log_stream.py", 39, 0]] },
  "kraft-3a90": { mr: 112, description: "Collect what changed since the last release into its notes: the new board filters, the forge retry and the removed poller flags. Each entry says what a person sees, and links the page that explains it.",
    files: [["docs/releases/latest.md", 61, 0], ["docs/releases/index.md", 3, 0], ["docs/guides/board.md", 4, 1]] },
  "kraft-1b6e": { mr: 104, description: "The --legacy-poller and --poll-interval flags have done nothing since the poller moved into the server, but the CLI still accepts them and its help still lists them. Remove both, say so once when a script passes them, and drop the code that read them.",
    files: [["kraft/cli/admin.py", 6, 31], ["kraft/cli/flags.py", 9, 22], ["kraft/poller/settings.py", 0, 18], ["tests/cli/test_flags.py", 19, 27], ["docs/cli.md", 2, 9], ["tests/cli/test_admin.py", 4, 11, "late"]] },
  "kraft-88ca": { mr: 96, description: "The harness profiles are called fast, default and strong, which says nothing about what each one runs. Rename them after the model tier they pick, keep the old names working as aliases for a while, and update the library that names them.",
    files: [["kraft/harness/profiles.py", 24, 19], ["kraft/harness/aliases.py", 18, 0], ["config/library.yaml", 7, 7], ["tests/harness/test_profiles.py", 21, 9], ["tests/harness/test_aliases.py", 14, 0, "late"]] },
  "kraft-c2d9": { mr: 92, description: "The docs site is light only, and its code blocks glare at night beside the dark dashboard. Add a dark theme that follows the reader's system setting, with a toggle that remembers their choice.",
    files: [["docs/assets/theme.css", 88, 4], ["docs/assets/toggle.js", 31, 0], ["docs/_layouts/page.html", 6, 1], ["docs/contributing/site.md", 9, 0]] },
  "kraft-0e7a": { mr: 87, description: "The extension takes whatever schema the SDK ships, so an SDK update can change what it reads without anyone noticing. Pin the schema the extension was built against, check it on start, and say plainly when the server answers with another.",
    files: [["src/sdk/pin.ts", 26, 0], ["src/sdk/check.ts", 34, 0], ["src/extension.ts", 9, 3], ["src/test/pin.test.ts", 41, 0], ["package.json", 3, 1], ["src/test/check.test.ts", 12, 2, "late"]] },
  "kraft-6fb3": { mr: 83, description: "The intake poller runs inside the API process, so a slow forge call holds up requests and a restart of the API drops a poll halfway. Run the poller as its own process with its own health check, and have the API only read what it wrote.",
    files: [["kraft_api/intake/poller.py", 31, 54], ["kraft_api/intake/worker.py", 72, 0], ["kraft_api/app.py", 3, 19], ["kraft_api/health.py", 14, 2], ["deploy/intake.service", 18, 0], ["tests/intake/test_worker.py", 58, 0], ["tests/intake/test_poller.py", 9, 24, "late"]] },
  "kraft-41d8": { mr: 79, description: "kraft view logs -n 20 prints 19 lines when the log ends with a newline, because the tail counts the empty string after the last one. Count lines, not separators, and cover both endings in the tests.",
    files: [["kraft/logs/tail.py", 5, 3], ["tests/logs/test_tail.py", 22, 1], ["tests/logs/test_tail_cli.py", 9, 0, "late"]] },
  "kraft-7c40": { mr: 144, description: "Review threads hold most of the reasoning about a change, but search never sees them. Index each thread's comments as a document of its item when the thread changes, and drop them when the item is abandoned.",
    files: [["kraft_api/index/threads.py", 49, 0], ["kraft_api/index/service.py", 12, 2], ["kraft_api/routes/threads.py", 6, 1], ["tests/index/test_threads.py", 53, 0], ["tests/index/test_service.py", 8, 1, "late"]] },
  "kraft-b2e7": { mr: 138, description: "The console shows a worker's log only once its session ends. Follow the log while the session runs, in a pane that keeps up with the agent and holds its place when you scroll back.",
    files: [["kraft/console/log_pane.py", 64, 12], ["kraft/console/app.py", 8, 2], ["kraft/logs/follow.py", 33, 0], ["tests/console/test_log_pane.py", 44, 0], ["tests/console/test_app.py", 6, 1, "late"]] },
  "kraft-e3c1": { mr: 134, description: "In a checkout used in place, the search plugin rebuilds its index into a temp folder on every run and throws it away. Keep the index in the checkout's own .kraft folder, reuse it while the commit it was built from is unchanged, and rebuild only what moved.",
    files: [["plugins/search/index_store.py", 47, 9], ["plugins/search/plugin.py", 11, 4], ["plugins/search/staleness.py", 29, 0], ["tests/search/test_index_store.py", 52, 0], ["tests/search/test_staleness.py", 13, 0, "late"]] },
  "kraft-6a2f": { mr: 146, description: "The extension's settings are listed in the order they were added, three of them say almost the same thing, and two have no description. Group them, merge the duplicates, and give every setting a line that says what it changes.",
    files: [["package.json", 46, 38], ["src/settings.ts", 21, 14], ["src/migrateSettings.ts", 27, 0], ["src/test/settings.test.ts", 18, 4], ["README.md", 9, 6, "late"]] },
  "kraft-9a02": { mr: 72, description: "The default chain's prompts grew a paragraph at every fix, and the implementer's now repeats what its skill already says. Cut each prompt back to what its task needs, and move the shared rules into one steering file.",
    files: [["plugins/chains/default/prompts.yaml", 18, 64], ["plugins/chains/steering/shared.md", 27, 0], ["plugins/chains/default/chain.yaml", 3, 3], ["tests/chains/test_prompts.py", 12, 4], ["tests/chains/test_steering.py", 7, 0, "late"]] },
  "kraft-d7e4": { mr: 64, description: "kraft admin reload rereads the library, policy.yaml and intake.yaml without a restart, but the docs mention it only in passing. Give it a section in the CLI reference that says what it rereads, what it leaves alone, and when to restart instead.",
    files: [["docs/cli/admin.md", 34, 1], ["docs/guides/configuration.md", 6, 2], ["docs/cli/index.md", 1, 0]] },
  "kraft-5c31": { mr: 61, description: "The webhook sender still builds the v0 payload beside the current one for receivers that asked for it, and none has in months. Remove the v0 builder and its setting, and refuse a subscription that still names it with a message that says what to send instead.",
    files: [["kraft_api/webhooks/payloads.py", 4, 61], ["kraft_api/webhooks/subscribe.py", 11, 6], ["kraft_api/settings.py", 0, 7], ["tests/webhooks/test_payloads.py", 6, 48], ["tests/webhooks/test_subscribe.py", 15, 2, "late"]] },
};

/** By title: what live motion refiles (motion.ts FRESH_TITLES), so a walked item has a story of its own too. */
export const FRESH_CONTENT: Record<string, Content> = {
  "Cache the forge's merge request lookups": { mr: 170, description: "Every forge task looks up the item's merge request again, so one node can ask the forge the same question five times. Cache the lookup per item for the length of a node, and clear it when the merge request changes.",
    files: [["kraft/forge/mr_cache.py", 38, 0], ["kraft/forge/run.py", 14, 6], ["tests/forge/test_mr_cache.py", 46, 0], ["tests/forge/test_run.py", 7, 2, "late"]] },
  "Show each session's model in the log header": { mr: 171, description: "A session's log does not say which model ran it, so comparing two runs means opening each session's details. Put the model and the harness in the log header, beside its start time.",
    files: [["kraft_api/routes/logs.py", 12, 3], ["kraft_api/logs/header.py", 19, 4], ["tests/logs/test_header.py", 27, 0], ["tests/logs/test_routes.py", 5, 1, "late"]] },
  "Retry a flaky CI poll once before stopping": { mr: 172, description: "A CI poll that times out once stops the item, though the next poll would have answered. Retry a timed-out poll once after a short wait, and stop only when the retry fails too.",
    files: [["plugins/ci/poll.py", 23, 7], ["plugins/ci/backoff.py", 16, 0], ["tests/ci/test_poll.py", 34, 2], ["tests/ci/test_backoff.py", 11, 0, "late"]] },
  "Add a --since filter to kraft view events": { mr: 173, description: "kraft view events lists an item's whole history, which for a long-running item is hundreds of lines. Document the --since flag, which takes a time or an event number, with an example of each.",
    files: [["docs/cli/view.md", 22, 3], ["docs/guides/events.md", 8, 1], ["docs/cli/events-since.md", 14, 0]] },
  "Warn when a gate names no artifact": { mr: 174, description: "A gate whose node produces nothing shows an empty document pane and gives its reviewer nothing to approve. Warn when the chain loads, name the gate, and say which task should produce its document.",
    files: [["kraft/templates/lint.py", 21, 2], ["kraft/executor/gates.py", 9, 3], ["tests/templates/test_lint.py", 31, 0], ["tests/executor/test_gates.py", 6, 0, "late"]] },
};

export const mrOf = (repo: string, number: number) => ({ number, url: `https://github.com/acme/${repo.split("/").pop()}/pull/${number}` });

const done = (b: ItemBundle, node: string) => b.events.some((e) => e.type === "node_completed" && e.payload.node_id === node);
/** The files the item has changed so far: none until its implementation completes, then the implementation's, and
 *  once verification has completed the files its round added too. */
export const changedFiles = (b: ItemBundle): File[] =>
  done(b, "implementation") ? b.content!.files.filter((f) => !f[3] || done(b, "verification")) : [];

/** A file's hunk from a template by its kind, named after the file, so it reads as a change to that file. */
export function hunk([path, ins, del]: File, title: string): string {
  const base = path.split("/").pop()!.replace(/\.[^.]+$/, "");
  const snake = base.replace(/[^A-Za-z0-9]+/g, "_").toLowerCase();
  const camel = snake.replace(/_(\w)/g, (_, c) => c.toUpperCase());
  const ext = path.split(".").pop()!;
  const [ctx, olds, news]: [string[], string[], string[]] =
    ext === "py" ? [[`"""${base} for acme."""`, "import logging"], [`def ${snake}(value):`, "    return value", `    # TODO: ${snake} ignores the limit`],
      [`def ${snake}(value, *, limit=None):`, `    """${title}."""`, "    if limit is not None and value > limit:", `        raise ValueError(f"${snake}: {value} > {limit}")`, "    logging.debug(\"%s ok\", value)", "    return value"]]
    : ["ts", "tsx", "js"].includes(ext) ? [[`// ${base}`, `import * as vscode from "vscode";`], [`export function ${camel}(value: unknown) {`, "  return value;", "}"],
      [`/** ${title}. */`, `export function ${camel}(value: unknown, limit?: number) {`, `  if (limit !== undefined && Number(value) > limit) throw new Error(\`${camel}: \${value} > \${limit}\`);`, "  return value;", "}"]]
    : ext === "md" ? [[`# ${base.replace(/[-_]/g, " ")}`, ""], [`This page is a stub.`, "See the source for now."],
      [`${title}.`, "", `## What it does`, "", `Each setting below says what it changes, and what it is set to when you leave it out.`]]
    : [[`# ${base}`], [`${snake}: old`], [`${snake}: ${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}`, `${snake}_enabled: true`]];
  // shortcut: a file shows at most its template's lines, not all it changed; enough to read what it is.
  const minus = olds.slice(0, del).map((l) => `-${l}`);
  const plus = news.slice(0, ins).map((l) => `+${l}`);
  return `diff --git a/${path} b/${path}
index 3f2a1b0..9c8d7e6 100644
--- a/${path}
+++ b/${path}
@@ -1,${ctx.length + minus.length} +1,${ctx.length + plus.length} @@
${[...ctx.map((l) => ` ${l}`), ...minus, ...plus].join("\n")}
`;
}

/** GET /diff for an item with content: what it changed is committed (`landed`), nothing is in flight. */
export function contentDiff(b: ItemBundle) {
  const files = changedFiles(b);
  const sha = (k: number) => ((parseInt(b.item.id.slice(0, 7), 16) + k * 7919) % 0xfffffff).toString(16).padStart(7, "0");
  return {
    work_item_id: b.item.id, base_ref: "main", files: [], diff: "", untracked: [], truncated: false,
    landed: {
      commits: files.length ? [`${sha(1)} ${b.item.title}`, ...(files.some((f) => f[3]) ? [`${sha(2)} Fix what verification found`] : [])] : [],
      files: files.map(([path, insertions, deletions]) => ({ path, insertions, deletions })),
      diff: files.map((f) => hunk(f, b.item.title)).join("\n"),
      truncated: false,
    },
  };
}

/** The node whose completion produces each gate document (`produces`), and the document's kind. */
export const PRODUCES: Record<string, string> = { spec: "spec", plan: "plan", chain_revision: "chain_revision", work_brief: "work_brief", work_item_summary: "review_brief" };
const LABEL: Record<string, string> = { spec: "Spec", plan: "Plan", chain_revision: "Chain revision", work_brief: "Work brief", review_brief: "Review brief" };
const words = (s: string) => s.replace(/_/g, " ");
/** What a session summary is called: its node and task, `Verification · code review`. */
const taskName = (hook: string | null | undefined, node: string | null | undefined) => {
  const name = words(node ?? "");
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} · ${words(String(hook ?? "").split(".").at(-1) ?? "")}`;
};

/** The item's documents, as the indexer has them: each gate document a node it completed produced
 *  (`items/<id>/docs/<kind>.md`), and each finished agent session's summary, one per path (an escalation thread's
 *  turns share theirs). Newest indexed first, as the list sorts. Ids are the item's own. */
export function itemDocs(b: ItemBundle): any[] {
  if (!b.content) return [];
  const it = b.item;
  const id = (k: number) => `${it.id.slice(0, 24)}${k.toString(16).padStart(8, "0")}`;
  const base = { work_item_id: it.id, repo: it.repo, attachment_kind: null, headingless: false };
  const out: any[] = [];
  Object.entries(PRODUCES).forEach(([node, kind], k) => {
    const e = b.events.find((x) => x.type === "node_completed" && x.payload.node_id === node);
    if (!e) return;
    const by = b.sessions.find((s) => s.node_id === node);
    out.push({ ...base, document_id: id(k + 1), title: `${LABEL[kind]}: ${it.title}`, kind: `${kind}s`, source_kind: "artifact", path: `items/${it.id}/docs/${kind}.md`,
      node_id: node, hook_point: by?.hook_point ?? null, worker_session_id: null, attempt: null, round: null, session_status: null, session_work_item_id: null, indexed_at: e.created_at });
  });
  const refs = new Map<string, number>();
  b.sessions.forEach((s, n) => { if (s.session_summary_ref && ["done", "done_with_concerns"].includes(s.status) && !refs.has(s.session_summary_ref)) refs.set(s.session_summary_ref, n); });
  for (const [path, first] of refs) {
    const s = b.sessions.filter((x) => x.session_summary_ref === path).at(-1)!;
    out.push({ ...base, document_id: id(0x100 + first), title: `${taskName(s.hook_point, s.node_id)}: ${it.title}`, kind: "sessions", source_kind: "session_summary", path,
      node_id: s.node_id, hook_point: s.hook_point, worker_session_id: s.id, attempt: s.attempt, round: s.round, session_status: s.status, session_work_item_id: it.id, indexed_at: s.exited_at ?? s.created_at });
  }
  return out.sort((x, y) => (x.indexed_at < y.indexed_at ? 1 : x.indexed_at > y.indexed_at ? -1 : 0));
}

/** The item's documents after a transition: `itemDocs` again, but a path already filed with a body of its own
 *  (`indexSummary`'s, in `docText`) keeps that document, and one `itemDocs` does not make stays. */
export function mergeDocs(had: any[] = [], fresh: any[], docText: Record<string, string> = {}): any[] {
  const own = had.filter((d) => docText[d.document_id] != null);
  return [...own, ...had.filter((d) => !own.includes(d) && !fresh.some((f) => f.path === d.path)), ...fresh.filter((f) => !own.some((d) => d.path === f.path))];
}

/** A document's text: the template for its kind, written from the item's brief and files. */
export function docBody(b: ItemBundle, d: { kind: string; hook_point?: string | null; node_id?: string | null }): string {
  const it = b.item;
  const c = b.content!;
  const [first, ...rest] = c.description.split(/(?<=\.) /);
  const files = c.files;
  const docsOnly = files.every(([p]) => p.startsWith("docs/") || p.endsWith(".md"));
  const list = files.map(([p, i, dl]) => `- \`${p}\` (+${i} −${dl})`).join("\n");
  const top = [...files].sort((x, y) => y[1] + y[2] - x[1] - x[2])[0];
  const check = docsOnly ? "The site builds, and every link on a changed page resolves." : "The repo's tests pass, the new behaviour has a test of its own, and the linter is clean.";
  switch (d.kind) {
    case "specs": return `# Spec: ${it.title}\n\n## Problem\n\n${first}\n\n## Change\n\n${rest.join(" ") || first}\n\n## Touches\n\n${list}\n\n## Done when\n\n- ${check}\n`;
    case "plans": return `# Plan: ${it.title}\n\n${first}\n\n${files.map(([p], k) => `### Task ${k + 1}: Change \`${p}\``).join("\n\n")}\n\n### Task ${files.length + 1}: Check it\n\n${check}\n`;
    case "chain_revisions": return `# Chain revision: ${it.title}\n\n${first}\n\nThe rest of the chain fits the approved spec and plan, so it stays as it is.\n\n- Implementation covers the ${files.length} files the plan names.\n- ${docsOnly ? "There is no verification node: the docs check runs in the merge request's CI." : "Verification runs the repo's tests and the docs check."}\n`;
    case "work_briefs": return `# Work brief: ${it.title}\n\n## What changed\n\n${c.description}\n\n## Files\n\n${list}\n\n## How it was checked\n\n- ${check}\n`;
    case "review_briefs": return `# Review brief: ${it.title}\n\n## Read first\n\n\`${top[0]}\`, the largest change (+${top[1]} −${top[2]}).\n\n## Summary\n\n${c.description}\n\n## Risk\n\n- ${first}\n- Nothing outside the ${files.length} files below changed.\n\n${list}\n`;
    default: {
      const task = words(String(d.hook_point ?? "").split(".").at(-1) ?? "");
      return `# ${taskName(d.hook_point, d.node_id)}: ${it.title}\n\n${first}\n\n## What this session did\n\n${task === "implement" ? `Worked through the plan task by task and committed each.\n\n${list}` : `Ran ${task} for ${d.node_id ?? "the item"} on the ${files.length} files the plan names.`}\n\n## Next\n\n${check}\n`;
    }
  }
}
