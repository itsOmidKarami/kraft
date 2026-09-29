---
title: Repos
description: Every field in repos.yaml, and how kraft repo connect fills it in.
---

`repos.yaml` lists the repositories you connected and how Kraft works in each.

```yaml
repos:
  - path: /home/you/code/my-service
    name: my-service
    default_chain_template: default
    forge: github
    test_command: null
    intent_dir: null
    setup_command: "uv sync"
    env: {}
    env_passthrough: []
    local_files: []
    models: {}
    deny_tools: []
    steering: []
    sandbox: null
    policy:
      allowed_tools: [Read, Edit, Bash]
    automated_review:
      bot: coderabbitai     # or `check: <name>` -- exactly one of the two
```

| Field | Default | Means |
|---|---|---|
| `path` | *(required)* | Absolute path to the repo. |
| `name` | — | Display name; set at connect time, not otherwise validated. |
| `id` | — | The repository id a workspace names this entry by (`[a-z][a-z0-9_-]*`, unique). Only a workspace's root and members need one; connecting a repo with submodules writes it for them. |
| `enabled` | `true` | Set `false` to take this repo out of service without disconnecting it: new items can't target it and auto-intake skips it, while running items keep going. An absent key counts as enabled. Kraft refuses an edit that would leave an enabled repo with neither a `test_command` nor `test_scopes`. |
| `managed` | `true` | Keeps a human-connected repo out of Settings' "Detected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain_template` | — | Which chain template a work item on this repo uses when none is named explicitly. |
| `forge` | `null` | `github` or `gitlab`, which forge adapter `backend: auto` resolves to for this repo. `kraft repo connect` sets it from the repo's remote. |
| `project` | `null` | The GitLab project path, when `forge: gitlab`. A legacy `gitlab_project` key still reads. |
| `models` | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude: opus`): above the profile's own `defaults:`, below a task's `model:` or agent `profile:` and the work item's override. Keyed by profile because one model name means nothing to another provider. The retired `default_model` key is dropped with a warning. |
| `test_command` | `null` | The command CI actually runs for this repo — what the changed-test-scope verification runs, as one scope over every path. A repo with neither this nor `test_scopes` stops that verification for a human rather than inventing a command. |
| `areas` | `{}` | Path-scoped contexts inside this repo, keyed by id: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. An area's test scopes join the repo's and are selected by changed paths the same way; its `setup` runs once before the first of its scopes runs. Areas are never forge targets. |
| `test_scopes` | `null` | A monorepo's per-directory test commands: a list of `{paths: [...], command: "..."}` mappings, each `paths` non-empty and each `command` a non-empty string. Not synthesized from `test_command` — the two stay independently editable. |
| `intent_dir` | `null` | Where the repo's intent tree lives, relative to its root. When set, every agent in the repo is told to follow it. Its check runs as one of the repo's `test_scopes`. |
| `setup_command` | *(required — no fallback)* | Run in every new worktree before any node starts. `""` means "deliberately nothing"; an absent value stops the repo's next work item rather than guessing. On a sandboxed item it runs as `sh -c` inside the sandbox, never on the host; without docker the item stops. |
| `env` | `{}` | Literal environment variables every worker for this repo gets. A worker's environment is an allowlist, not the daemon's: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `TERM`, `TZ`, `TMPDIR`, `SSH_AUTH_SOCK`, the proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, any case), the CA variables (`SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`), Kraft's own `KRAFT_*` variables and the agent's credential variable. These values are layered on top of it. A sandboxed worker gets none of that allowlist, only what [Sandboxed workers](#sandboxed-workers) lists, which covers the daemon's proxy and an extra CA. |
| `env_passthrough` | `[]` | Names of variables to carry over from the daemon's own environment, for what the worker allowlist under `env` doesn't cover. A sandbox gets each by name, so its value never appears on the `docker` command line. |
| `local_files` | `[]` | Relative paths (no globs, no directories) to copy into every new worktree — for files `git worktree add` can't carry, like an untracked `.python-version`. Only a file the worktree's `.gitignore` covers is copied; an entry that is not, or a directory, is refused and named in its own section of the item's `worktree_prepared` event (`kraft view events`). |
| `deny_tools` | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer (see the `policy` row): frozen into each work item when it is filed, and a later addition still applies to running items. |
| `steering` | `[]` | Names of `library.yaml` steering profiles given to every agent launch on this repo, before the task's own steering. Frozen into each work item when it is filed. |
| `sandbox` | `null` | `{kind: docker, image: ..., resources: {...}, network: {...}}` — run this repo's task processes in that container, within the optional [resource limits](#resource-limits) and [network policy](#network-policy). Part of the repository policy layer: once set, no chain, node or task can turn it off or change it, limits and network policy included, and `false` here cannot turn off one a layer set. Set it here or in `policy.sandbox`, not both. See [Sandboxed workers](#sandboxed-workers). |
| `policy` | `null` | The repository policy layer: any of `allowed_tools`, `deny_tools`, `grants`, `sandbox`, `allowed_harnesses`, `timeout_minutes`, `max_attempts` and the four caps (`time_cap_minutes`, `total_time_cap_minutes`, `token_budget`, `budget_usd`, each the work item's own, within `maxima.work_item`), applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. It binds every work item filed in this repo, whatever its chain; a value `policy.yaml` refuses makes [intake](/concepts/vocabulary#intake) refuse the item. See [Policy fields](/reference/configuration/policy#policy-fields). |
| `automated_review` | `null` | The one automated reviewer a chain's `mr.automated_review` task waits for: `bot: <login>` or `check: <name>`. See [Automated review](#automated-review). |

No key on an entry passes silently. A key within two edits of a field above (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the table doesn't list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed.

## Sandboxed workers

A sandboxed task runs `docker run --init` (or `podman run`) with every capability dropped, within its [resource limits](#resource-limits), as a user that leaves what it writes yours, on rootless Docker and Podman too. Which CLI runs it, and what happens on an SELinux-enforcing host, is [sandbox.yaml](/reference/configuration/sandbox)'s. Only what is listed here reaches the container.

| What | How it reaches the container |
|---|---|
| The worktree | Mounted read-write at its real path. |
| The repository's refs | A private copy per worktree, under `$KRAFT_HOME/run/sandbox-git/`. The worker sees every branch and tag as of its session's start, and may create, move or delete refs, but only there. When a session ends, Kraft moves the item's branch in your repository to where the worker left it, and only if nothing else moved it since; any other ref the worker changed is dropped. What Kraft relies on to decide that lives outside the copy, where the worker cannot write. A branch Kraft could not move is recorded as a `sandbox_branch_not_synced` event, and its commit is kept at `refs/kraft/unsynced/<branch>`. A setup command sees the copy but never moves a branch. |
| Objects and LFS content | Your repository's `objects/` and `lfs/`, read-write: they are data a commit writes. `objects/info` (where alternates are named) and alternates themselves are read-only. |
| `HOME` | `$KRAFT_HOME/run/sandbox-home/<work item>`, read-write and kept across sessions, so an agent CLI keeps its state and can resume a paused session. A CLI config directory Kraft owns (Cursor's) lives there too, one per item. |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` and every name in `env_passthrough`, forwarded by name, so their values never appear on the `docker` command line. Values in `env` are passed literally and are visible to `ps`; keep secrets in `env_passthrough`. Logins kept in your home directory or the OS keychain do not reach a sandbox. |
| Proxy and CA | The daemon's `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` (any case), forwarded by name. A proxy on the daemon's loopback (`127.0.0.0/8`, `localhost`, `::1`) is left out, since the container's loopback is its own and nothing listens there; `kraft admin doctor` warns about it. Under a [network policy](#network-policy) none of these is forwarded: the container's proxy is Kraft's own. When there is an extra CA, [sandbox.yaml](/reference/configuration/sandbox#ca-certificates)'s `ca_bundle` or else a usable `SSL_CERT_FILE` of the daemon's, Kraft combines it with the image's own roots into one bundle, mounted read-only at `/etc/kraft/ca-bundle.pem`, and points `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`, `CODEX_CA_CERTIFICATE`, `PIP_CERT` and `npm_config_cafile` at it. No host path is forwarded. With no extra CA, nothing is mounted or set. |
| Git identity | `GIT_AUTHOR_*` and `GIT_COMMITTER_*` from the daemon's environment, else your `user.name` and `user.email` for the repository. Repository hooks never run in the container, as they never run on a worker's commits outside one. |
| Tool policy | Enforced inside the container or refused. Amp's and OpenCode's rules travel with the launch, and Amp's rules file is mounted read-only. Cursor's and Codex's policy needs Kraft's permission hook, which cannot run in a container, so a sandboxed Cursor or Codex task with `allowed_tools`, `deny_tools` or a grant other than `git-commit` is refused. Codex runs with `sandbox_mode=danger-full-access` unless a task or harness profile sets a mode other than `workspace-write`, because its own sandbox cannot start inside Docker. |

