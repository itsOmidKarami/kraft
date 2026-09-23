# The permission gate

A worker has nobody to answer a permission prompt. Left to the harness's own
judgment, an unattended agent either stalls on a tool it's unsure about, or
runs it anyway because nobody was watching. Kraft's permission gate is the
third option: the worker asks Kraft, Kraft answers from the task's own
policy, and the answer — allow or deny, and why — lands on the work item's
timeline. Nobody has to sit and watch a session to know what it was let do.

## How a call reaches the gate

Claude Code's own classifier settles most tool calls itself — a read-only
shell command, a routine edit. What it won't settle it hands to whatever
`--permission-prompt-tool` names. Kraft launches Claude with that flag
pointed at its own MCP tool, `mcp__kraft__permission_request`
(`approval_channel` in `src/kraft/harnesses/claude.yaml`), so the CLI calls
into Kraft instead of prompting a human who isn't there.

That MCP tool (`permission_request` in `src/kraft/mcp.py`) forwards the ask
to `POST /worker-sessions/{sid}/permission` (`permission_request` in
`src/kraft/api/routes/sessions.py`), carrying the tool name, its arguments
and the CLI's own id for the call.

Whether the gate is reachable at all, and how much reaches it, follows from
`permission_mode`. With no `allowed_tools` set, Claude runs in `auto`, and
"everything else goes to the classifier" — the gate only ever sees what the
classifier itself declines to settle. Set an `allowed_tools` list on a task
and Claude switches to `manual`: every call the built-in tool-name allowlist
doesn't cover reaches the gate, which is what makes the gate worth
configuring in the first place.

### Cursor: a hook before every call

Cursor has no prompt tool and no per-launch tool flags. It has a
`preToolUse` hook instead, which runs before *every* tool call, ahead of its
own `--auto-review` classifier. When a Cursor task's policy has something to
enforce — an `allowed_tools` list, any `deny_tools`, or a grant other than
`git-commit` — Kraft writes an entry into the worktree's `.cursor/hooks.json`
running `kraft admin permission-hook cursor` (Cursor reads hooks only from the
project, not from `CURSOR_CONFIG_DIR`). With nothing to enforce there is no
hook. Once written, the entry stays for the worktree's life and is the same
for every launch, so sibling launches in one worktree never undo each other's;
a session with nothing to enforce gets no opinion from the gate. A repo's
own hooks in that file stay; Kraft's entry sits beside them. A launch that
installs nothing leaves the file alone entirely. One that needs the hook and
finds a `.cursor/hooks.json` it can't read is refused, naming the file.

The file never reaches a commit: an untracked `.cursor/hooks.json` is kept out
through one marked line in the repository's `info/exclude`, and a tracked one
through `skip-worktree` in that worktree's index. A skip-worktree file can make
a rebase that touches it refuse to run (Kraft-4in7z.10).

What Cursor's hook sees (cursor 2026.09.18, probed 2026-09-23), under the
names policy uses (`tool_names:` in `src/kraft/harnesses/cursor.yaml`):

| Cursor's tool | Checked as | |
|---|---|---|
| `Shell` | `Bash` | |
| `Read` | `Read` | |
| `Write` | `Write` and `Edit` | Cursor creates *and* edits files with it: denied if either is denied, allowed under an allowlist only if both are listed |
| `Delete` | `Delete` | |
| `Grep` | `Grep` | also Cursor's glob |
| web fetch, web search | — | never reach the hook |

A web fetch never reached the hook in the probe, and a web search wasn't seen,
so Kraft can't deny either on Cursor. A Cursor launch whose policy would have
to — `WebFetch` or `WebSearch` in `deny_tools`, or an `allowed_tools` list that
doesn't name both — is refused (`unhooked_tools:` in `cursor.yaml`) rather than
run with that part of its policy unenforced.

The hook asks the same route in **enforce** mode. It starts in about 0.16 s
per tool call. Under an `allowed_tools` list the launch sets
`KRAFT_PERMISSION_FAIL_CLOSED=1` in that worker's environment, and the hook is
fail-closed for that session: a Kraft it can't reach, a payload it can't read or a policy the gate can't resolve is a deny. Without
one, any of those is no opinion, and Cursor's classifier decides as if there
were no hook.

