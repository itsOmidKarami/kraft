# Changelog

Notable changes to Kraft, newest first. The release workflow writes each
section from the `## Changelog` part of the pull requests it ships; do not
edit this file by hand. Releases before 1.0.0 are
listed on the [GitHub releases page](https://github.com/itsOmidKarami/kraft/releases).

## 1.3.1

### Fixes

- Security: updates urllib3 to 2.8.0, which fixes two high-severity and one medium-severity advisory (HTTPS proxy TLS settings ignored or overridden, an unbounded chunk-size line buffered in memory, and a chunked-deflate infinite loop). It is only installed with the optional `vector` extra. (#318)

## 1.3.0

### New

- Kraft now runs on Python 3.12 and 3.13 as well as 3.14. (#316)

### Fixes

- The seeded `policy.yaml` and `quick-task.yaml` comments now describe each setting in user terms, with no internal notes. Existing installs keep their own copies.
- Troubleshooting now covers the `budget_usd cannot be checked` stop, and a `budget_usd` cap set on one node, step or task. (#313)

## 1.2.2

### Fixes

- The VS Code extension no longer goes read-only when it connects to a Kraft daemon running from a source checkout (a `.devN` or `+local` version). (#308)

- `/api/health`, the sidebar and the empty board now show the address the server actually listens on. Before, a server started with `--port` or `KRAFT_PORT` still showed `access.yaml`'s port.
- `kraft admin start --port N` now says when other `kraft` commands will still dial a different port, and how to reach this instance (`KRAFT_PORT=N`, or `port: N` in `access.yaml`).
- `kraft admin doctor` on a home where Kraft has never run now says so first. Its `harnesses.yaml` row says to start `kraft` once instead of showing a raw file-not-found error. (#310)

## 1.2.1

### Fixes

- Fix: a release publishes to PyPI, the VS Code Marketplace and Homebrew again. v1.2.0 reached only the GitHub release, because PyPI rejected the extension package the release put next to the wheel. (#305)

## 1.2.0

### New

- Four new kraft plugin skills (triage, review, steer, doctor); skill descriptions and guidance corrected: stale CLI verbs, gate/start safety checks. (#221)

- Add: `POST /api/templates/check` validates an unsaved config file as its save would, and template lint issues (API and `kraft admin templates lint`) report line and column. (#232)

- Add: a VS Code extension for Kraft: a sidebar board, gates, diff review with findings and line comments, and config-file diagnostics. (#233)

- New: cut alpha, beta and rc pre-releases from the release workflow, and install them with `kraft admin update --channel`. (#234)

- Add `kraft item reply` and a review-flow API — line-anchored comment threads on a pending gate, review submission with a must-fix gate on approval, a compare endpoint for any two attempts, and an agent that answers threads after a plain "comment" review. (#236)

- Comment and request-changes threads now work at any point in a run, not just at a pending gate; new `kraft view threads`/`compare`, `kraft item comment`/`resolve`/`reopen`, and `kraft item review` verbs (and matching MCP tools) for reviewing a change headlessly. (#240)

- **Behaviour change: the docker sandbox now keeps workers off your other branches and never runs a tool policy unenforced.** A sandboxed worker's ref changes stay in a store of its own, and only its item branch reaches your repository. A sandboxed Cursor or Codex task with `allowed_tools`, `deny_tools` or a grant beyond `git-commit` now stops at launch, because their permission hook cannot run in a container; use Amp or OpenCode for a sandboxed policy, or remove the policy. Sandboxed workers now get their own `HOME`, credentials named in `env_passthrough`, and your git identity, so every shipped harness that can authenticate by env runs in a sandbox. `kraft admin doctor` checks the docker daemon and image. (#245)

- **Sandboxed tasks run on Podman and on rootless Docker and Podman, and on SELinux hosts when you say how.** A new optional `sandbox.yaml` picks the container CLI (`cli: podman`) and what to do where SELinux enforces (`selinux: relabel` or `disable`). Without an answer there, a sandboxed task now stops with a message naming both, instead of failing on denied mounts. On rootless runtimes, what a worker writes is now yours rather than owned by a subordinate uid. A `docker` that is Podman underneath is recognised. `kraft admin doctor` names the runtime it found. (#247)

- **Sandboxed tasks work behind a corporate proxy and CA.** The daemon's proxy settings are forwarded into the container; a proxy on the host's loopback cannot work from a container, and `kraft admin doctor` now says so. An extra root CA, from the new `sandbox.yaml` `ca_bundle` or the daemon's `SSL_CERT_FILE`, is combined with the image's own roots and trusted by every common CLI in the container. Host workers now also keep `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `GIT_SSL_CAINFO`, `CURL_CA_BUNDLE`, `SSL_CERT_DIR` and `ALL_PROXY`. (#248)

- **Sandboxed tasks can have resource limits.** `sandbox.resources: {cpu, memory, pids}` becomes `--cpus`, `--memory` (swap included where the runtime can limit it) and `--pids-limit`, and every sandboxed run gets a default of 4096 processes. A limit the runtime cannot enforce stops the task with a message naming the fix instead of being silently dropped. A task killed by its memory limit is recorded (`sandbox_oom_killed`) and stops for a person naming the limit, rather than counting as the agent failing. `kraft admin doctor` shows which limits the runtime enforces. (#249)

- Feature: sandboxed workers get deny-by-default egress (`sandbox.network`, including Docker Desktop and podman machine), callbacks to Kraft from inside the container, and proxy-injected credentials (`sandbox.credentials`) so API keys never enter the container. (#256)

- Add `kraft item raise-budget` and the MCP `raise_budget` tool, which raise a stopped item's own dollar cap and retry it, like the board's Raise budget button.
- Add `ci_checks: false` to `repos.yaml`: on a repo with no CI, the CI waits before and after the merge pass at once instead of waiting on checks that never come. (#279)

- Security: failed logins are throttled per address (429 after 5 in 15 minutes), the session cookie is `Secure` over HTTPS, and a new `run/trigger-token` authenticates `POST /api/triggers` only, for CI and webhooks. (#284)

- Change: workspace members are checked out as worktrees of their connected repositories, and a workspace whose root or members set a sandbox now runs sandboxed (it was refused before). (#289)

- **Changed default:** `POST /api/work-items` now files the item paused unless the body sets `autostart: true`. Before, a raw API caller started the item, and spent tokens, by default. The board, `kraft item create` and the MCP tool are unaffected.
- **Changed default:** a repo's `default_chain_template` in `repos.yaml` now applies to every way of filing work: `kraft item create`, the MCP `create_work_item` tool, the board's New work item dialog, `POST /api/work-items` and `POST /api/triggers`. Before, it applied only to auto-intake. Naming a chain still wins.
- Fixed: `kraft repo connect .`, and any other relative path given to `repo connect`/`disconnect`, `item create --repo`, `admin reindex --repo` or `view list`/`watch --repo`, is read from where you stand instead of from the Kraft server's directory. `POST /api/repos` refuses a relative path with a 422. (#290)

### Fixes

- Fix: a database connection could be left open when the writer failed or the search index was corrupt. (#220)

- Fix: the plugin READMEs and skills are corrected and link to the current docs. (#222)

- `findings_measured` events now include a `dropped` count when a reviewer's malformed findings were discarded. Internal skills trimmed of contracts Kraft already injects. (#223)

- Fix: after a gate rejection sends work back to implementation, the item page no longer shows the plan's task list and progress bar for a run that isn't following the plan. (#237)

- Fix: review submission, attempt comparison and the reply agent handle refusals, renamed files and agent failures correctly. (#238)

- Fix: a running agent session's spend now shows as a live estimate (`~$X (est.)`) instead of a stale `$0.666+`, priced correctly against Claude Code's own 1-hour cache-write rate, and a dollar budget cap can stop a long session before it exits -- or after a crash that reported no final cost -- instead of losing that spend to a blank total, without double-counting a paused-then-resumed session's spend. (#239)

- Fix: `base_ref` no longer goes stale on retry/resume after a branch is manually rebased onto a new upstream head outside Kraft. (#242)

- Docs: the Kraft plugin's gates and review skills now leave line-level review threads and submit reviews, and the agent guide lists the review MCP tools. (#243)

- Fix: the chain-review step now raises the implementation time cap when the plan has more tasks than the cap can hold, instead of letting the run stop mid-plan. (#244)

- Fix: Kraft works with only the Claude Code plugin installed, and `kraft admin init` is no longer needed. Workers use the permission tool the plugin registers, and a Claude launch with no Kraft MCP server registered is refused with the fix named instead of stopping with 0 tokens. (#260)

- Fix: Kraft reads a repository's committed Claude Code plugin settings the way Claude Code does, so a repository that disables the Kraft plugin is no longer treated as having it. (#264)

- Fix: a session killed by its sandbox memory limit is no longer sometimes recorded as an ordinary failure. (#265)

- Fix: an escalation turn's `git-push` grant now covers only a push to its work item's own branch, and allows `--force-with-lease` but not a plain `--force`. (#268)

- Fix: the API also refuses a worker session's approve, reject, pause, resume, skip, abandon or retry on its own work item, and the security docs say plainly what that does and does not stop. (#269)

- Fix: a sandboxed worker can no longer move your branches or stash by planting git state in its worktree; Kraft stops for you instead. (#272)

- Fix: `kraft admin start --detach` now waits for the server to actually answer `/api/health`, not just for its pidfile to appear. (#273)

- Fix: opening `/docs` or `/redoc` in a browser shows FastAPI's API docs instead of the board.
- Fix: the Analytics "Throughput by week" chart says it counts merged items, and reads "nothing merged in this range" instead of contradicting the Completed count.
- Fix: `$KRAFT_HOME/run` is now `0700`, tightened on every start, and new databases and session logs in it are `0600`. (#275)

- Fix: a session a sandbox memory limit killed is no longer sometimes recorded as an ordinary failure when Docker drops its out-of-memory flag; it stops as an unconfirmed memory-limit kill instead. (#277)

- Fix: the board and docs site no longer load fonts from Google; the API refuses a worker's complete, cancel, escalate and attachment, override, policy and budget changes on its own item; and an escalation turn can no longer approve or reject its own item's gates. (#280)

- Security: bump PyJWT to 2.15.1 (Dependabot #18). (#281)

- Fix: an escalation turn can no longer raise or change its own work item's budget or policy; like a gate, that's a person's decision. (#282)

- Fix: Raise budget appears only when the item's own dollar cap stopped it. A stop on a policy `budget_usd`, a `token_budget` or the daily cap names that cap and how to raise it, and `kraft item raise-budget` refuses it. (#283)

- Fix: Claude workers are no longer refused when Kraft's MCP server is registered at local scope or in a managed `managed-mcp.json`; a Kraft plugin enabled but not installed now counts as not registered, and `kraft admin doctor` checks each connected repo. (#285)

- Fixed: every server route that takes a repo path refuses a relative one instead of reading it against the server's directory: `POST /api/work-items` and `POST /api/triggers` answer 422, `PATCH`/`DELETE /api/repos?path=` answer 404. Fixed: analytics filtered to the `default` chain now counts items filed without naming a chain. (#291)

- Fix: Kraft no longer force-pushes over commits a person pushed to an item's merge request branch; it stops and says how to bring them in. Fix: connecting a workspace root reuses a member you already connected on its own instead of adding an empty entry for its submodule, and `kraft admin doctor` warns about existing duplicates. (#292)

- The `list_work_items` MCP tool now names the statuses you can filter on, including `needs_human`, and no longer lists `failed`, which never matched anything.
- The docs site's canonical links now point at the real pages under `/kraft/`. The dead "Add MCP Server" and "Copy MCP Server URL" menu items are gone, `robots.txt` exists, and the search dialog has a proper accessible name.
- The PyPI page lists classifiers: Beta, console, developers, macOS and Linux, Python 3.14.
- The docs say that `--auto-gate` is on by default, explain how to install the VS Code extension, and cover an agent that isn't logged in and a port that is already in use. (#304)

## 1.1.0

### New

- New harness: Cursor's agent CLI (`agent -p`), checked with real runs on
  cursor-agent 2026.09.18-9a7762b (Kraft-bosip). It runs in `--auto-review`,
  Cursor's classifier mode; `permission_mode: force` overrides it. Every
  launch points `CURSOR_CONFIG_DIR` at a Kraft-owned directory under
  `$KRAFT_HOME/run`, rewritten with Cursor's default config and commit
  attribution off, so a worker's commits carry no `Co-authored-by: Cursor`
  trailer and the classifier no longer refuses them (Kraft-umakq). Your own
  `~/.cursor` is untouched. Tokens are read off the log; Cursor reports no cost. (#192)

- **Behaviour change: the never-signal rule is no longer on every agent launch.** It is now an opt-in steering profile, `never-signal-processes-you-didnt-start`, that a repository names in `repos.yaml` (`steering: [never-signal-processes-you-didnt-start]`). Its text is unchanged. **Existing installs lose it on upgrade until you act.** Templates are never reseeded: an install first set up on 1.0.x has no such profile, and one upgraded from 0.x has it but no repo names it. If any of your repositories run tests that start their own servers, run `kraft admin doctor` after updating: it prints the exact YAML for the library profile (skip that part if your library.yaml already has one) and for the `repos.yaml` line. An item already in flight froze its steering at intake, so it runs without the rule until it is retried or re-filed. (#193)

- Codex workers now work on a default install. Codex's sandbox refused
  writes outside the worktree, so a worker could not write its result file
  under `~/.kraft/run/results` or commit (a linked worktree commits into the
  main checkout's `.git`), and every codex item failed. Kraft now grants
  both directories on every launch through a new `writable_dirs` capability
  (Kraft-rs9pk).
- Codex runs in approve-for-me mode by default: the workspace-write sandbox
  plus Codex's automatic reviewer for anything outside it, the counterpart of
  Claude's `auto`. `permission_mode` still overrides it per harness profile or
  task.
- Fix: resuming a codex session failed at argument parsing, because
  `codex exec resume` rejects `-s` after `resume`. Every codex option is now a
  `-c` config key, which parses on both paths.
- Fix: a codex usage limit crashed the run instead of parking the item as
  rate limited. (#194)

- New harness: OpenCode (`opencode run`), for any provider OpenCode knows,
  including a ChatGPT login and OpenCode's free models (Kraft-nv1f1). Checked
  against opencode 2.0.15 with real Kraft work items on `opencode/big-pickle`
  and `openai/gpt-5.6-luna`. Every launch passes `--auto`, since `run`
  otherwise rejects every permission request. There is no `effort`: name a
  variant in the model id (`provider/model#high`). Usage comes from
  `opencode session export`, because opencode 2.x's JSON log leaves out the
  last step's tokens (Kraft-ihoen). (#195)

- New harness: Amp (`amp -x`), with Amp's mode (`low` to `ultra`) as
  `effort` (Kraft-gvrke). Checked with a real Kraft work item on a logged-in
  amp. Resuming works: every launch passes `--no-archive-after-execute`,
  since Amp archives a thread after `-x` and refuses to continue an
  archived one. Amp reports no cost Kraft can read, so none is recorded. (#196)

- Kraft's permission gate now answers Codex workers too. When a task's
  policy has something to enforce, the launch passes Codex a `PreToolUse`
  hook with `-c` and trusts only that hook, by the hash `codex app-server`
  reports; `--dangerously-bypass-hook-trust` is never used, and a launch
  whose hook Codex won't trust is refused. `deny_tools` and `allowed_tools`
  now work on Codex (`apply_patch` is checked as `Write` and `Edit`), each
  decision is logged on the timeline, and a resumed thread keeps the hook.
  Codex's web search never reaches the hook, so policy that must deny
  `WebSearch` refuses the launch (Kraft-4in7z.3).
- OpenCode and Amp workers now honour a task's `deny_tools` and
  `allowed_tools`: Kraft writes them into that launch's own config (OpenCode's
  `OPENCODE_CONFIG_CONTENT` with `--standalone`, a per-session Amp
  `--settings-file`), and the CLI enforces them. Denying `Bash` also denies
  OpenCode's code mode. An allowlisted tool on Amp is allowed outright, past
  Amp's own built-in asks. A denied tool name the CLI has no tool for refuses
  the launch; an allowlisted one grants nothing. These decisions aren't logged on the timeline, and grants aren't
  applied (Kraft-4in7z.4, Kraft-4in7z.2).
- A Codex, Cursor or other non-Claude worker that outlived a Kraft restart
  now has its tokens read with its own harness's log reader instead of
  Claude's, which found none; each session now records its harness, and
  older sessions keep Claude's reader (Kraft-9elw1).
- Kraft's permission gate now answers Cursor workers too: a `preToolUse`
  hook, written into the worktree's `.cursor/hooks.json` (never committed)
  when the task's policy has something to enforce, applies `deny_tools` and
  `allowed_tools` (which Cursor launches can now use) and logs each decision
  on the item's timeline, with the harness and Cursor's own tool name.
  Everything policy doesn't decide is left to Cursor's classifier, and a hook
  allow doesn't override it (Kraft-4in7z, Kraft-4in7z.6). New `grants:` in
  policy (`git-commit`, `git-rebase`, `git-push`) let a task's gate allow
  exactly one plain git invocation of that operation, even outside
  `allowed_tools`; a commit message with `$` or `!`, or anything chained,
  goes to the classifier when there is no allowlist, and is denied under
  one. A granted push must name a plain remote, and `--delete`, `--mirror`,
  `--all`, `--prune`, `--repo` and git's `-C` are never granted. A grant is
  the gate's logged allow: Cursor's classifier can still refuse the call
  (Kraft-4in7z.6), and a Claude task under an allowlist without `Bash` has no
  shell tool to make it with (Kraft-4in7z.12). Grants accumulate down the
  layers, and a work item's own override, or a retry's, can drop a grant but
  never add one. An
  escalation turn is granted all three by default, so it can rebase and
  push; `defaults.escalation_grants` in `policy.yaml` narrows that.
- New `policy.yaml` key `escalation_harness` picks the harness an escalation
  turn runs on (Kraft-wge0e). Unset, it is `claude`, as before. Set it in
  `defaults:`, on a repository, chain or node, or for one item with
  `kraft item set-policy`; `item` follows the harness the item's own work
  last ran on. An unknown profile is refused when `policy.yaml` is read. A
  resumed escalation or paused task now finds its session id with its own
  harness's reader, and a resumed codex thread records only what it added. (#216)

### Fixes

- Before a workspace item's draft merge requests open, each changed member is
  now rebased onto its own default branch, and the root's pointer is updated to
  match. Member drafts no longer open on the stale base they were cut from.
- The pre-MR rebase's `git rebase --abort` now has its own 30-second limit. A
  git hook that hangs during the abort stops the item for a human, with a
  message saying the worktree was left mid-rebase, instead of holding the
  worker slot forever. (#208)

- `kraft admin templates lint --dir PATH` lints a template directory
  in-process, with no server and no `$KRAFT_HOME`. It doesn't check an
  installed skills override or `policy.yaml` ceilings. (#210)

- A workspace item whose root rebase conflicts only because the root's base
  branch also moved a member's pointer now stops with a sentence naming the
  member and saying how to resolve it, after git's own conflict text. (#211)

## 1.0.8

- Fix: a `chain_revision_approval` gate now gets the same board, action-bar
  and search Approve treatment `human_review_approval` has -- its own prompt
  text, and Approve routes to the item page instead of firing a blind
  request that always 409s (the gate's Approve needs a digest only the
  artifact pane carries).

- Fix: a `read_only_violated` event (a read_only step or node changed the
  worktree) now shows what changed on the Timeline instead of the bare event
  name with no detail.

## 1.0.7

- Fix: an in-flight work item's own repository steering no longer silently
  drops if `repos.yaml`'s `path:` for that repository is hand-edited while it
  runs. The launch now finds it by the repo the item was filed against, not
  only by the repository's current path. (A workspace item's fanned-out
  member repositories are still looked up by their live path only, so a
  member's path edited mid-flight can still lose its steering -- Kraft-ku1um.)
- Fix: `kraft admin doctor` now fails a row for a connected repo whose
  `steering:` in repos.yaml names a profile the template library does not
  define, naming the repo and the missing profile. Previously a hand-edited
  repos.yaml (or a library edited outside Kraft) read healthy until the next
  work item's intake refused it.

## 1.0.6

- Fix: a gate's agent reviewer no longer approves a chain revision that
  changed after it read it — its approval is now bound to the digest of
  what it actually read, the same way a person's approval already is.
- Fix: a gate whose `auto_review` task selects an agent profile carrying its
  own `fallback:` list is now refused at launch, naming the profile, instead
  of silently ignoring the list and leaving a rate-limited review undecided.

## 1.0.5

- **Fix: a `policy.yaml` cron trigger naming an unconnected repo is now
  skipped, not filed.** `POST /work-items` and `POST /triggers` already
  refused a repo Kraft has not connected (`kraft repo connect`, Kraft-ta8nv);
  `triggers.tick` called intake directly and missed that door. It now skips
  the trigger with a logged warning and still fires the other due triggers in
  the same tick.

## 1.0.4

- Fix: `kraft item set-overrides` (and MCP `set_agent_overrides`) now refuses
  a `model`, `escalate_model` or `effort` that any agent task's harness in the
  item's chain would refuse, at the door instead of hours later when that
  task launches -- the same check a per-node override already got.

- Fix: `kraft admin doctor` now tells an install first set up on 1.0.1 or
  1.0.2 to add the pre-MR rebase from 1.0.3. The capability was recorded
  as 1.0.1, so doctor only flagged installs seeded before that.

## 1.0.3

- **Fix: the default chain's draft merge request now rebases before it
  opens.** `draft_merge_request` opened against the item's base branch as it
  stood when the worktree was cut, not wherever the base moved to while the
  item ran -- Template Schema V1 had dropped the pre-MR rebase every earlier
  chain had. `draft_merge_request` now runs a `kraft.mr_rebase` builtin task
  first, then opens the draft.
  - **This does not reach any existing install on its own.**
    `$KRAFT_HOME/templates/` is seeded once, at first run, and never
    overwritten -- `kraft admin update` replaces Kraft itself, not your
    templates. So **every install running before this release**, not only a
    hand-customized chain, keeps opening draft MRs on a stale base until you
    add the task yourself: run `kraft admin doctor` after updating, which
    lists this (and every other capability your templates are missing) with
    the exact YAML to paste in. An item already running when you update keeps
    the chain it was filed against; only an item filed after you update the
    template gets the fix.

## 1.0.2

- Fix: a session log too big to read whole now shows where it was cut off,
  with a link to the full plain-text log, and offers a download instead of
  copying the whole log into the page (Kraft-qmjk1).
- Codex: Kraft reads codex's `--json` log for token usage, the session id
  and usage limits (Kraft-w3kot).

## 1.0.1

- Fix: the Appearance settings page's palette, mode, density and board
  controls now show as disabled while your theme is still loading, instead of
  silently doing nothing if you clicked one in that window.

## 1.0.0

Kraft 1.0 makes the chain a piece of data you can read, lint, and reason
about. Chains are typed YAML under Template Schema V1. Policy is layered from
the instance down to a single task and enforced on every agent launch, and
every scope can be bounded in time and spend. The plan can revise the rest of
the chain, and a person approves the revision before it applies. Agent tasks pick
a named model tier, and can fall back to another harness or model when one is
rate-limited or unavailable. A person, not an agent, decides whether to skip,
abandon, or give more room.

### Before you upgrade from 0.x

- **Finish or abandon every in-flight work item first.** An item filed under
  0.x has no V1 chain, and 1.0 refuses to run it.
- **`kraft admin update` replaces a pre-V1 template home**, after asking (or
  with `-y`). The old home is moved to `~/.kraft/templates.pre-v1-<timestamp>`.
  There is no migration of chains: re-apply your tuning (model and effort
  pins, custom nodes) by hand from that backup.
  - Carried across unchanged: `access.yaml`, `notify.yaml`, `theme.yaml`,
    `repos.yaml`, `intake.yaml`, `steering/`, `harnesses/`.
  - `policy.yaml` keeps your value for every key V1 still has, and prints every
    key it drops.
- **Steering files become library steering profiles.** On its first start,
  1.0 adds each `templates/steering/<name>.md` to `library.yaml` as
  `steering: {<name>: {instructions: <the file's text>}}` and moves the
  directory to `templates/steering.pre-1.0/`. `repos.yaml`'s `steering:` names
  keep working. A name `library.yaml` already defines keeps the library's
  text; that file (and any empty one) is only in the moved-aside copy, and the
  log says so. An item already in flight keeps its steering: it reads its
  repository's names against the library at each launch, as it read the
  files before.
- **Harnesses are renamed:** `codex_default` → `codex`, `claude_review`
  → `claude`. A chain or policy that names an old harness must be updated.
- **Chain API routes moved** from `/api/templates/{id}` to
  `/api/templates/chains/{id}` (and `/resolved`). The old paths answer 404.
- **Check for a second `kraft` on your PATH**, such as an old Homebrew
  install. MCP servers run `kraft` by name; `kraft admin doctor` now fails when
  another install shadows this one.
- **Restart any agent session that uses Kraft's MCP tools** after upgrading.

### Highlights

- **Chains are data.** Nodes, steps, tasks and gates are typed and ordered.
  Recovery handlers, fix loops with a judge, and stuck escalation are part of
  the chain rather than hidden in code. A reusable **Library** of tasks, steps,
  nodes and steering lets chains extend shared parts. The Library has a
  Settings page, an API and a CLI.
- **Policy reaches every launch.** Policy is layered instance → repository →
  work item → chain → node → step → task. It applies to dispatch, gate
  review, escalation, the judge and recovery alike. A work item can carry its
  own policy override and its own base branch.
- **Caps at every level.** `time_cap_minutes` (running time),
  `total_time_cap_minutes` (wall clock), gate timeouts, `token_budget` and
  `budget_usd` can be set on any scope. `policy.yaml` sets defaults and maxima
  per level: `work_item`, `nodes`, `steps`, `tasks`. A child scope can't exceed
  its parent. Hitting a cap stops the item for a person and is never recorded
  as a code failure.
- **The plan can revise the chain.** After planning, a `chain_revision` node
  proposes the rest of the chain, and a gate shows you the change before it
  applies. A revision can never drop the merge-request nodes. A revision
  computed from a chain that has since changed is refused. The final gate is
  now called `final_review`.
- **Stops tell you what to do next.** A stopped item carries a
  `suggested_action` (skip, retry or abandon, with a reason). The API, the CLI
  and escalation all show it. Only a person may skip, abandon, or reset a
  limit. An escalation agent may retry its own work, and says so when it
  isn't allowed to do more.
- **Harnesses you can see.** A Harnesses page, `/api/harnesses/profiles` and
  `/providers`, and `kraft admin harnesses` show each harness, its provider,
  and which tasks use it. Each provider lists its capabilities: the CLI flag
  each one becomes, what it accepts, and what every launch forces.
- **Model tiers and fallback.** Agent profiles (`deep`, `strong`, `fast`) name
  a model tier once, with a model per provider, and tasks select a tier
  instead of spelling out a model. An opt-in fallback list moves a rate-limited
  or unavailable launch to another harness, tier or model at once, and every
  switch is logged where you will see it.
- **One steering store.** Steering profiles live in the Library. A task or a
  repository names them, and their text is frozen into the item at intake.

### Added

- **Agent profiles:** `harnesses.yaml` gains `profiles:`, named model tiers
  (`deep`, `strong`, `fast`) with a model per provider. A library task picks
  one with `profile:` instead of its own `model:`/`effort:`. The profile is
  read live at each launch. A task whose profile names no model for its
  harness's provider fails `kraft admin doctor` and stops for a human, and no
  other model is substituted. The shipped `implementer`, `repair_*` and
  `strict_judge` tasks use `profile: strong`, which is the same `sonnet` at
  `high` effort as before. An existing install keeps its files and runs
  unchanged. `kraft admin doctor` says how to adopt profiles.
- `kraft item create` matches intake: `--skip-nodes`, `--budget`,
  `--node-override`, `--autostart`. An agent asking for autostart is refused.
- Attachments can be replaced until the item starts
  (`kraft item set-attachments`). A gate trimmed for a missing attachment
  comes back once one is attached. Intake warns about a duplicate open item.
- The default chain writes the merge request's title and description before
  it opens the MR.
- **Intent-driven development:** set `intent_dir:` on a repository, and every
  agent launch is told about its intent tree. The spec, plan, code-review and
  work-brief skills apply it.
- `kraft view show` prints a usage line with cached and uncached tokens apart.
- `kraft admin doctor` checks for a shadowing `kraft` on PATH.
- The Library shows a steering profile's text as text.
- One node of a not-yet-started item can get its own `model`, `effort` and an
  `extra_prompt` appended to each of its agent tasks (`kraft item
  set-node-override`, `--node-override`, MCP `set_node_overrides`). The node's
  values beat the item-wide `set-overrides`; one the node's harness refuses is
  refused when you set it.
- `read_only: true` on a step or an exec node: Kraft checks the worktree
  before and after, and stops the item naming any file it changed. It is
  opt-in, and no shipped chain sets it.
- **Launch fallback:** a `fallback:` list, on an agent profile or on a task,
  says where a launch goes when it is rate-limited or its harness is
  unavailable, in the same dispatch: another harness, another profile, or
  another model or effort. Kraft remembers a rate-limited harness and model
  until its reset and skips it on every item, and logs each switch as a
  `launch_fallback` event, a timeline sentence and a board marker. It is opt-in, and no shipped
  task or profile declares one.

### Changed

- **Token accounting.** Spend reads each result's cumulative `modelUsage`, so
  sub-agent tokens and turns killed before their result are counted. A
  resumed CLI session is counted once. Cache writes and cache reads are stored
  apart from uncached input: `tokens_in` now means uncached input. Totals and
  budgets still count all of them. Sessions recorded before 1.0 keep their
  old total and show "cache not split".
- `kraft admin reload` rereads `policy.yaml` too. A file that doesn't validate
  is refused and the running policy is kept.
- Every settings file loads and saves through a typed model: repositories
  (with a typed sandbox), harnesses, notify, access, intake, theme.
  A repository entry without `enabled` counts as enabled.
- Tool lists in policy hold exact tool names.
- Kraft closes a work item's beads only when its branch actually changed
  something, or a member MR merged. Otherwise a `beads_left_open` event says why.
- Every shipped agent task runs on the `claude` harness.
- The never-signal-processes-you-didn't-start rule is built into every agent
  launch instead of seeded as a steering file.
- **One steering store.** A repository's `steering:` in `repos.yaml` names
  steering profiles from `library.yaml`, like a task's. The text is frozen into
  the work item at intake, so editing a profile reaches items filed afterwards.
  A repository save, an intake, and a library save are each refused when a
  repository would name a profile the library doesn't define. Steering
  profiles are created and edited on Settings → Library; the repository
  steering picker lists them.

### Fixed

- **Sandboxing:** the sandbox wraps the whole work item, and `setup_command`
  never runs on the host. Host git never runs code a sandboxed worker could
  plant: it doesn't recurse into nested repositories, and hooks are off. A
  planted repository stops the item for a person.
- **CI and merge requests:**
  - A cancelled CI run is never a verdict, and one with no successor stops
    promptly.
  - No MR is opened with zero commits.
  - A workspace root MR waits for its own approval and merge.
  - Workspace members:
    - each member rebases onto its own origin;
    - the root pointer moves only to what merged;
    - a rebased head waits for its own CI.
- A worker turn that ends with a background job still running fails and names
  the job, instead of hanging. The implementer task ships with a 120-minute cap.
- Session logs: the live tail reads each byte once, off the event loop. Huge
  logs return their last 2 MB behind a truncation marker.
- `kraft admin doctor` no longer fails on the optional embeddings extra.
  `kraft view show --json` returns the full item.
- The connect probe proposes `just test` only for a real `test` recipe.
- `install-service` works on Linux runners and reinstalls cleanly.
- A repo edit that turns on a repo with no test command, or clears the last test
  command of an enabled one, is refused, also when `enabled` is unset in
  `repos.yaml` (unset means enabled).
- `kraft view logs -n 0 -f` prints only lines written after it started, not the
  log's tail.
- A trigger added to a policy that had none at startup (Settings, or
  `kraft admin reload`) fires without a restart.
- `kraft admin doctor` fails when the embeddings extra is installed but its
  model will not load or encode; `/health` reports the last such failure.
- Filing a work item (`POST /work-items`, `POST /triggers`, `kraft item create`,
  MCP `create_work_item`) against a repo that isn't connected is refused with a
  422 that names `kraft repo connect`, instead of accepting any directory.
- Chain revision:
  - approving one applies the revision you read. The approval sends back the
    digest `kraft view artifact` prints (`kraft item approve --digest`), and a
    revision that changed since is refused with 409;
  - a revision can't skip a node whose only merge request work is in its
    recovery steps.

### Removed

- The legacy template system and the legacy intake path.
- `wait_timeout_minutes`. It is still read and migrated to the wait task's
  `total_time_cap_minutes`, with a warning, but it is refused on write.
- The seeded `steering/README.md` and `steering/never-signal-…` files. An
  existing install's copies become library steering profiles, like any other
  steering file.
- `templates/steering/*.md` as a steering store, the Settings → Steering page
  (its address opens the Library), and the `/api/steering` routes.

### Known limits

- Sibling tasks that launch together can each pass a shared budget check and
  overshoot it by their combined cost (Kraft-ib2sn).
- A sandboxed repository with submodule members is refused (Kraft-ju36l).
- The merge step does not pin the exact head its CI verified (Kraft-vomwx).
- An auto-review agent approving a chain revision gate is checked against the
  gate's last recorded view, not a view of its own (Kraft-rndd1).
- Only Claude launches can trigger a fallback: codex and gemini don't report a
  rate limit yet. They can still be fallback targets.