**The image** must hold the agent CLI (and, for a fallback, every harness it may fall back to) on its `PATH`, plus `git`, `sh` and CA certificates. Before a task's first launch in an image, Kraft asks the image, through its own entrypoint and with the repository's `env`, whether the command is there; an image that answers no stops the item as a configuration error before anything runs. `kraft admin doctor` checks that the runtime answers, that SELinux has an answer in `sandbox.yaml` where it enforces, that an extra CA can be read, and that the image is pulled; pull it before filing work, or the first launch pulls it inside the task's time cap.

**Not yet covered.** Without a [network policy](#network-policy), the container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable. A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch. A worker cannot reach Kraft's API, so `kraft item reply`, progress reports and an escalation's self-retry do not work from inside a sandbox. Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

Abandoning or archiving an item removes its ref store and sandbox home.

### Resource limits

```yaml
sandbox:
  kind: docker
  image: ghcr.io/acme/agent:1
  resources: {cpu: 2, memory: 4g, pids: 512}
```

| Field | Default | Means |
|---|---|---|
| `cpu` | unset | CPUs the container may use (`--cpus`), fractions allowed: `0.5`, at least `0.01`. |
| `memory` | unset | A hard memory limit: a whole number with an optional unit `b`, `k`, `m` or `g` (binary, as Docker reads them), at least `6m`. Swap is held to the same limit (`--memory-swap`) wherever the runtime can limit swap; where it cannot, the container may swap as much again, and doctor says `memory (swap unbounded)`. |
| `pids` | `4096` | Processes and threads at once (`--pids-limit`), which bounds a fork bomb. |

The limits bind every sandboxed launch of the item, its setup command included, and they are part of the sandbox: a chain, node or task cannot loosen, tighten or drop them. `resources: {}` sets none, and is the same sandbox as no `resources` at all.

A limit is applied or the task does not start. Docker runs a container without a limit whose cgroup controller is missing, with only a warning, and rootless Podman on cgroup v1 ignores every limit, so Kraft asks the runtime what it can enforce (Docker's `info`, Podman's cgroup controllers) and stops a task that sets a limit it cannot as a configuration error naming it. The fix is cgroup v2 and, for a rootless runtime, delegating the controllers to your user (systemd `Delegate=cpu memory pids` for `user@.service`). A runtime whose `info` gives no answer Kraft can read (a daemon still starting) enforces nothing as far as Kraft knows: a task that sets a limit stops, and the next launch asks again. The default `pids` is left out, never refused, where the runtime cannot limit processes. `kraft admin doctor` names the limits the runtime enforces on each sandboxed repository's row, and fails a row whose limits it cannot.

**Out of memory.** A session container is kept after it exits, without a log of its own (`--log-driver=none`; Kraft reads the output as it runs), so Kraft can ask the runtime whether the memory limit killed it, then removes it by name. The `docker run` client runs in a directory of Kraft's under `$KRAFT_HOME/run/sandbox-client/`, never the worktree. When a process in the sandbox was killed by its memory limit, Kraft records a `sandbox_oom_killed` event (`{session_id, memory}`) and the item stops for you as a configuration error naming the limit: the same limit would kill a retry the same way, so no fix loop runs. A session that still reported a result of its own (something it ran was killed and it got past it) keeps that result, with the event recorded. Only the runtime's own word counts, never exit code 137, which is also what Kraft's own kill at a time cap looks like: Docker's out-of-memory flag, and on Podman also the `oom` file its conmon writes where the client runs, since Podman on cgroup v1 never sets the flag. A setup command the limit killed stops the item the same way.

### Network policy

```yaml
sandbox:
  kind: docker
  image: ghcr.io/acme/agent@sha256:...
  network:
    install: {allow: [registry.npmjs.org, pypi.org, files.pythonhosted.org]}
    runtime: {allow: [api.github.com], deny: []}
```

Without `network`, a sandboxed task has the runtime's default network and can reach anything, the cloud metadata address included. `kraft admin doctor` warns about each sandboxed repository with open egress. With `network` set, a sandboxed task reaches only what its lists allow.

**How it works.** Each session gets a relay container with no network of its own, from `relay_image` in [sandbox.yaml](/reference/configuration/sandbox). The worker joins the relay's network namespace, so loopback is its only interface. The relay forwards `127.0.0.1:3128` there to a unix socket that belongs to that session alone, and Kraft's daemon serves the proxy on it. The worker gets `HTTP_PROXY` and `HTTPS_PROXY`, in both cases, set to `http://127.0.0.1:3128`, an empty `NO_PROXY`, and `NODE_USE_ENV_PROXY=1`. The daemon makes each allowed connection itself, through its own `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` when it has them, so a proxy on the host's loopback works here. After a daemon restart, a running session's channel is reopened with the lists it launched under, not the current configuration.

| Field | Applies to |
|---|---|
| `install` | The repository's `setup_command`. |
| `runtime` | Every task session: agents, commands, test scopes, reviews and escalations. |

Each phase takes `allow` and `deny`, lists of hosts:

- `api.github.com` names that host exactly, and `api.github.com:443` names it on one port.
- `*.example.com` covers exactly one label in front of `example.com`, not `example.com` itself.
- `*` and `**` cover everything.
- CIDRs are not accepted.

Deny wins over allow. A phase that is missing or has empty lists allows nothing, so `network: {runtime: {}}` denies everything. Only `network: {}` with no phase at all is the same as no `network`: open. The lists are part of the sandbox, so a chain, node or task cannot change them.

**Harness hosts.** An agent session's `runtime` allow list also gets the hosts its harness file declares under `network.requires`, and a `deny` still wins over them. Only `api.anthropic.com` (Claude) and `api.openai.com` (Codex) have been checked. The rest are unverified: if a CLI needs a host its harness does not list, allow it in `runtime`. OpenCode declares none, because its hosts depend on its provider.

**Always denied**, whatever the lists say:

- loopback;
- link-local (`169.254.0.0/16`, `fe80::/10`);
- cloud metadata names and addresses;
- the host's own addresses, which are what its hostname resolves to.

Kraft checks the address a name resolves to, resolving it once on the host, and then connects to that checked address. If any address a name resolves to is denied, the whole name is refused. A private address (RFC 1918, IPv6 ULA) is reachable only through an allow entry naming its host exactly, never through a wildcard.

**Refusals.** A refused request is answered `403` with a one-line reason. Kraft records one `sandbox_egress_refused` event (`{session_id, host, port, phase, reason}`) per session and host, so a retry loop does not flood the timeline: `kraft view events --type sandbox_egress_refused`.

**Clients that ignore the proxy.** Anything that does not use `HTTP(S)_PROXY`, such as git over SSH or a raw socket, has no route at all. A harness whose file declares `proxy_aware: false` (Cursor) is refused under a network policy as a configuration error before it starts.

**It fails closed.** A session that cannot get its route stops as a configuration error and runs nothing. This happens when the runtime cannot carry the channel (below), when the relay image is not pulled, or when the relay does not start. `kraft admin doctor` fails the repository's sandbox row for the first two, naming `docker pull <relay_image>` for a missing image, so pull it before filing work.

**Limits.**

- **Linux-native Docker and Podman only**, rootful or rootless. Docker Desktop and Podman machine run containers in a VM, where a container cannot connect to a host unix socket. Kraft probes for this and refuses `network` there, naming the runtime, and doctor does the same. A transport for those runtimes is not available yet.
- **Domain fronting.** The allow list is enforced on the name the client asks for and on the address Kraft dials. It does not see what travels inside the TLS connection. An allowed name on a shared CDN address can reach other names that address serves, by TLS SNI or the HTTP `Host` header. Kraft does not terminate TLS per host.

## Automated review

`automated_review` names exactly one reviewer, one of two ways. It is the reviewer a chain's `mr.automated_review` task waits for.

- `bot: <login>` settles when that forge login has reviewed the merge request's current head. On GitHub, changes requested or any inline comment is actionable (one finding per comment), and anything else is clean. A dismissed review does not count. On GitLab, the bot's unresolved discussions are actionable and its approval is clean. A GitLab approval is not tied to a commit, so a bot's approval of an earlier head still reads as clean, unless the project resets approvals on push.
- `check: <name>` settles when that check run or commit status on the head completes. Success is clean. Failure is actionable, with its output as the finding.

Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for a person rather than spending a repair.

Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet.

## Connecting a repo

`kraft repo connect` probes a `setup_command` and a test command from the repo's
markers (a justfile with a `test` recipe proposes `just test` ahead of any
manifest) and prints the test command with the file it came from; check both
before trusting them, and `kraft admin doctor` reports any connected repo still
missing a `setup_command`.

## Repository steering

`repos.yaml`'s `steering: [name, ...]` names steering profiles in
`library.yaml`, the same ones a task's `steering:` selects (see the
[library reference](/reference/configuration/library-and-chains)). There is no other steering store. When a work item is filed,
Kraft resolves each name to its profile's `instructions` and freezes the text
into the item, so editing a profile reaches items filed afterwards and never
one already filed. Every launch gets the repository's profiles first, then
the task's, in the agent's system prompt under a `## Project standards`
heading, 8 KB at most together.

A name the library doesn't define is refused when the repository is saved
(Settings, Repos) and when an item is filed, and a
library save that removes a profile a repository still names is refused too.
You write profiles on Settings, Library, which edits `library.yaml`.

A `templates/steering/*.md` directory from an older release is folded into `library.yaml` as steering profiles of the same name on first start. The old directory is kept as `templates/steering.pre-1.0/`.

This is not a place for target-repo files: Kraft never reads `CLAUDE.md`,
`AGENTS.md`, or anything else from inside the repo being worked on as a
source of process context.

## In this section

- [Workspaces](/reference/configuration/repos/workspaces): a root repository with other repositories mounted as submodules.
