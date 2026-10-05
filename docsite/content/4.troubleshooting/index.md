---
title: Troubleshooting and FAQ
description: Why a work item stopped and how to get it going again, common doctor failures, known issues, and common questions.
---

## Why did my item stop?

A work item that needs you appears under **Needs you** on the board, and its
row shows the node it stopped at (`needs_human` is its status in `kraft view
list`). The reason is in the item's side panel and in `kraft view show ID`.
Find out why before you retry: a retry without the cause reruns the same
failure.

```bash
kraft view show ID                                  # the node it stopped on, and the reason
kraft view events ID --type work_item_needs_human   # the stop, with every field
kraft view logs ID                                  # the log of the session that stopped it
```

In Claude Code, `/kraft:triage` reads all three and suggests the next step.

Find the reason in the first column. Where a fix says **retry**, run
`kraft item retry ID`, and add `--steer "..."` to tell the agent what to do
differently. A retry you run resets the node's attempt counters.

Some stops reach you only after Kraft has tried an **escalation turn** on its
own, up to three times (`auto_escalate_stuck_cap` in `policy.yaml`): a task
that failed after its recovery, an exhausted fix loop, and a `stuck:` stop.
`kraft view logs ID` shows those turns. The other stops (a configuration or
forge problem, a budget or time cap, a wait that ran out) come to you at once.

