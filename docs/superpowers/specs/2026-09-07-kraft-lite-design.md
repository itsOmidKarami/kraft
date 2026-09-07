# Kraft Lite — the chain without the orchestrator

**Status:** design, awaiting approval
**Date:** 2026-09-07

## 1. What this is

Kraft Lite runs a Kraft chain inside a single coding-agent session. No daemon, no
port, no SPA, no database process. The agent session *is* the executor; a plugin
supplies the spine — node ordering, gates, loop caps and state — and delegates the
work of each node to skills the user already has.

It is not a smaller Kraft. It is the attended half of Kraft:

| | Kraft | Kraft Lite |
|---|---|---|
| Executor | headless agents spawned by a service | the session you are sitting in |
| Concurrency | many chains at once | one |
| Survives your terminal closing | yes | no |
| Board / gates UI | React SPA | the conversation |
| State | `orchestrator.db` | `bd`, or a JSONL file |
| Install | `just install`, run a process | drop in a plugin |

Everything Kraft has that Lite does not exists to serve *unattended* execution.
That is the whole boundary, and it should stay that clean.

## 2. Goals

1. **Distribution.** Ship Kraft's process to repos and people who will not run a
   daemon. A plugin directory is a lower bar than a service on a port.
2. **Low-ceremony local use.** Run a chain on a task too small to justify booting
   a work item, without leaving the session.
3. **On-ramp.** Someone who outgrows Lite upgrades by installing Kraft, not by
   learning a second system. Same chain YAML, same registry shape, same
   vocabulary, importable state.

## 3. Non-goals

Explicitly out, and not "phase two" unless a real user asks:

- Unattended or background execution; anything that outlives the session.
- Parallel chains, a board, a web UI, `watch`.
- CI polling. `on.ci.poll` is a wait loop and a wait loop is exactly what an
  attended session must not do (see §9).
- Spend caps and analytics. No process observing token cost; the host CLI already
  shows it.
- Cross-repo search and the document index.
- Multi-repo state. Lite state is per repo.

## 4. Vocabulary and file shapes

Lite reuses Kraft's nouns unchanged: **chain**, **node**, **hook**, **gate**,
**loop cap**. It also reuses Kraft's two files.

### 4.1 Chain definition — unchanged

`templates/default.yaml` is used verbatim. Same `id`, `nodes`, `tasks`,
`gate_after`, `fix_loop` keys. A chain file authored for Kraft runs under Lite and
vice versa. Lite does not fork the schema, and there is no second hand-written
copy of the node list living in a SKILL.md — if a skill needs to show the steps,
it reads the YAML.

### 4.2 Registry — same shape, new handler kinds

`registry.yaml` maps hook → handler. Kraft's kinds are `agent`, `subprocess`,
`builtin`. Lite adds `skill` and `prompt`, and keeps `subprocess`:

```yaml
hooks:
  on.spec.requested:
    kind: skill
    skill: superpowers:brainstorming
    prompt: Agree the requirements and write a spec before any code.  # fallback
  on.test.run:
    kind: subprocess
    command: [just, test]
  on.mr.open:
    kind: skill
    skill: superpowers:finishing-a-development-branch
  on.human_review.requested:
    kind: prompt
    prompt: Show the diff and the findings, then ask for a decision.
```

| kind | meaning in Lite |
|---|---|
| `skill` | invoke this skill, in this session |
| `prompt` | follow this instruction inline; the escape hatch and the fallback |
| `subprocess` | run this command, surface its output |
| `agent`, `builtin` | Kraft-only. Lite refuses with a message naming the hook. |

Every `skill` entry carries a `prompt` sibling as fallback, so a missing or
renamed skill degrades to an inline instruction instead of halting the chain.

The default bindings Lite ships:

| node | hook | default binding |
|---|---|---|
| spec | `on.spec.requested` | `superpowers:brainstorming` |
| plan | `on.plan.requested` | `superpowers:writing-plans` |
| chain_review | `on.chain.review_ready` | `chain-review` if installed, else `prompt` |
| env_setup | `on.env.prepare` | `superpowers:using-git-worktrees` |
| implementation | `on.implementation.start` | `superpowers:test-driven-development` |
| verify | `on.test.run` | detected test command, `subprocess` |
| verify | `on.review.local.run` | `code-review`, else `prompt` |
| open_mr | `on.mr.open` | `superpowers:finishing-a-development-branch` |
| mr_checks | `on.ci.poll` | `subprocess: [gh, pr, checks]` — one shot, §9 |
| mr_checks | `on.review.mr.run` | `code-review`, else `prompt` |
| human_review | `on.human_review.requested` | `prompt` |
| merge | `on.merge` | `prompt` |

