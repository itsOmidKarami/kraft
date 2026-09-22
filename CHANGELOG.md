# Changelog

Notable changes to Kraft, newest first. The release workflow publishes the
section for each version as that release's notes. Releases before 1.0.0 are
listed on the [GitHub releases page](https://github.com/itsOmidKarami/kraft/releases).

## 1.0.0

Kraft 1.0 makes the chain a piece of data you can read, lint, and reason
about. Chains are typed YAML under Template Schema V1. Policy is layered from
the instance down to a single task and enforced on every agent launch, and
every scope can be bounded in time and spend. The plan can revise the rest of
the chain, and a person approves the revision before it applies. A person, not an
agent, decides whether to skip, abandon, or give more room.

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
- **Harness profiles are renamed:** `codex_default` → `codex`, `claude_review`
  → `claude`. A chain or policy that names an old profile must be updated.
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
  `/providers`, and `kraft admin harnesses` show each profile, its provider,
  and which tasks use it. Each provider lists its capabilities: the CLI flag
  each one becomes, what it accepts, and what every launch forces.

### Added

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
- The Library shows a steering profile's text, and the Library and Steering
  pages each say which kind of steering they hold and link to the other.

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
  (with a typed sandbox), harnesses, steering, notify, access, intake, theme.
  A repository entry without `enabled` counts as enabled.
- Tool lists in policy hold exact tool names.
- Kraft closes a work item's beads only when its branch actually changed
  something, or a member MR merged. Otherwise a `beads_left_open` event says why.
- Every shipped agent task runs on the `claude` harness profile.
- The never-signal-processes-you-didn't-start rule is built into every agent
  launch instead of seeded as a steering file.

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
- Editing a repo whose `repos.yaml` entry leaves `enabled` unset now refuses
  to save it enabled with no test command, as for an explicit `enabled: true`.
- `kraft view logs -n 0 -f` prints only lines written after it started, not the
  log's tail.
- A trigger added to a policy that had none at startup (Settings, or
  `kraft admin reload`) fires without a restart.

### Removed

- The legacy template system and the legacy intake path.
- `wait_timeout_minutes`. It is still read and migrated to the wait task's
  `total_time_cap_minutes`, with a warning, but it is refused on write.
- The seeded `steering/README.md` and `steering/never-signal-…` files. An
  existing install keeps its copies.

### Known limits

- Sibling tasks that launch together can each pass a shared budget check and
  overshoot it by their combined cost (Kraft-ib2sn).
- A sandboxed repository with submodule members is refused (Kraft-ju36l).
- The merge step does not pin the exact head its CI verified (Kraft-vomwx).
- Steering lives in two places for now: task steering in the Library, and
  repository steering files on the Steering page (Kraft-91i6p).
