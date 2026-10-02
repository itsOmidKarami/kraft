---
title: Repos
navigation:
  title: Overview
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
| `enabled` | `true` | Set `false` to keep auto-intake off this repo without disconnecting it. It governs auto-intake only: you can still file and run items on a disabled repo, and running items keep going. `kraft repo connect` saves a repo it found no test command for with `enabled: false`; an item on it stops at `verify` until it has a `test_command` or `test_scopes`. An absent key counts as enabled. Kraft refuses an edit that would leave an enabled repo with neither a `test_command` (`""` counts) nor `test_scopes`. |
| `managed` | `true` | Keeps a human-connected repo out of Templates, Repos' "Detected · not connected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain_template` | — | Which chain template a work item on this repo uses when none is named explicitly, however it is filed: `kraft item create`, the MCP tool, the board, the API, `POST /api/triggers` or auto-intake. Unset, it is `default`. |
| `forge` | `null` | `github` or `gitlab`, which forge adapter `backend: auto` resolves to for this repo. `kraft repo connect` sets it from the host of the repo's remote: `github.com`, `gitlab.com`, or a self-hosted host that names one (`gitlab.example.com`). |
| `project` | `null` | The GitLab project path, when `forge: gitlab`. A legacy `gitlab_project` key still reads. |
| `models` | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude: opus`): above the profile's own `defaults:`, below a task's `model:` or agent `profile:` and the work item's override. Keyed by profile because one model name means nothing to another provider. The retired `default_model` key is dropped with a warning. |
| `test_command` | `null` | The command CI actually runs for this repo — what the changed-test-scope verification runs, as one scope over every path. It runs from the worktree root without a shell. `""` declares a repo with no tests (docs, infrastructure): verification passes and its session says so. A repo with neither this nor `test_scopes` stops that verification for a human rather than inventing a command. |
| `areas` | `{}` | Path-scoped contexts inside this repo, keyed by id: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. An area's test scopes join the repo's and are selected by changed paths the same way; its `setup` runs once before the first of its scopes runs. Areas are never forge targets. |
| `test_scopes` | `null` | A monorepo's per-directory test commands: a list of `{paths: [...], command: "..."}` mappings, each `paths` non-empty and each `command` a non-empty string. Every command runs from the repository root, without a shell, so one for a project in a subdirectory names that directory itself: `sh -c 'cd frontend && npm test'` (what connect proposes) or `npm --prefix frontend test`, not `npm test`. Not synthesized from `test_command` — the two stay independently editable. |
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
| `ci_checks` | `true` | Set `false` on a repo with no CI: both CI waits, a chain's `mr.ci` task before the merge and its `mr.post_merge_ci` task after it, then pass at once, each recording `ci_not_configured`, instead of waiting on checks that never come. It does not touch `mr.automated_review` or `mr.external_approval`. |

No key on an entry passes silently. A key within two edits of a field above (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the table doesn't list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed.

## Sandboxed workers

A sandboxed task runs `docker run --init` (or `podman run`) with every capability dropped, within its [resource limits](#resource-limits), as a user that leaves what it writes yours, on rootless Docker and Podman too. Which CLI runs it, and what happens on an SELinux-enforcing host, is [sandbox.yaml](/reference/configuration/sandbox)'s. Only what is listed here reaches the container.

| What | How it reaches the container |
|---|---|
| The worktree | Mounted read-write at its real path. |
| The repository's refs | A private copy per worktree, under `$KRAFT_HOME/run/sandbox-git/`. The worker sees every branch and tag as of its session's start, and may create, move or delete refs, but only there. When a session ends, Kraft moves the item's branch in your repository to where the worker left it, and only if nothing else moved it since; any other ref the worker changed is dropped. What Kraft relies on to decide that lives outside the copy, where the worker cannot write. A branch Kraft could not move is recorded as a `sandbox_branch_not_synced` event, and its commit is kept at `refs/kraft/unsynced/<branch>`. A setup command sees the copy but never moves a branch. A HEAD the worker points at another branch, or a rebase, merge or other git operation it leaves in progress, is never acted on: Kraft stops the item for you instead (see [Troubleshooting](/get-started/troubleshooting#why-did-my-item-stop)). |
| Objects and LFS content | Your repository's `objects/` and `lfs/`, read-write: they are data a commit writes. `objects/info` (where alternates are named) and alternates themselves are read-only. |
| Workspace members | Each member's own repository, the same way as the root's: a private copy of its refs, from which Kraft moves the item's branch in that repository, and its objects. The member's `.git` file, and the `commondir`, `gitdir` and `config.worktree` of its git directory, are read-only, as the root's are, and every directory from the worktree down to the member is a mount point, which the worker cannot rename or remove. Kraft takes every path it mounts from the connected repository, never from the member's `.git`. A member that is not the checkout Kraft made, or that is reached through a symlink, is not mounted, and the launch does not start. A task fanned out to one member still mounts the whole worktree, and starts in the member. A member branch Kraft could not move is its own `sandbox_branch_not_synced` event, naming the repository, and abandoning or archiving the item removes the members' copies too. See [Workspaces](/reference/configuration/repos/workspaces#sandboxing). |
| `HOME` | `$KRAFT_HOME/run/sandbox-home/<work item>`, read-write and kept across sessions, so an agent CLI keeps its state and can resume a paused session. A CLI config directory Kraft owns (Cursor's) lives there too, one per item. |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, and every name in `env_passthrough` and `env`, forwarded by name, so their values never appear on the `docker` command line or in `ps`. The container's own environment still holds them, so `docker inspect` shows them to anyone who can reach the container runtime. A name listed under [`credentials`](#credentials) is the exception: the container holds only its sentinel, and `docker inspect` shows that. Logins kept in your home directory or the OS keychain do not reach a sandbox. |
| Proxy and CA | The daemon's `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` (any case), forwarded by name. A proxy on the daemon's loopback (`127.0.0.0/8`, `localhost`, `::1`) is left out, since the container's loopback is its own and nothing listens there; `kraft admin doctor` warns about it. Under a [network policy](#network-policy) none of these is forwarded: the container's proxy is Kraft's own. When there is an extra CA, [sandbox.yaml](/reference/configuration/sandbox#ca-certificates)'s `ca_bundle` or else a usable `SSL_CERT_FILE` of the daemon's, Kraft combines it with the image's own roots into one bundle, mounted read-only at `/etc/kraft/ca-bundle.pem`, and points `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`, `CODEX_CA_CERTIFICATE`, `PIP_CERT` and `npm_config_cafile` at it. No host path is forwarded. With no extra CA, nothing is mounted or set. |
| Git identity | `GIT_AUTHOR_*` and `GIT_COMMITTER_*` from the daemon's environment, else your `user.name` and `user.email` for the repository. Repository hooks never run in the container, as they never run on a worker's commits outside one. |
| Tool policy | Enforced inside the container or refused. Amp's and OpenCode's rules travel with the launch, and Amp's rules file is mounted read-only. Codex's policy is held by Kraft's permission hook, which reaches Kraft only through a [network policy](#network-policy)'s channel (see [Callbacks from a sandbox](#callbacks-from-a-sandbox)); without `network:` a Codex or Cursor task with `allowed_tools`, `deny_tools` or a grant other than `git-commit` is refused. Claude asks Kraft through an MCP tool on every launch, so a sandboxed Claude task without `network:` is refused whatever its policy. Codex runs with `sandbox_mode=danger-full-access` unless a task or harness profile sets a mode other than `workspace-write`, because its own sandbox cannot start inside Docker. |

**The image** must hold the agent CLI (and, for a fallback, every harness it may fall back to) on its `PATH`, plus `git`, `sh` and CA certificates, and `curl` under a network policy (the `kraft` shim uses it). Before a task's first launch in an image, Kraft asks the image, through its own entrypoint and with the repository's `env`, whether the command is there; an image that answers no stops the item as a configuration error before anything runs. `kraft admin doctor` checks that the runtime answers, that SELinux has an answer in `sandbox.yaml` where it enforces, that an extra CA can be read, and that the image is pulled; pull it before filing work, or the first launch pulls it inside the task's time cap.

**Not yet covered.** Without a [network policy](#network-policy), the container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable. A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch. Without a network policy a worker cannot reach Kraft at all: see [Callbacks from a sandbox](#callbacks-from-a-sandbox). Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

Abandoning or archiving an item removes its ref store and sandbox home.

### Callbacks from a sandbox

Under a [network policy](#network-policy) a sandboxed worker reaches Kraft through its session's own channel, the one its proxy runs on. Every such container has Kraft's `kraft` shim, a `sh` and `curl` script, mounted read-only at `/opt/kraft/bin` and first on its `PATH` (the image's own `PATH` after it). The shim has only the verbs a worker needs: `kraft item progress K`, `kraft item reply THREAD --body ...`, `kraft item retry`, `kraft view show|threads` and `kraft admin permission-hook HARNESS`. There is no `kraft view diff` or `compare` inside: Kraft reads no git in a worktree while a sandboxed session runs in it, and the worker has `git` itself. It sends each to `http://kraft` through the proxy, which answers it in the Kraft server itself, never forwarded anywhere.