| The reason says | What happened | Fix |
|---|---|---|
| `needs_context: <question>` | The agent asked a question it could not answer from the repo. | Answer it: `kraft item resume ID --steer "<answer>"`. |
| `task failed in node <node>: ...` | An agent task or command failed. | Read `kraft view logs ID`, then retry with a steer that names the fix. |
| `task failed in node <node>: ...`, and `kraft view logs ID` shows an authentication or login error | The agent CLI is installed but not logged in. `kraft admin doctor` checks only that it is on `PATH`. | Run the agent once in a terminal and log in (`claude` for the shipped chains), then `kraft item retry ID`. |
| A forge task failed (opening, syncing or merging the merge request), or a git push or fetch did, and the log shows `HTTP 401`, `Bad credentials`, `Authentication failed`, `could not read Username`, `Permission denied (publickey)` or `gh auth login`. | Kraft holds no forge token. It runs `gh` (GitHub) or `glab` (GitLab), and git, as the user the server runs as, and the forge refused what they sent. Nothing in Settings › Repos can supply a token. | Sign the forge CLI in on the machine the server runs on: `gh auth login`, or `glab auth login` for GitLab. If git still cannot authenticate, see [Forge authentication fails](#forge-authentication-fails). Then `kraft item retry ID`. |
| The log shows `Could not resolve host`, `Connection timed out` or `Connection refused` on a forge or git call. The stop's `facts.cause` is `forge_unreachable`. | The machine the server runs on could not reach the forge. | Check that machine's network, DNS and proxy settings, then `kraft item retry ID`. |
| `no user.name configured in <repo>; set it before Kraft can commit` (or `user.email`) | git has no commit identity for the repo, so Kraft would not make the item's worktree: its commits would fail or carry an invented author. | Set both for the user Kraft runs as, `git config --global user.name "Your Name"` and `git config --global user.email you@example.com` (or without `--global`, in the repo), then `kraft item retry ID`. |
| `no setup_command declared for <repo> in repos.yaml, so <id>'s worktree cannot be prepared. Declare one ...` | The repo has no `setup_command`, and Kraft will not guess what to run in a fresh worktree. This is the usual first stop on a repo with no lockfile Kraft knows. The item stops at its first node. | Set **setup command** on the repo in Settings › Repos (or `setup_command` in `repos.yaml`), or tick **No setup needed** there if the repo has nothing to install (`""` in `repos.yaml`), then **Review & publish**. Then `kraft item retry ID`. |
| `setup command failed for <id>: '<command>': <output>` | The repo's `setup_command` ran in the new worktree and exited non-zero. The reason ends with its output. | Run the command yourself in `cd "$(kraft repo path ID)"`, fix it or the repo's `setup_command`, then `kraft item retry ID`. |
| `could not start <task> in node <node>: ...` | Kraft refused the launch. The rest of the reason says why. | Fix the named cause, often with `kraft admin doctor`, then retry. |
| `could not start <task> in node <node>: ... stored model override is not a model id: clear it with ...` | The item, or one of its nodes, has a model override that is not a model id, such as `sonnet 4`. 1.4 stored any text; 2.0 refuses it before it reaches the agent's command line. | The reason ends with the stored text. Run the command the reason names, `kraft item set-overrides ID --clear` or `kraft item set-node-override ID --node N --clear`, or set a valid model. Then retry. |
| `... nothing registers it for Claude Code: ...` | No `kraft` MCP server is registered, so Kraft refused a Claude worker. | Install the Kraft plugin and run `/kraft:onboard`, or run `kraft admin init`. Then retry. See [the tutorial](/get-started/first-work-item#troubleshooting). |
| `<loop> exhausted after N fix cycle(s)` | A fix loop spent its attempts without passing. | Read the findings (`kraft view events ID --type findings_measured`), then retry with a steer. |
| `<node>.on_base_changed exhausted after N restart(s) from '<node>'` | The base branch kept moving: each time the chain restarted from the named node to pick up the new base, it moved again, until the node's restarts ran out. The shipped chain restarts `merge_request_feedback` from `verification`. | Retry once the base branch settles; a retry resets the count. To allow more restarts, raise that key under `loops:` in `policy.yaml`. |
| `<gate> exhausted after N rejection(s)` | A gate was rejected as many times as its chain allows. | Retry with a steer, or rework the spec or plan yourself. |
| `stuck: ...` | A fix loop made no progress across cycles. | Kraft has already tried an escalation turn on its own, up to three times. Send a message with `kraft item escalate ID --message "..."`, or retry with a steer. |
| `judge: ...` | The fix loop's judge decided another repair attempt is not justified. | Kraft has already tried an escalation turn on its own, up to three times. Send a message with `kraft item escalate ID --message "..."`, or retry with a steer. |
| `a repair in node <node> finished with concerns ...` | A repair ran, but its agent doubted the result. | Read the concerns in the reason, then retry with a steer, or `kraft item skip ID`. |
| `<scope> is read_only, but it changed the worktree: ...` | A read-only step edited files. | Look at the named files, then retry. |
| `the conflict in node <node> cannot be resolved ...` | Rebasing onto the base branch hit a conflict nobody resolved. | Resolve it in the worktree (`cd "$(kraft repo path ID)"`), commit, then retry. |
| `untracked <paths> would be overwritten by the base branch; move or delete it, then retry` (or `git would not start the rebase onto the base branch ...`) | Kraft went to rebase the item onto its base branch, and git refused to start, so nothing was rebased. See [Git would not start the rebase](#git-would-not-start-the-rebase). | `cd "$(kraft repo path ID)"`, look at the files git names, then move, delete or commit them, and retry. |
| `<worktree> has a <name> in progress: inspect it, then delete ...` | The worktree holds a rebase, merge, cherry-pick, revert, bisect or autostash, so Kraft would not rebase or commit over it. | Look at the named file first. If you did not leave it there yourself, delete it as the reason says; for a detached `HEAD`, see [details](#a-git-operation-is-in-progress); then retry. Never run `git rebase --abort`, `git merge --abort` or `--continue` on it. |
| `<worktree> is not on <branch>; check it out by hand, then retry` | The worktree's HEAD names another branch, so Kraft would not commit, rebase or push from it. | `cd "$(kraft repo path ID)"`, check that nothing of yours is on the named branch, `git checkout <branch>`, then retry. |
| `origin/<branch> is at <sha>, which Kraft did not push and this worktree does not have ...` | Someone pushed commits to the item's merge request branch, and Kraft's push would have overwritten them. | `cd "$(kraft repo path ID)"`, `git pull --rebase origin <branch>` to bring them in, then retry. |
| `origin no longer has <branch>, which Kraft pushed ...` | The item's branch was deleted from origin, usually with its merge request merged or closed. Kraft won't recreate it. | If the work should still be published, run the two `git update-ref -d` commands the reason names in the item's worktree, then retry. Otherwise abandon the item. |
| `work item <id> runs sandboxed, and its workspace members <paths> are not the checkouts Kraft made ...` | A member of the item's worktree is not the checkout Kraft made from its connected repository. A sandboxed worker may have swapped it, or the item started before Kraft checked members out this way. | Abandon the item and file it again, which checks each member out afresh. See [Sandboxed workspace members](#sandboxed-workspace-members). |
| `workspace member <path> of <worktree> has no checkout Kraft made ...`, `this sandboxed launch was not given the checkout of workspace members ...`, `setup command for <id> cannot run: its sandbox was not given the checkout ...`, `<path> is not a directory; the member under it is not mounted`, or `... cannot be made in the sandbox's ref store ...` | A sandboxed launch would have mounted a member Kraft could not check against its connected repository, or reach without a symlink, so it did not start. | Retry. If the member's checkout changed, the check before the next task names it. If it recurs, report it as a bug. |
| `<id> runs sandboxed, and no connected repository is known for <paths> ...` | A member's repository is not connected, so Kraft has nowhere safe to check it out from. | Connect it with `kraft repo connect PATH`, then retry. |
| `member <path> of <id>: the connected repository <path> for <path> is missing ...` | The member's repository is no longer at the path `repos.yaml` gives. | Reconnect it (`kraft repo connect PATH`), or fix its `path` in `repos.yaml`, then retry. |
| `member <path> of <id>: the connected repository <path> for <path> is not the top of a git repository ...` | The path `repos.yaml` gives for the member is a directory inside some other repository, so git there would act on that one. | Fix the member's `path` in `repos.yaml` to the repository's own top directory, then retry. |
| `member <path> of <id>: <repo> already has a branch <branch> at <sha>, not at <sha> ...` | The member's repository already has a branch with the item's name, somewhere other than the commit the root points at. Kraft won't check it out and replace what the root records. | Look at that branch in the member repository. Move it to the named commit or delete it, then retry. |
| `budget cap reached: ... on this work item` | The item's own dollar cap refused the next launch. | Click **Raise cap** on the item (on a phone, **Raise budget**), or run `kraft item raise-budget ID --usd N`: either raises that cap and retries. See [Caps and budgets](/guides/day-to-day/raise-a-cap). |
| `budget_usd reached ...`, `token budget reached ...` | A policy `budget_usd` or `token_budget` refused the next launch. The message names the scope: `the work item`, or a node, step or task path in backticks. | The fix depends on the scope the message names. See [A policy budget or token cap refused the launch](#a-policy-budget-or-token-cap-refused-the-launch). |
| `budget cap reached: ... today, across every work item` | The daily cap refused the next launch. Nothing on the item raises it: `raise-budget` is refused, and the item's page offers **Retry** with no raise. | Raise `budget.daily_usd` in `policy.yaml`, or wait for local midnight, then retry. |
| `budget_usd cannot be checked: N launch(es) in ... reported no cost, and unknown spend is never counted as free ...` | A launch in that scope ran on a harness that reports tokens but no cost, on a model `prices.json` cannot price, so Kraft cannot show the scope is under its `budget_usd` and will not count the launch as free. | Clear the item-wide cap with `kraft item set-policy ID --policy budget_usd=none` (add `--policy token_budget=N` to keep a bound), then retry. See [A budget cap cannot be checked](#a-budget-cap-cannot-be-checked). |
| `kraft: a process in the sandbox was killed by its memory limit (<size>)`, or `... under its <size> memory limit (the runtime did not confirm it was the limit)` | The sandbox's memory limit killed the task. In the second form Docker never confirmed it, but the container exited 137 under the limit and Kraft did not stop it. | Raise `resources.memory` in the sandbox policy, or make the task need less, then retry. See [sandboxed workers](/reference/configuration/repos#sandboxed-workers). |
| `the Kit <ref> cannot be used: ...`, or `the Kit <ref> has not been fetched here yet ...` | The item's sandbox is a [Kit](/reference/configuration/repos#kits) Kraft could not fetch, or will not run. | See [A Kit is refused](#a-kit-is-refused). |
| `<scope> hit its time cap of N minutes` | A time cap ran out. | Raise it for this item, then retry: `kraft item set-policy ID --policy time_cap_minutes=240`. See [Raising a cap](/guides/day-to-day/raise-a-cap). |
| `<scope> hit its total time cap of N minutes` | A scope's wall-clock cap ran out. Waits and gates count toward it. | Raise it for this item, then retry: `kraft item set-policy ID --policy total_time_cap_minutes=N`. A node or task needs its path in the key, such as `merge_request_feedback.ci.await_ci.total_time_cap_minutes=N`. |
| `gate <gate> waited past its timeout of N minutes` | Nobody decided a gate in time. | Retry to reopen the gate, then decide it. |
| `<task> timed out waiting ...` | An external wait (CI, a review) ran out. | See [Waiting on CI or a review](#waiting-on-ci-or-a-review). Retry once the outside is fixed. |
| `rate_limit retries exhausted after N attempt(s)` | The agent's provider kept refusing for a rate limit. | Wait for the limit to reset, then retry. |
| CI or automated-review failure, with a suggested **retry** | CI failed for its own reasons, or a cancelled run has no successor. | Retry once CI has recovered. |
| `... are executed by the running Kraft daemon ...` | A task that runs inside Kraft itself failed. | Update and restart Kraft (`kraft admin update --restart`; pause running items first, see [A restart ends running agents](/guides/run/upgrade-kraft#a-restart-ends-running-agents)), or skip the node. |
| `reattach: running session, PID identity unconfirmed, no result (pid N is not alive)` | Kraft was stopped or restarted (`kraft admin stop`, `restart`, `update --restart`, or the service) while the item's agent ran. A clean stop ends every running agent, so there was nothing to pick up again. | The board shows the item as failed. Retry. Before your next restart or upgrade, pause running items, and resume them after: see [A restart ends running agents](/guides/run/upgrade-kraft#a-restart-ends-running-agents). |
| `executor crashed: ...`, `resume: ...`, or a `reattach ...` reason other than the one above | Kraft crashed while the item ran: it was killed, or the machine went down. A restart you asked for gives the `reattach` row above, not this one. | Retry. If it happens again, run `kraft admin doctor` and [open an issue](https://github.com/itsOmidKarami/kraft/issues). |

A pending gate is not a stop. It waits for your decision:
`kraft item approve ID` or `kraft item reject ID --note "..."`.

If the work is heading the wrong way rather than failing, pause it and resume
it with a steer: `kraft item pause ID`, then `kraft item resume ID --steer "..."`.
To move past a node without running it, use `kraft item skip ID`.

The [events catalogue](/reference/events#work_item_needs_human) lists every
field of a stop.

### Stop reasons in detail

The table links here from the rows with more to say.

#### Forge authentication fails

The stop's `facts.cause` is `forge_auth`, and the card reads **Failed** with the fix under it.

`gh auth status` shows who the CLI is signed in as. If git itself cannot authenticate, check its credentials there too: `gh auth setup-git`, your SSH key or credential helper. `kraft admin doctor` checks only that the CLI is installed, not that it is signed in.

#### Git would not start the rebase

Most often the base now tracks a file the worktree has untracked, such as a note or a script the agent never committed, and git will not overwrite it.

A `uv.lock` the setup command wrote and nobody changed is not one of these: Kraft sets it aside for the rebase. One recorded before Kraft kept digests (1.5.0rc14) is kept in place, in case the agent edited it.

#### A git operation is in progress

A sandboxed worker can plant this to move one of your branches. A rebase of Kraft's own that was cut short (a server killed mid-rebase, or an abort that could not run) leaves one too.

If deleting it leaves `HEAD` detached, check the item's branch out again with `git checkout -f <branch>`, as the reason names it: the branch still has the item's commits. Then retry.

Never run `git rebase --abort`, `git merge --abort` or `--continue` on it: that is what moves the branch.

#### Sandboxed workspace members

For the `work item <id> runs sandboxed, and its workspace members <paths> are not the checkouts Kraft made ...` row: host git there would read config the worker can write.

#### A policy budget or token cap refused the launch

For a `token_budget` or a path, `raise-budget` is refused, and the item's page offers **Retry** with no raise. The fix depends on the scope:

- **A `budget_usd` on `the work item`**: click **Raise cap** on the item (on a phone, **Raise budget**), or run `kraft item raise-budget ID --usd N`: either sets it in the item's policy, keeping the policy's other fields, and retries.
- **A `token_budget`**: run `kraft item set-policy ID --policy token_budget=N`, up to `maxima.work_item`, then retry. `set-policy` replaces the item's whole policy override, so pass every field it already sets too (`policy_override` in `kraft view show ID --json`).
- **A path**: the item-wide value also lifts it, unless that node, step or task sets its own cap in the chain. That one only tightens under `set-policy` (`PATH.budget_usd=N` can lower it, not raise it), so this item cannot raise it: skip that node with `kraft item skip`, or raise the cap in the chain and file the item again.

The item's policy was fixed when it was filed, so editing `policy.yaml` or the chain reaches only items filed afterwards. See [Raising a cap](/guides/day-to-day/raise-a-cap).

#### A budget cap cannot be checked

The harnesses that report tokens but no cost are Codex, Cursor, Amp, Antigravity and Gemini. Raising `budget_usd` does not help: the stop stays while any `budget_usd` applies to the scope.

The `budget_usd=none` fix clears a cap from the chain's own `policy:`, `repos.yaml` or a `policy.yaml` default too, but is refused under a `maxima.work_item.budget_usd`, and does not lift a cap the chain set on a node, step or task: skip that node with `kraft item skip`, or bound that harness's scopes with `token_budget` in place of `budget_usd` and file the item again. See [Harnesses that report no cost](/reference/harnesses/cost-reporting#harnesses-that-report-no-cost).

### A Kit is refused

An item whose sandbox is a [Kit](/reference/configuration/repos#kits) reads
the Kit before its worktree is made. If that fails, the item stops needing
you, and the reason names the Kit and why. The same check runs again before
each task; there a failure ends that task's launch as a configuration error,
and nothing runs outside the sandbox. The rest of the reason says which:

| The reason goes on | Fix |
|---|---|
| `` `manifest inspect <ref>` failed: ... ``, or under Podman `` `pull -q <ref>` failed: ... `` or `` `image inspect <ref>` failed: ... `` | The CLI could not read the Kit: a wrong reference, a missing registry login, or a registry it does not trust. Check it with `docker manifest inspect <ref>` as the user Kraft runs as. |
| `<ref> is not a Kit: no vnd.docker.sandbox.kit.descriptor annotation` | The image was not built with the Kit frontend (`# syntax=docker/sandbox-kit:3`). |
| `requires <type>; Kraft does not enforce it`, `is a kind: mixin ...`, `has a capability group ...` | The Kit needs something Kraft does not run. Mark the capability `optional: true`, or remove it, and rebuild. Docker's published Kits are refused this way: [build one for Kraft](/guides/harnesses/worker-kit). |
| `credential service '<service>' has no binding ...` | Add `<service>: <DAEMON_ENV_NAME>` under `credentials` in [sandbox.yaml](/reference/configuration/sandbox), and set that variable in the daemon's environment. |
| A dotted path and a message, such as `capabilities.1.config.apiKey: ...` | The descriptor does not decode: fix the Kit at that path. |
| `has not been fetched here yet` | A review, gate review, escalation turn, or a read through the artifacts or diff API ran before any task fetched the Kit. Retry the item. |

Fix the Kit or the policy, then `kraft item retry ID`. A changed Kit is a new
digest: put it in `kit:` in `repos.yaml` and file the item again, since its
sandbox is fixed when it is filed.

## Waiting on CI or a review

**`merge_request_feedback` waits on "no checks yet".** The pull request has no
CI checks, so nothing will ever pass. Add CI that runs on pull requests, set
`ci_checks: false` on the repo's entry in
[`repos.yaml`](/reference/configuration/repos) so both CI waits, before and
after the merge, pass at once, or
file the item on the `quick-task` chain. Otherwise Kraft keeps checking until
the wait's timeout, then stops the item.

**`external_approval` keeps waiting.** A branch rule on the forge requires an
approving review. Have someone else approve the pull request, or remove the
rule. Kraft checks again on its own.

## `kraft`: command not found

Your shell prints `kraft: command not found` (zsh: `command not found: kraft`)
when the directory `kraft` was installed in is not on your `PATH`. That is
usual right after a uv install, the install script included. Run
`uv tool update-shell`, open a new terminal, and check with `kraft --version`.
After a Homebrew install, put `$(brew --prefix)/bin` on your `PATH` instead.
If Kraft runs as a service, see
the `kraft on PATH` row under
[Common `kraft admin doctor` failures](#common-kraft-admin-doctor-failures).

## Kraft won't start: port already in use

If something already answers on Kraft's port (8765 by default), `kraft`
refuses to start and says what it found:

```text
kraft: refusing to start - something is already answering on 127.0.0.1:8765. curl http://127.0.0.1:8765/api/health to inspect it, or `kraft admin stop` if it's yours.
```

When it names a Kraft server, that is an instance you already started:
`kraft admin stop` stops it. Otherwise pick another port. `kraft admin start
--port 9000` moves only the server; to have the `kraft` command, the plugin and
the VS Code extension follow it, set `port` in
[`access.yaml`](/reference/configuration/access) instead.

## A webhook or proxy gets 403 "unexpected Host"

```text
{"detail":"unexpected Host for a server bound to 127.0.0.1"}
```

On a loopback bind (the default), Kraft answers only a request addressed to
`localhost`, `127.0.0.1` or `[::1]`. A webhook to `POST /api/triggers`, a
health check to `GET /api/health`, and anything else that comes through a
tunnel or reverse proxy with the name its client used, gets this 403. 1.4
checked only browsers, so a tunnel that worked on 1.4 can get it after the
upgrade. `allowed_hosts` is not read on a loopback bind, so adding the name
there changes nothing.

Bind Kraft beyond loopback, with a password, and list the name the proxy's
clients use in `allowed_hosts`: that is the one way the board works through a
tunnel or proxy (see [Remote access](/guides/run/remote-access)). On such a bind a
browser at another name gets a page naming the one it used instead.

For webhooks and scripts alone, the proxy can rewrite `Host` to `localhost`
instead, but a loopback-bound Kraft asks a request from this machine for no
password: read
[A proxy in front of a loopback bind](/guides/run/remote-access#a-proxy-in-front-of-a-loopback-bind)
before you do.

## Common `kraft admin doctor` failures

`kraft admin doctor` runs every check and exits 1 if any fails. `kraft admin
health` checks only the server.

| Row | What it means | Fix |
|---|---|---|
| `home` (a warning) | No server has ever run on this `KRAFT_HOME`. `config`, `harnesses.yaml` and the tokens fail until one has. | Start `kraft` once, then run `doctor` again. |
| `server` | Nothing answers on the configured address. | Start it: `kraft`, or `kraft admin start --detach`. |
| `restart` (a warning) | `kraft admin update` installed a new release, but the running server still runs the old one. Until it restarts, the board shows a page saying so instead of the interface. | `kraft admin restart`. |
| `config` (a warning): `... both exist and only one is read` | A 1.4 `templates/` is still a directory of its own beside `config/`, not the link 2.0's first start leaves. See [Both templates and config exist](#both-templates-and-config-exist). | Merge what `templates/` holds into `config/`, keeping the copy you want of each, and remove `templates/`. See [Upgrading from 1.4](/guides/run/upgrade-kraft#upgrading-from-14). |
| `moved keys` (a warning) | A file still has a key under its 1.4 name or in its 1.4 file: `policy.yaml`'s `triggers`, `intake.yaml`'s `max_concurrent`, a repo's `default_chain_template`, or `theme.yaml`'s `group_by: template`. See [Moved keys](#moved-keys). | Move or rename each key the row names by hand, as [Upgrading from 1.4](/guides/run/upgrade-kraft#upgrading-from-14) lists. For `triggers`, see [Moved keys](#moved-keys). |
| `health`: `invalid policy` or `invalid template` | `policy.yaml` or a chain file does not load. Kraft refuses new work. | Fix the named file, then `kraft admin reload`. `kraft admin templates lint` checks every chain. |
| `health`: `invalid intake.yaml, auto-intake and its schedules are off` | The server started on an `intake.yaml` that does not load: a key Kraft does not know, or a schedule's `cron` it can't run (minute `61`, say). Auto-intake is off and none of its schedules fires. | Fix the named field (Settings › Auto-intake shows it on the schedule), then `kraft admin reload`. See [Invalid intake.yaml](#invalid-intakeyaml). |
| `health`: `invalid intake.yaml, not applied` | A reload refused an edited `intake.yaml` for one of those reasons. The auto-intake and schedules that were running are kept. | Fix the named field, then `kraft admin reload`. |
| `detectors.yaml` | Your own [`detectors.yaml`](/reference/configuration/repos/detectors#detectorsyaml) does not load: a key Kraft does not know, an unknown task reader, or a regex that does not compile. `kraft repo connect` refuses to probe until it does. | Fix the named field, or delete the file to use the packaged detectors alone. Nothing needs reloading. |
| `health`: `orphaned agent sessions` | After a restart, Kraft could not confirm some sessions, and stopped their items. A session leaves the list once its item is retried or has ended. | Retry the affected items. |
| `mcp server` | Nothing registers a `kraft` MCP server with Claude Code: not your user scope, and not any connected repo. Every Claude worker is refused. The row reads Claude Code's registration only, not Codex's, Cursor's, OpenCode's, Amp's, Antigravity's or Gemini CLI's. | Install the Kraft plugin, or run `kraft admin init`. |
| `mcp server` (a warning) | A registration exists, but a connected repo has none of its own, often because its committed `.claude/settings.json` turns the plugin off. See [The mcp server warning](#the-mcp-server-warning). | Register it for that repo (`kraft admin init --repo`), or ignore it if no unsandboxed Claude task runs there. |
| `agent: <profile>` | The agent CLI is not on the `PATH` of the shell running `doctor`. The row names the chains that select the profile and so cannot run, or says no chain can run when every chain does. | Install it. See [The agent row and the service PATH](#the-agent-row-and-the-service-path). |
| `cost: <harness>` (a warning) | That harness reports no cost, and the chains launch it on a model `prices.json` cannot price, or on no model (the CLI's default). `budget.work_item_usd`, `budget.daily_usd` and `--budget` count its spend as $0; a `budget_usd` stops on it. | Give its tasks or profile a priced model, or bound them with `token_budget`. See [Harnesses that report no cost](/reference/harnesses/cost-reporting#harnesses-that-report-no-cost). |
| `kraft on PATH`: `not on PATH` | The `kraft` command is not on `PATH`, usually right after a uv install. The plugin, every MCP registration and the hooks run it by name, so they start nothing. | Run `uv tool update-shell` (or add the directory `kraft` is in to `PATH`), then start `kraft` again from a new terminal. Under a service, see [Kraft not on PATH under a service](#kraft-not-on-path-under-a-service). |
| `kraft on PATH`: `another install` | An older `kraft` comes first on `PATH`. The MCP server and hooks run it. | Uninstall the other one, or reorder `PATH`. |
| `sandbox <repo>`: `the Kit <ref> is refused: ...` | The repository's [Kit](/reference/configuration/repos#kits) cannot be fetched or will not run, so each item there stops. | See [A Kit is refused](#a-kit-is-refused). |
| `kit hosts <repo>` (a warning) | The Kit does not allow a host a harness requires, so that harness's sessions under it are refused the host. | Add the host to the Kit's `runtime` allow list and rebuild it, or run that harness elsewhere. |
| `kit credentials <repo>` (a warning) | An optional Kit credential has no binding in `sandbox.yaml`, so it is skipped. | Add `<service>: <DAEMON_ENV_NAME>` under `credentials` in [sandbox.yaml](/reference/configuration/sandbox). |
| `forge <repo>` | `gh` or `glab` is missing, or the repo has no forge recorded. | Install the forge CLI and log in, or set `forge:` on the repo. |
| `repo <repo>` | A connected repo's path is gone or is not a git repo. | Reconnect it, or `kraft repo disconnect PATH`. |
| `setup <repo>` | The repo has no `setup_command` in `repos.yaml`, so its next work item will stop. The row reads `no setup_command in repos.yaml — suggest: ...` and names a command when it found one. | Run `kraft repo connect <path>` again to save the suggested command. Or set it in Settings › Repos (or `repos.yaml`), or tick **No setup needed** there (`""` in `repos.yaml`) for a repo that needs no preparation, then **Review & publish**. |
| `tests <repo>` (a warning) | `test_command ""`: the repo's work items pass verify without running a test. Or the repo has no test command, so its items stop at their test node (`verify` on `quick-task`, `verification` on `default`). See [Older entries and the tests row](#older-entries-and-the-tests-row). | For `""`, nothing, if the repo really has no tests. Otherwise set a test command in Settings › Repos, then **Review & publish**. When the row names one, `kraft repo connect <path>` saves it. |
| `duplicate <repo>` (a warning) | A repo you connected and a submodule checkout Kraft detected are one repository: usually a member you connected on its own, and its root's submodule. A workspace member naming the second runs with none of the first's settings. | Point the workspace member at the entry you configured (`workspaces:` in `repos.yaml`), then `kraft repo disconnect` the other. |
| `access.yaml`: `allowed_hosts: '...' is not a host name or IP address` | An `allowed_hosts` entry, usually a wildcard such as `*.ts.net` written into the file by hand, is not one host. No browser's `Host` ever matches it, so a device using that name gets a 403. | Replace it with the exact name each device uses, such as `mybox.tailnet-name.ts.net`, in `access.yaml` or on the Access screen. See [Allowed hosts rows](#allowed-hosts-rows). |
| `access.yaml` (a warning): `bound to ... with no allowed_hosts` | The server is bound off loopback and `allowed_hosts` is empty, so a browser on any other device gets a page saying the name it used is not allowed. | Add each exact name the devices use on the Access screen (from this machine, at `http://127.0.0.1:PORT`), or to `allowed_hosts` in `access.yaml`. See [Allowed hosts rows](#allowed-hosts-rows). |
| `mcp token`, `trigger token` | The token file is missing, or other users can read it. | Start the server once to write it, or `chmod 600` it. |
| `pidfile` | `run/kraft.pid` names a process that is gone, or one that is not this Kraft server: its pid was reused, or the process belongs to another program or user. `kraft admin stop` and `restart` never signal such a process. | Nothing. The next `kraft admin start`, `stop` or `restart` clears it. |
| `worktrees` | A worktree has no work item, not even an ended or archived one. A cancelled item's worktree is not flagged. | Check it for work you want, then delete it. |
| `spa bundle` | This install has no web UI. | Reinstall from a release. |

### Doctor rows in detail

The table links here from the rows with more to say.

#### Both templates and config exist

The 2.0 first start found a `config/` you had made and moved in only what it lacked; what both held stayed in `templates/`, and Kraft does not read it.

#### Moved keys

Kraft still reads each one, so nothing stops, except `intake.yaml`'s `max_concurrent`: the server ignores it, with a warning, until the next start moves it to `policy.yaml`.

For `triggers`, the row says how many the next start moves, and, for each that stays, the entry and key `schedules:` refuses (`triggers.0.enabled`, say); a `cron` past its field (`0 24 * * *`) is also skipped, as 1.4 never ran it, and so is one with a number in digits other than 0-9 (`²`). Fix that entry and restart.

#### Invalid intake.yaml

A trigger left in `policy.yaml` still fires.

#### The mcp server warning

In a connected repo with no registration of its own, an unsandboxed task on the harness the row names is refused; a repo whose items run sandboxed or on another harness is unaffected.

The `mcp server` row is also only a warning when no chain launches an unsandboxed Claude task, for example when every repo is sandboxed or the library is Codex-only.

#### The agent row and the service PATH

Doctor does not read the server's `PATH`: under a service, the unit's `PATH` is the one that counts when an item runs, so compare it with your shell's, and if they differ run `kraft admin uninstall-service`, then `kraft admin install-service` from a shell where the CLI works. See [Run Kraft as a service](/guides/run/run-as-a-service#run-kraft-as-a-service).

#### Kraft not on PATH under a service

If Kraft runs as a service, reinstall it from the new terminal you started `kraft` from: `kraft admin uninstall-service`, then `kraft admin install-service`. The service keeps the `PATH` it was installed with.

#### Older entries and the tests row

An entry connected before 2.0 often has no test command where 2.0's probe finds one, and the row then names it.

#### Allowed hosts rows

For the `allowed_hosts: '...' is not a host name or IP address` row: while the server is bound to loopback the list is not read, so the row is only a warning there.

For the `bound to ... with no allowed_hosts` row: 1.4 did not check this over plain http, so an upgraded board can stop opening on another device.

## Where did a page go, and why does my theme look different?

Kraft 2.0 replaced the interface. The old pages moved, and the old addresses
still open the right page.

| Before | Now |
|---|---|
| Settings › Chains, and the old Templates page | Templates › Chains (`/templates/chains`) |
| Settings › Library and Steering | Templates › Library (`/templates/library`); steering profiles are library components |
| Settings › Repos and Settings › Harnesses | Still under Settings (`/settings/repos`, `/settings/harnesses`); the `/templates/...` addresses the 2.0 release candidates gave them open the same pages |
| Settings › Plugins (`/settings/plugins`, a redirect to Settings › Chains already in 1.4) | Templates › Chains |
| Settings › Notifications (`/settings/notify`) | Settings › Notifications (`/settings/notifications`) |
| Settings › Auto-intake (`/settings/intake`) | Settings › Auto-intake (`/settings/auto-intake`) |
| Settings (the front page) | Settings › Policy (`/settings/policy`) |
| The Search page (`/search`) | The board, with the search box on **⌘K** (**Ctrl+K**) from any page |

Repos, Harnesses, Policy, Access and Appearance stayed under Settings. About is still at
`/settings/about`: on the desktop it opens from the version line at the foot
of the sidebar, and on a phone from **More**.

A bookmark to an old address opens the page that replaced it, and keeps its
query string. Two older kinds of address:

- An address from the preview of the new interface, which began with `/ng`,
  drops that prefix and opens the same page.
- A bookmark to an item's old tab address (`/work-items/ID#tab=changes`) opens
  the item's review page, and one to a node (`#node=NAME`) opens that node.

The **Current UI** link and the **Try the new UI** banner are gone: there is one
interface.

On a screen narrower than 768 pixels Kraft shows the phone layout instead,
with the same pages behind a bottom bar (Board, Search, Analytics, More).
Widen the window, or rotate the phone, to get the desktop layout back on the
same page. A phone screen with no desktop page of its own opens the nearest
one: **More** opens the board, and one harness, profile, schedule or
notification channel opens its list.

**My theme looks different.** The 2.0 interface chooses colour with `surface`,
`accent` and `colour_amount`, not `palette`. The first start after the upgrade
rewrites `theme.yaml` once, so the look you had stays: your old `palette` is
replaced by the three keys that draw it, and the original file is kept as
`theme.yaml.pre-2.0` beside it (`theme.yaml.pre-1.5`, or `theme.yaml.pre-ux2`,
if a 1.5.0 release candidate converted it first). See [theme.yaml](/reference/configuration#themeyaml).
If it still looks wrong:

- Open **Settings › Appearance** and pick the surface, accent and amount of
  colour you want. That is the supported way to change them.
- To go back to the file as it was before the upgrade, stop Kraft, copy
  `~/.kraft/config/theme.yaml.pre-2.0` (`theme.yaml.pre-1.5` if a 1.5.0
  release candidate converted it, `theme.yaml.pre-ux2` if that was any of
  rc5 to rc9) over `theme.yaml`, and start it
  again. The next start converts its `palette` once more. The saved copy is
  never overwritten.
- A `theme.yaml` that does not parse, or names a value Kraft does not know, is
  left as it was and the interface falls back to its defaults. `kraft admin
  doctor` has no row for this file: fix the key by hand
  ([theme.yaml](/reference/configuration#themeyaml) lists them), or delete the
  file to start from the defaults.
- `mode: system` follows your operating system's light or dark setting.

## FAQ

**Does Kraft work only with GitHub?** No. Kraft opens and merges changes on
GitHub through `gh` and on GitLab through `glab`. A repo's forge is recorded
when you connect it, from its `origin`.

**Can I use it without Claude Code?** Partly. Kraft ships seven
[harnesses](/reference/harnesses): Claude, Codex, Cursor, OpenCode, Gemini,
Antigravity and Amp. Every agent task in the shipped chains names Claude, so to run without it
you change each task's `harness:`. See
[Switch a task to another harness](/guides/harnesses/switch-harness). Any agent that speaks MCP can drive Kraft
with `kraft admin mcp`, and any shell can use the `kraft` command.

**What does it cost?** Kraft is free and Apache-2.0 licensed. Your agent
provider bills you for the sessions Kraft runs. The shipped `policy.yaml` caps
spend at $10 per work item and $50 per day. See
[Caps and budgets](/concepts/caps-and-budgets).

**Does it run on Windows?** No. Kraft supports macOS and Linux. WSL is
untested. See [Supported platforms](/project/status-and-support#supported-platforms).

**What makes a good spec?** Start with a good description: it is the brief the
spec is written from, and the title is only a label. A good spec names the
problem in the repo's own terms, the approach and the options you rejected,
what is out of scope, and the tests that prove it.

If you already have a spec
or plan, attach it (`kraft item create --spec PATH --plan PATH`), and Kraft
skips writing and approving it again. Each `PATH` must be a file inside the
repo, or a worktree of it, usually under `.engineering/`: Kraft commits it on
the item's branch.

A spec kept outside, such as in `~/notes`, is refused with
`attachment path escapes the repo`. Copy it in, for example to
`.engineering/specs/`, and give that path.

**Kraft or Kraft Lite?** Use Kraft when work should run without you watching,
in its own worktree, across sessions and restarts, with a board and caps. Use
[Kraft Lite](/guides/kraft-lite) when you want the same chain and gates inside
one agent session, with no server to run.

**Can I approve each tool call myself?** No. A worker runs unattended, and
Kraft's [permission gate](/reference/permissions) answers each tool call from
the task's policy, never by asking a person. To control what a worker may do,
set `allowed_tools`, `deny_tools` and `grants`, run it in a
[sandbox](/reference/configuration/repos#sandboxed-workers), and review its
work at the gates between nodes.