Seven of these are `{kind: builtin, handler: noop}` in Kraft today. Lite is the
first working implementation of those nodes, not a degraded copy of one. That
count moves as adapters land — the planning adapter took it from nine to seven —
so it is asserted by a test rather than trusted here.

Kraft's own registry has since grown a `skill:` field on `kind: agent` hooks, and
resolves a `provider:name` value by telling the agent to load that skill by name.
That is the same idea as Lite's `kind: skill`, arrived at separately. Where the
two differ is only who loads it: Kraft hands the reference to a headless agent
through its system prompt, Lite invokes it in the session it is already in.

## 5. Storage

`bd` if present, a JSONL file if not — **one format either way**.

`bd export` emits one flat JSON object per issue (`_type`, `id`, `title`,
`status`, `description`, `labels`, `dependencies`, `updated_at`, comments). `bd
import` reads exactly that. So the no-`bd` fallback writes that same format to
`.kraft-lite/chain.jsonl`, and adopting `bd` later is `bd import`, not a
migration.

Three details of that format are not optional, and each one was found by running
the real binary rather than reading the shape of an export:

- **`dependencies` are objects**, `{issue_id, depends_on_id, type}`, not id
  strings. The fallback file uses objects too — a file bd cannot import would
  defeat the only reason to share the format.
- **`bd export` writes to stdout only when `-o` is absent.** `-o -` creates a
  file named `-`.
- **`bd import` is an upsert guarded by `updated_at`, at one-second granularity,
  and a tie keeps the local row.** Two writes inside one second — which is every
  gate-then-approve — need a stamped, strictly newer timestamp or the second one
  is silently dropped.

A test that drives the real `bd` binary guards all three; the rest of the bd
tests fake `subprocess.run`, and faking is how all three got shipped once already.

### 5.1 Records

- One **epic** record per chain run: title = the work item title, label
  `kraft-chain:<template-id>`.
- One **child** record per node: title = node id, label `kraft-node:<node-id>`,
  `dependencies` pointing at the previous node so `ready` means "predecessor
  closed".
- A node awaiting a gate is `blocked`, label `kraft-gate:<gate-name>`.
- Loop attempts live in a label, `kraft-attempt:<n>`, replaced each pass.

Nothing else is stored. Node → hook mapping comes from the chain YAML at read
time, never duplicated into the records.

### 5.2 The fallback path

The no-`bd` path is deliberately dumb: append a line, read all lines, last write
per id wins, "ready" = every id in `dependencies` is closed. A chain is at most
a dozen nodes, so there is no index and no query language.

`ponytail:` linear scan over a whole-file read, and no concurrency control —
upgrade path is "install bd", which is also the documented fix if two sessions
ever race the same chain.

## 6. The executor

Two skills carry the whole spine.

**`/kraft-lite:start "<title>"`** — resolve the chain template, materialize the
epic and node records, then hand off to the walk.

`start` freezes its chain into `.kraft-lite/chain.json` and every later verb reads
that copy, so a `--chain` given once is not forgotten by the next verb and editing
a template cannot retarget a run already in flight.

**`/kraft-lite:next`** — the loop, and the resume point after a compaction or a
new session:

1. Read the frozen chain and the state records.
2. Find the first node that is not closed, **in the chain's own order** — followed
   through the dependency links, not taken from the order the store returned.
   `bd export` returns neither insertion nor sorted order. If no chain exists at
   all the state is `unstarted`, which is a different answer from `done`.
   If the node is blocked on a gate, go to §7.
3. For each hook in `node.tasks`, look up the registry and dispatch by kind.
4. Node's tasks all succeeded → close the record. Node has a `fix_loop` and a
   task failed → §8.
5. If `gate_after` is set, open the gate and stop. Otherwise continue to the next
   node.

Both are namespaced (§11), and `next` is safe to call at any point — it derives
everything from the YAML plus the records, and holds no state in the
conversation. That is what makes it survivable across a `/clear`.

## 7. Gates

A gate stops the walk. The skill states which gate, shows what the human needs to
decide on (the spec, the plan, the diff), and ends its turn. It does not decide
and it does not continue.