A call acts as the session whose channel it came in on, whatever headers or token the worker sends, and only on that session's own work item: its progress and retry, replies on its own item's threads, its own permission asks, and reads of its own item. Anything else is answered `403` and recorded as a `sandbox_egress_refused` event, like a refused host. No allow list entry is needed for any of this.

Permission checks under `network:`:

- **Codex.** Its hook is the shim's `admin permission-hook codex`. The hash Codex needs to trust the hook is asked of the image's own `codex`, in a short container with no network, since Codex silently skips a hook it does not trust and runs the call. When that cannot be done (the image's Codex does not start, or does not trust the hook), the task is refused. When the shim gets no answer from Kraft, it prints Codex's own deny.
- **Claude.** Launched with Kraft's MCP server at `http://kraft/mcp` and no other (`--strict-mcp-config`), which answers its permission tool for that session alone.
- **Cursor** ignores a proxy, so it is refused under a network policy.

**Without `network:`** the container has no route to Kraft. A sandboxed Claude task is refused before it starts (its permission tool would be missing and the CLI would exit), as is a Codex or Cursor task with a tool policy. Prompts do not tell the worker to run `kraft`: it tags its commits with the task number instead of reporting progress, and says what it did about each review thread in its result. No reply agent is launched for review threads: a `comment` review answers `reply_agent: false`, and the item records a `reply_agent_skipped` event saying why. An escalation turn cannot resume the chain itself; a person retries it.

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

**Out of memory.** A session container is kept after it exits, without a log of its own (`--log-driver=none`; Kraft reads the output as it runs), so Kraft can ask the runtime whether the memory limit killed it, then removes it by name. The `docker run` client runs in a directory of Kraft's under `$KRAFT_HOME/run/sandbox-client/`, never the worktree. When a process in the sandbox was killed by its memory limit, Kraft records a `sandbox_oom_killed` event (`{session_id, memory, confirmed}`) and the item stops for you as a configuration error naming the limit: the same limit would kill a retry the same way, so no fix loop runs. A session that still reported a result of its own (something it ran was killed and it got past it) keeps that result, with the event recorded. The runtime's own word makes a confirmed kill: Docker's out-of-memory flag, and on Podman also the `oom` file its conmon writes where the client runs, since Podman on cgroup v1 never sets the flag. The runtime can set its flag a moment after the container exits, so for a container that exited 137 under a memory limit Kraft asks again for up to two seconds. Docker on cgroup v2 can also drop the flag altogether ([moby#41929](https://github.com/moby/moby/issues/41929)), so a container that exited 137 under a memory limit, that Kraft did not stop itself (a pause, a time cap, a cancel), and that is still unflagged after those two seconds counts as an unconfirmed kill: `confirmed: false`, and the stop says the runtime did not confirm it was the limit. Exit 137 from a container with no memory limit is never counted. A setup command the limit killed stops the item the same way.

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

**Docker Desktop and Podman machine.** These run containers in a VM, where a container cannot connect to a host unix socket. Kraft checks once per runtime whether a container can connect to a host socket. When it cannot, and the runtime reports that it runs in a VM (Docker Desktop's `OperatingSystem`, Podman's `ServiceIsRemote`), Kraft carries the channel in two hops instead. The relay the worker joins, still with no network, forwards to a socket in a volume made for that session. A second relay, on the runtime's default network, forwards that socket to one mutual-TLS listener the daemon keeps on `127.0.0.1`, which it reaches as `host.docker.internal` (Docker) or `host.containers.internal` (Podman). Both relays run `relay_image`. The listener trusts only a client certificate from Kraft's own CA, kept under `run/ca/`, and takes the session from that certificate, one per session, which only the second relay mounts. The worker shares no network, volume or mount with it. `kraft admin doctor` names the transport in the repository's sandbox row and checks that the listener answers.

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

Kraft checks the address a name resolves to, resolving it once on the host, and then connects to that checked address. Through an upstream proxy (the daemon's own `HTTP_PROXY` or `HTTPS_PROXY`), Kraft still checks the address, but the upstream resolves the name again and connects to whatever it gets. If any address a name resolves to is denied, the whole name is refused. An IPv4 address carried inside an IPv6 one (IPv4-mapped, IPv4-compatible, 6to4, NAT64 `64:ff9b::/96`) is checked as that IPv4 address as well. Any other non-public address (RFC 1918, IPv6 ULA, the shared `100.64.0.0/10` range, reserved ranges) is reachable only through an allow entry naming its host exactly, never through a wildcard.

**Refusals.** A refused request is answered `403` with a one-line reason. Kraft records one `sandbox_egress_refused` event (`{session_id, host, port, phase, reason}`) per session and host, so a retry loop does not flood the timeline: `kraft view events --type sandbox_egress_refused`. After 100 hosts, one last event with `suppressed: true` says later refusals in that session are not recorded; they are still refused.

**Clients that ignore the proxy.** Anything that does not use `HTTP(S)_PROXY`, such as git over SSH or a raw socket, has no route at all. A harness whose file declares `proxy_aware: false` (Cursor) is refused under a network policy as a configuration error before it starts.

**It fails closed.** A session that cannot get its route stops as a configuration error and runs nothing. This happens when the socket check does not run, when a container cannot connect to the socket on a runtime that does not report a VM (an SELinux denial, or the permissions on `run/sn/`), when the relay image is not pulled, or when a relay does not start. `kraft admin doctor` fails the repository's sandbox row for the first three, naming `docker pull <relay_image>` for a missing image, so pull it before filing work.

**Limits.**

- **Docker Desktop and Podman machine: seven days per session.** A session's client certificate expires after seven days; a session that runs longer loses its route and fails closed. The listener keeps its port across a daemon restart. If another program has taken that port by then, the listener moves to a new one, and sessions started before the restart have no route until they are retried.
- **Domain fronting.** The allow list is enforced on the name the client asks for and on the address Kraft dials. It does not see what travels inside the TLS connection. An allowed name on a shared CDN address can reach other names that address serves, by TLS SNI or the HTTP `Host` header. Kraft terminates TLS only for a managed [credential](#credentials)'s host.

### Credentials

```yaml
sandbox:
  kind: docker
  image: ghcr.io/acme/agent@sha256:...
  network:
    runtime: {allow: [registry.example.com]}
  credentials:
    - env: ANTHROPIC_API_KEY          # its harness says how
    - env: REGISTRY_TOKEN             # the repository's own, in full
      service: registry
      inject: [{domain: registry.example.com, header: authorization, format: "Bearer %s"}]
```

A variable listed under `credentials` never reaches the container. The container gets a sentinel in its place, and Kraft's egress proxy puts the daemon's own value into the named header on requests to the named host. This holds for every sandboxed launch of the item: agent sessions, subprocess tasks, test scopes and setup commands, whose code the worker may have written. A variable not listed passes through as the Credentials row above describes. Nothing is managed unless the repository lists it.

- `env: NAME` alone takes the rest from the harness file of the session's CLI. Claude declares `ANTHROPIC_API_KEY` (`x-api-key`) and `CLAUDE_CODE_OAUTH_TOKEN` (`Authorization: Bearer`) on `api.anthropic.com`, Codex `CODEX_API_KEY` (`Authorization: Bearer`) on `api.openai.com`, and Gemini `GEMINI_API_KEY` (`x-goog-api-key`) on `generativelanguage.googleapis.com`. Codex's is `CODEX_API_KEY` because `codex exec` does not send an `OPENAI_API_KEY`. Verified against the real CLI, in a Docker and a Podman sandbox, by `e2e(<cli>)` in `tests/worker/test_credentials_docker.py` (run with `KRAFT_E2E=1`): `ANTHROPIC_API_KEY` with Claude Code 2.1.284, `CODEX_API_KEY` with codex-cli 0.158.0 (which opens a WebSocket to the host first; against the test's fake host it fell back to HTTPS, and whether it does when the real host's `101` ends the connection is untested), and `GEMINI_API_KEY` with Gemini CLI 0.61.0. `CLAUDE_CODE_OAUTH_TOKEN` is unverified. A CLI that pins its host's certificate, or needs HTTP/2, cannot be managed this way, and its variable should stay unlisted. A CLI whose harness does not declare the name gets the sentinel and nothing injected.
- A repository's own credential gives `service`, and `inject`: a list of an exact `domain`, a `header`, and optionally a `format` holding `%s` where the value goes. `sentinel` sets what the container sees, `kraft-proxy-managed` unless the harness says otherwise.
- `phase` limits a credential to the launches of those phases: `[install]` for the setup command, `[runtime]` for agent sessions, subprocess tasks and test scopes. A launch in another phase gets neither its sentinel nor its value. Unset, it is in both.
- `source` names the daemon's own environment variable its value is read from, instead of `env` in the worker's environment. The worker's environment is never read for it, not even as a fallback, and the `source` variable is kept out of the container as `env` is. Set but empty counts as no value.
- `credentials` needs `network`. A repository's own credential's `domain` must be named in the `allow` list of each phase its `phase` lists (either phase, when unset), not only covered by a wildcard or a harness's hosts, and not denied there. A variable can be listed once per phase, and one header on one host set by one credential per phase, so one name can serve two entries whose `phase` lists do not overlap. Anything else fails when repos.yaml loads.
- `credentials` is part of the sandbox, so a chain, node or task cannot change it.

**How it works.** When a session manages a credential, its container's CA bundle (mounted at `/etc/kraft/ca-bundle.pem`, as for an extra CA) also holds Kraft's own CA. A `CONNECT` to a host a managed credential goes to, where the session's phase allows that host by name, is not tunnelled: the proxy answers the worker's TLS itself with a certificate Kraft's CA issued for exactly that host, refusing a handshake that names another, and makes its own TLS connection to the real host, verified against the daemon's own roots plus the extra CA, never Kraft's. It then passes each request on, one at a time, keep-alive and pipelined included. A request whose `Host` is not the host it connected to, or where any instance of the credential's header is not exactly the sentinel in its `format`, or that carries no managed credential's sentinel at all, is answered 403 and recorded as a refused host, and never reaches the host, so the worker cannot use the route with a key of its own. Otherwise every instance of the header is dropped and one set to the real value. A daemon that has no value refuses those requests rather than forward the sentinel. Every other host stays a blind tunnel, including one allowed only through a wildcard, and a plain `http://` request to a managed host is refused.

The value is read the way the rest of the worker's environment is: the daemon's own, a name in `env_passthrough`, or `env`; a credential with `source` reads the daemon's own variable of that name alone. Kraft keeps it only in the daemon's memory and puts it only in the header on its way out. It is not in the container's environment, argv, mounts, `docker inspect` (which shows the sentinel), the `docker` client's environment, an event, a log line or the session's row. `kraft admin doctor` lists, for each sandboxed repository with `credentials`, which names are managed and on which hosts, and which names a harness declares that still pass through; a managed name with no value (under its `source`, for one that has one) fails the row, and a credential with `phase` says which. A name listed alone that no harness declares, such as `OPENAI_API_KEY` for Codex (whose name is `CODEX_API_KEY`), warns, naming what each harness does declare: it holds only its sentinel and nothing injects it.

**Limits.**

- **HTTP/1.1 only.** The terminated connection offers only `http/1.1`, in both directions. A CLI that needs HTTP/2 does not get it, and a WebSocket or any other upgrade does not work to a managed host: a `101` ends the connection.
- **Responses pass back as they are.** Only the request is rewritten. A host that echoed the real key in a response header or body would hand it to the worker; Kraft does not strip it.
- **Listing a credential does not clean the item's `HOME`.** A sandboxed item keeps one `HOME`, `$KRAFT_HOME/run/sandbox-home/<work item id>`, across its sessions. A session that ran before the variable was listed had the real value, and its CLI may have saved it there, in a login or config file. To start clean, pause the item and delete that directory; Kraft makes an empty one at the next session, and that session starts without the CLI's saved state, so it cannot resume the earlier one. `kraft view show` prints the work item id.
- **After a daemon restart,** a running session's credentials are restored with the rules it launched with and their values read again through the repository entry it launched with. If that repository has been disconnected, its `repos.yaml` no longer loads, or the value is gone, its requests carrying the sentinel are refused until the item is retried.

### Kits

**Operator-authored Kits only.** Docker's published Kits (`docker/sbx-kit-shell`, `docker/sbx-kit-claude`) require capabilities Kraft does not enforce, so Kraft refuses them. Build a Kit for Kraft instead: [Build a worker Kit](/guides/worker-kit) gives one for Claude.

```yaml
sandbox:
  kind: kit
  runtime: docker
  kit: registry.example.com/acme/kraft-worker-claude@sha256:<64 hex>
```

A [Docker Sandbox Kit](https://github.com/docker/sandbox-kit-spec) is an image whose manifest carries a descriptor of what its workload may reach and hold. Kraft reads Kits written to the spec's `v3.0.0-m.7`. Under `kind: kit` the Kit is the whole sandbox: `runtime` and `kit` are required, and `image`, `network`, `resources` and `credentials` are refused. `kit` must be pinned by digest (`<name>[:<tag>]@sha256:<64 hex>`); a tag alone is refused when repos.yaml loads. Like any sandbox, a chain, node or task cannot change it, and a chain's or library's policy can set it too.

Kraft runs exactly one Kit, a `kind: workload`, and composes nothing:

| Capability | What Kraft does |
|---|---|
| `network-policy@1` | Enforced, as the [network policy](#network-policy), phase for phase. Required: a Kit without one is refused. |
| `credential@1` | Enforced for a proxy-managed `apiKey` with a `name` and header `inject` rules, as a [credential](#credentials) scoped to its `phase`. Its value is the daemon's variable that [sandbox.yaml](/reference/configuration/sandbox)'s `credentials` binds to its `service`. An `oauth` beside the `apiKey` is recorded as ignored. |
| `resources@1` | Enforced: `cpu` and `memory` as the [resource limits](#resource-limits) (`2gib` is `2g`, `cpu: 0` is no limit). `gpu` is not. |
| `agent-sessions@1` | Accepted and not applied: the harness file builds the command, as for `kind: docker`. |
| Any other type, or a form above Kraft does not enforce | Refused when required, naming it. Skipped when `optional`. |

A `kind: mixin`, a `requires`, a capability group, a `${{ }}` reference in a capability, an `args` entry exported to `env`, and a descriptor over 512 KiB are refused too.

**What a launch runs.** The Kit, lowered to the `kind: docker` sandbox it describes: the Kit's image by digest, its network lists, its credentials and its limits, and nothing else. The harness's `network.requires` hosts are not added, so a host the Kit leaves out is refused and recorded like any other. The image's `Entrypoint` is kept and the harness's command replaces its `Cmd`, so a Kit whose entrypoint is the agent CLI itself does not work. The worker session records `docker` as its backend.

**When it is read.** Kraft reads the descriptor with your runtime's own CLI and registry login (`docker manifest inspect`; Podman, which reads only an index that way, pulls a single-manifest Kit and reads its image), before an item's worktree is made, and caches it under `$KRAFT_HOME/run/kit/` by its digest. A Kit that cannot be fetched or is refused stops the item for you, naming the Kit and why: see [Troubleshooting](/get-started/troubleshooting#a-kit-is-refused). Each task dispatched under it records a [`sandbox_kit_resolved`](/reference/events) event once per Kit, with what was skipped or ignored.

**Doctor.** `kraft admin doctor` fetches and lowers each Kit repository's Kit, fails its sandbox row when it cannot, and runs the sandbox, egress, proxy and credentials rows on what it lowers to. It warns about each harness host the Kit does not allow (`kit hosts`) and each credential service with no binding (`kit credentials`).

## Automated review

`automated_review` names exactly one reviewer, one of two ways. It is the reviewer a chain's `mr.automated_review` task waits for.

- `bot: <login>` settles when that forge login has reviewed the merge request's current head. On GitHub, changes requested or any inline comment is actionable (one finding per comment), and anything else is clean. A dismissed review does not count. On GitLab, the bot's unresolved discussions are actionable and its approval is clean. A GitLab approval is not tied to a commit, so a bot's approval of an earlier head still reads as clean, unless the project resets approvals on push.
- `check: <name>` settles when that check run or commit status on the head completes. Success is clean. Failure is actionable, with its output as the finding.

Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for a person rather than spending a repair.

Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet.

## Connecting a repo

`kraft repo connect` proposes a `setup_command`, a `test_command` and, for a
repo with more than one project in it, `test_scopes`. It reads them from the
repo's own task runner (a justfile, Makefile, Taskfile or mise task,
`script/test`), from its CI, and from its toolchain's lockfile, in that order.
It prints each command with the file it came from and lists what else it found.
See [Detectors](/reference/configuration/repos/detectors) for the ranking, and
for how `detectors.yaml` teaches Kraft your own conventions.

Check the proposal before you trust it. `--verify` runs it once in a throwaway
worktree; `--test-command`, `--setup-command` and `--no-tests` replace it; in a
terminal, connect asks which candidate to use. `kraft admin doctor` reports any
connected repo still missing a `setup_command`.

Some repos get no proposal, on purpose. A `pyproject.toml` declaring a project,
with no lockfile beside it (`uv.lock`, `poetry.lock`, `pdm.lock`, a Pipfile),
gets neither command. `uv sync` and `uv run` both write a `uv.lock` when there
is none, and the worker would commit it on the item's branch. Kraft does not
fall back to a `go.mod` or `package.json` beside it either. A lockfile-based
install still stands (`npm ci` beside a `package-lock.json`), and so does a
task runner's `test` recipe in that directory, which comes first.

For tests, such a directory at the root means no test command for the whole
repo. One below the root means the same, unless the root's command runs its
tests anyway. Say `backend/` has one and `frontend/` has a `package.json`. A
change to `backend/` matches no scope, and a change that matches none runs
every scope, so a `frontend/**` scope alone would pass it on `npm test` with the
Python tests never run. Kraft proposes nothing instead, and connect says which
directory stopped it. The root covers the directory when its command is one you
give with `--test-command`, its task runner's `test` recipe, or a root
`uv.lock`'s (a uv workspace): the stopped directory then gets no scope of its
own, and the root scope, running that command, covers it.

Connect says when it found no command. With no `test_command` it saves the
repo disabled. Set `setup_command` yourself, or `""` if the repo needs no
preparation. Set `test_command`, then `enabled: true`.

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
(Templates, Repos) and when an item is filed, and a
library save that removes a profile a repository still names is refused too.
You write profiles in Templates, Library, which edits `library.yaml`.

A `templates/steering/*.md` directory from an older release is folded into `library.yaml` as steering profiles of the same name on first start. The old directory is kept as `templates/steering.pre-1.0/`.

This is not a place for target-repo files: Kraft never reads `CLAUDE.md`,
`AGENTS.md`, or anything else from inside the repo being worked on as a
source of process context.

## Where a repo's value comes from

A repo that sets no `steering:` gets the steering profiles its default chain's
tasks select, from the library. One that sets none of `deny_tools` or `models`
or a `policy:` key gets the instance's: `deny_tools` and `policy:` come from
`policy.yaml`, and a repo's own list only adds to it. A repo's `policy:` may
tighten the instance's safety layer but not relax it; a draft of `repos.yaml`
shows that as a problem naming the repo and the field before it is published,
the same refusal a run gives. Each field's source (`repo`, `library` or
`default`) comes with the value in the `repos` draft's `resolved` view; see the
[HTTP API reference](/reference/http-api).

## In this section

- [Detectors](/reference/configuration/repos/detectors): how `kraft repo connect` proposes setup and test commands, and `detectors.yaml`.
- [Workspaces](/reference/configuration/repos/workspaces): a root repository with other repositories mounted as submodules.
