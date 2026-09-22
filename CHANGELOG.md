# Changelog

Notable changes to Kraft, newest first. The release workflow publishes the
section for each version as that release's notes. Releases before 1.0.0 are
listed on the [GitHub releases page](https://github.com/itsOmidKarami/kraft/releases).

## 1.0.1

- **Fix: the default chain's draft merge request now rebases before it
  opens.** `draft_merge_request` opened against the item's base branch as it
  stood when the worktree was cut, not wherever the base moved to while the
  item ran -- Template Schema V1 had dropped the pre-MR rebase every earlier
  chain had. `draft_merge_request` now runs a `kraft.mr_rebase` builtin task
  first, then opens the draft. **A chain you wrote or customized yourself
  keeps the old behavior** -- add the same task to your own
  `draft_merge_request` (or wherever you open a draft) by hand; `kraft admin
  templates show default --resolved` prints the shipped shape to copy from.

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