Resuming is `/kraft-lite:gate approve` or `/kraft-lite:gate reject --note "..."`.
A reject reopens the node the gate guards and sets the note as the leading
context for its next attempt — the same rule Kraft's `reject_gate` enforces, and
for the same reason: a rejection with no reason strands whoever picks it up.

Gates are the one place where Lite and Kraft behave identically, because a gate
is a human decision and neither system is the one making it.

## 8. Loop caps

`policy.yaml` is read as-is. `verify_fix_loop` is `{attempts: 3}` by default.

On a failed task inside a node with a `fix_loop`: read `kraft-attempt`, and if it
is below the cap, increment it, fix, and re-run *that node's tasks only*. At the
cap, stop the chain and escalate to the human with every attempt's output — not a
summary, the traces. Escalation is a stop, not a gate: there is nothing to
approve, the chain has failed.

`wall_clock_s` is ignored in Lite; an attended session has a human watching the
clock. Noted rather than silently dropped so the Kraft/Lite policy files stay
readable as the same file.

## 9. The two adapted nodes

- **`mr_checks`.** Kraft polls CI. Lite runs `gh pr checks` once, reports, and
  stops if checks are pending — "CI is still running, run `/kraft-lite:next` when
  it finishes". A session that sleeps in a loop is a session burning the human's
  attention on a wait.
- **`human_review`.** Kraft has a gate plus a notification. Lite is already
  talking to the human, so the node collapses into its gate.

## 10. `kraft-lite init`

Detection over interrogation. A ten-question wizard gets abandoned on question
three, and eight of the answers are inferable.

1. Scan for installed skills: `~/.claude/plugins`, `~/.claude/skills`,
   `.claude/skills`, plugin manifests.
2. Detect the test command: `justfile` recipe named `test`, else `Makefile`, else
   `package.json` scripts, else the language default.
3. Write `.kraft-lite/registry.yaml` fully populated, every line commented with
   the other candidates that were found.
4. Ask **only** where a hook had two or more plausible candidates, or none.
5. Print the path and say it is meant to be edited.

Typical run: zero or one question. Everything else is a file the user can diff,
revert and commit — the same argument the README already makes for
`~/.kraft/templates/`.

Detection is the one piece of real logic here and gets real tests: a fixture repo
per detection branch, and one asserting an unknown project still produces a valid
registry with `prompt` fallbacks rather than an empty file.

## 11. Packaging and namespace

Ships as a plugin directory with a `.claude-plugin/plugin.json`, exactly as
`kraft init` writes today — the manifest is what makes the skills load as
`/kraft-lite:start` rather than a bare `/start`.

Plugin name is **`kraft-lite`**, not `kraft`. Both can be installed at once and
two plugins claiming `kraft` collide. This is the one naming decision that is
cheap now and expensive after people build muscle memory.

Skills, all namespaced:

| | |
|---|---|
| `/kraft-lite:init` | detect, write the registry |
| `/kraft-lite:start` | materialize a chain from a template |
| `/kraft-lite:next` | run the next node; the resume point |
| `/kraft-lite:gate` | approve or reject |
| `/kraft-lite:status` | where the chain is, what is blocking it |

Distribution is the plugin directory alone. Lite must not require the `kraft`
Python package, or goal 1 is dead on arrival. §15 makes that a structural rule
rather than a good intention.

## 12. Upgrading to Kraft

Documented as three steps, all of which the design above already makes true:

1. Install Kraft, `kraft connect` the repo.
2. `bd import .kraft-lite/chain.jsonl` if state was on the fallback path.
3. Point Kraft's registry at real adapters. The chain YAML does not change.

## 13. Testing

- Detection: fixture repos per branch (§10).
- Fallback storage: ready-set computation over a hand-written JSONL — dependency
  satisfied, dependency open, all closed.
- Registry loading: `agent`/`builtin` kinds are refused by name; a `skill` entry
  whose skill is absent falls back to its `prompt`.
- Cap arithmetic: attempt 3 of 3 escalates and does not run a fourth.

The skills themselves are prose. They are checked two ways, and neither is a
substitute for the other: statically, against the real chain artifact — no hook
or gate named that does not exist, no Kraft-only handler kind left unrefused, no
instruction to sleep or poll — the pattern `tests/test_chain_review_skill.py`
already uses; and dynamically by walking a whole chain through the CLI in a temp
repo. What no test covers is a human running `/kraft-lite:init` in a scratch repo
and confirming it asks at most one question. That stays a manual check.