## How it decides

The gate answers from the resolved policy of the task the calling session is
running — the same `allowed_tools`, `deny_tools` and `grants` its launch
resolved, not a separate table. In order:

| The call | Prompt mode (Claude) | Enforce mode (Cursor's hook) |
|---|---|---|
| names a tool in `deny_tools` | deny | deny |
| is an instance of one of the task's grants | allow | allow |
| no layer set `allowed_tools` | allow — an unset allowlist bounds nothing | no opinion: Cursor's classifier decides |
| `allowed_tools` is set and names the tool | allow | allow |
| `allowed_tools` is set and doesn't name it (`[]` names nothing) | deny | deny |
| the policy can't be resolved — the task or its profile is gone | deny: not knowing isn't a grant | deny if the session is fail-closed, else no opinion |

A deny is a deny on every harness. On Cursor a hook `allow` is *not* final: it
does not override `--auto-review`, so a call the gate allows can still be
refused by Cursor's classifier. The gate honours a grant on Cursor, but Cursor
may still refuse it; a launch-time rule to make it stick is Kraft-4in7z.6.

## Grants

A grant is a named operation the gate allows a task whatever its
`allowed_tools` says: `git-commit`, `git-rebase`, `git-push` (on Cursor, subject
to the classifier caveat above). It is a name,
never a command pattern. It matches a `Bash` call only when the command is
exactly one plain git invocation of that subcommand. Refused, so falling
through to the rest of the table, is any command with:

- a shell operator, substitution, redirection, subshell, brace or glob
  expansion, backslash, `!` or a newline — so a commit message with `$` or `!`
  in it, a multi-line one, or one written through a heredoc, is not granted
  (on Cursor with no allowlist, that leaves it to the classifier);
- an env prefix or anything but `git` as the first word;
- a git option before the subcommand other than `-C DIR`, `--no-pager`, `-P`,
  or `-c` setting `user.name` or `user.email`;
- an option that runs a command of the caller's choosing: `--exec` and
  `--receive-pack` on push, `--exec`/`-x` and `--strategy`/`-s` on rebase, and
  any abbreviation of those long options.

`git-push` allows `--force`: an escalation that rebased the branch has to
force-push it.

Grants are set like `deny_tools` and accumulate the same way down the layers
(repository `policy:`, chain, node, step, task). A workspace's meet grants
only what every repository in it grants. A work item's own override, and a retry's, can drop a
grant but never add one: what a task is granted is authored in the chain or
the repository, not filed with the item.

An escalation turn holds its node's grants plus `policy.yaml`'s
`defaults.escalation_grants`, which unset is all three: an escalation has to
be able to rebase the branch and push it. Set it to a shorter list, or `[]`,
to grant escalations less. A gate's reviewer gets no such default, and a
chain task's `git-commit` already comes with its launch.

## Where decisions show up

Every decision — allow or deny, and why — is appended to the work item's
timeline as a `permission_decision` event, with the tool name, the session
and node it came from, the decision, the reason, the grant that allowed it
(if one did) and, from a hook, the harness and the CLI's own tool name
(`harness: cursor`, `cli_tool: Shell`). No opinion is not a decision, so an
enforce-mode call left to Cursor's classifier logs nothing. Watch a live item or
read one back after the fact with:

```bash
kraft view events ID --type permission_decision
```

## Configuring it

The gate enforces whatever `allowed_tools`, `deny_tools` and `grants`
resolve to at the task's scope — set them the same way as any other policy field, and they
narrow or extend what a worker can do without a human standing by. See the
`allowed_tools`, `deny_tools` and `grants` rows in [Configuration](configuration.md)
for how the layers combine.

## Which harnesses reach it

Claude, through its prompt tool, and Cursor, through its hook. Every other
harness runs in its own classifier mode or its most permissive unattended
mode — see the table in [Agent harnesses](harnesses.md#how-each-harness-runs-unattended).
Bringing the rest onto the same gate, one hook per CLI, is tracked under
epic Kraft-4in7z; no dates promised.