## 14. Open questions

None blocking. **MIT**, matching the decision already recorded for Kraft's own
first public release — but nothing under `plugins/kraft-lite/` is open-source
until the `LICENSE` file physically exists, so it lands before the first publish,
not after.

The one reversible-now decision is the plugin name (§11). Everything else follows
from the Kraft file formats, which are already settled.

## 15. Two repos

Lite is the first piece of this project that goes public, ahead of Kraft. It gets
its own GitHub repo, published from this one.

Lite is published **before** Kraft's own public release, so the two must not be
entangled: §15.4 is what keeps them apart.

### 15.1 Develop here, publish a regenerated split

`plugins/kraft-lite/` is developed in this monorepo and published to
`github.com/<org>/kraft-lite` by regenerating a `git subtree split` and
force-pushing it. One source, and the drift guard (§4.1) keeps working because
development still happens where `templates/` lives.

**The public repo's history is a build artifact, not a record.** That is a
deliberate choice with a stated expiry: this directory is a few commits old, so
preserved history is worth almost nothing, while depending on it costs a great
deal (§15.4). The day the public repo has its first external contributor is the
day this stops being true, and it is the same day §15.3 applies — a force-push
over somebody else's merge base is not a tradeoff, it is destroying their work.

Until then the split is one-way. An outside PR comes back by cherry-pick into
this repo, never by pulling into the public one.

### 15.1a Telling the public repo's readers

Both breakages caused by §15.1 are the published repo's own fault, so the fix
lives there — one `## Contributing` section in the plugin README, shipped with
the first publish:

- A contributor who branches off `main` loses their merge base at the next
  publish.
- Any *installed* user hits a diverged `git pull`, because the README's own
  install instruction is `git clone`. This is the bigger one: it reaches everyone,
  not just contributors, and the natural reading is "the plugin is broken."

The section states the recovery (`git fetch && git reset --hard origin/main`) and
that a PR is cherry-picked upstream rather than merged there. No `CONTRIBUTING.md`
and no issue templates until the tracker has traffic — one reader path, and
GitHub shows the README first.

### 15.2 The rule that keeps the door open

**Everything under `plugins/kraft-lite/` must work as a standalone checkout.** No
import above that directory, no dependency beyond the standard library, its own
tests, its own CI workflow. Two pieces deliberately live outside it and are the
seam between the repos:

| Outside the plugin | Why |
|---|---|
| `dev/build_lite_chain.py` | needs PyYAML and reads `templates/`; both are things the published repo must not have |
| `tests/kraft_lite_artifact_test.py` | the drift guard is the only test that must see both the YAML and the shipped JSON |

Enforced, not asserted: one test fails on any path in the plugin that climbs
above its own root, and another fails on any non-stdlib import in `kl.py`. Without
those, the leak is only discovered by the first person to clone the public repo.

### 15.3 Switching to two independent repos

**Trigger: the first external contributor.** Not a schedule, not a tidying
impulse. From that point the public history belongs to people other than us and
§15.1's force-push is no longer available.

If inbound contribution makes the one-way split painful, the public repo becomes
the source: stop running `lite-publish`, give Lite its own chain artifact source,
and let Kraft consume it. Because §15.2 was enforced from the start and the split
carried real history, that flip is a decision about where commits land — not a
migration, and not history surgery.

The thing it gives up is the drift guard. Two repos means the chain definition can
diverge from `templates/`, and that becomes a real cost to weigh rather than a
detail. Do not make the switch to tidy something up; make it when contributors are
actually blocked.

### 15.4 Sequencing against Kraft's own release

Lite goes public first; Kraft follows. Kraft's release rewrites this repo's
history wholesale, which changes every source commit `git subtree split` derives
from — so a publish that had preserved history would stop fast-forwarding the
moment that rewrite landed, and would need reconciling by hand.

Regenerating the split (§15.1) dissolves that: the next publish after the rewrite
is simply the next publish. This is the whole reason the public history is
disposable, and the reason to say so out loud rather than discover it later.

Two things follow, and both are requirements rather than observations:

- **Nothing published may cite a path that Kraft's release removes.** That
  release moves this repo's design and planning directories into a private repo.
  Any reference from inside `plugins/kraft-lite/` to a document outside it will
  be a dead link in the public repo, so rationale that matters gets inlined where
  it is needed.
- **`lite-publish` is never run by an agent.** It is public, and it force-pushes.
  A green recipe is not permission to run it.
