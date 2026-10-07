---
title: Sandboxed workers
navigation:
  title: Overview
description: The sandbox key of repos.yaml, what reaches a sandboxed worker's container, and how its refs, credentials, proxy and tool policy are handled.
---

A repository's [`sandbox`](/reference/configuration/repos#sandbox) setting runs its workers in a container. This page lists the keys of that setting and what reaches the container from the host. The other pages in this section cover each part in turn.

```yaml [config/repos.yaml]
repos:
  - path: /home/you/code/my-service
    sandbox:
      kind: docker
      image: ghcr.io/acme/agent@sha256:...
      resources: {cpu: 2, memory: 4g, pids: 512}
      network:
        install: {allow: [registry.npmjs.org]}
        runtime: {allow: [api.github.com]}
```

## The `sandbox` key

The keys depend on `kind`. A key a `kind` refuses stops `repos.yaml` from loading, naming the entry.

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `kind` | `docker` or `kit` | Both, required. | `docker` runs `image` under the keys below. `kit` runs a [Kit](/reference/configuration/sandbox/kits) as the whole sandbox. |
| `image` | string | `docker`, required. `kit` refuses it. | The image the worker runs in. See [The image](#the-image). |
| `runtime` | `docker` | `kit`, required. `docker` refuses it. | The runtime that runs the Kit. |
| `kit` | string, pinned by digest | `kit`, required. `docker` refuses it. | The Kit image, `<name>[:<tag>]@sha256:<64 hex>`. See [Kits](/reference/configuration/sandbox/kits). |
| `resources` | mapping | `docker`, optional. `kit` refuses it. | CPU, memory and process limits. See [Resource limits](/reference/configuration/sandbox/callbacks-and-limits#resource-limits). |
| `network` | mapping | `docker`, optional. `kit` refuses it. | The hosts a task may reach. Unset, egress is open. See [Network policy](/reference/configuration/sandbox/network-policy). |
| `credentials` | list of mappings | `docker`, optional, and it needs `network`. `kit` refuses it. | Secrets the container never holds. See [Credentials](/reference/configuration/sandbox/credentials). |

How to set the key in `repos.yaml` or in a policy, and why no chain, node or task can change it, is under [`sandbox`](/reference/configuration/repos#sandbox) in Repos.

## What reaches the container

A sandboxed task runs `docker run --init` (or `podman run`) with every capability dropped, within its [resource limits](/reference/configuration/sandbox/callbacks-and-limits#resource-limits). It runs as a user that leaves what it writes yours, on rootless Docker and Podman too.

[sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml) says which CLI runs it, and what happens on an SELinux-enforcing host. Only what is listed here reaches the container:

| What | How it reaches the container |
|---|---|
| The [worktree](/concepts/vocabulary#worktree) | Mounted read-write at its real path. |
| The repository's refs | A private copy per worktree, under `$KRAFT_HOME/run/sandbox-git/`. See [Repository refs](#repository-refs). |
| Objects and LFS content | Your repository's `objects/` and `lfs/`, read-write: they are data a commit writes. `objects/info` (where alternates are named) and alternates themselves are read-only. |
| Workspace members | Each member's own repository, the same way as the root's: a private copy of its refs, from which Kraft moves the item's branch in that repository, and its objects. See [Workspace members](#workspace-members). |
| `HOME`, results, git identity | See [Home, results and git identity](#home-results-and-git-identity). |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, and every name in `env_passthrough` and `env`, forwarded by name, so their values never appear on the `docker` command line or in `ps`. See [Forwarded credentials](#forwarded-credentials). |
| Proxy and CA | The server's proxy variables and an extra CA. [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml#proxy) says which, and how. |
| Tool policy | Enforced inside the container or refused. See [Tool policy](#tool-policy). |

## Repository refs

The worker sees every branch and tag as of its session's start, and may create, move or delete refs, but only in that copy.

When a session ends, Kraft moves the item's branch in your repository to where the worker left it, and only if nothing else moved it since. Any other ref the worker changed is dropped. What Kraft relies on to decide that lives outside the copy, where the worker cannot write.

A branch Kraft could not move is recorded as a `sandbox_branch_not_synced` event, and its commit is kept at `refs/kraft/unsynced/<branch>`. A setup command sees the copy but never moves a branch.

A HEAD the worker points at another branch, or a rebase, merge or other git operation it leaves in progress, is never acted on: Kraft stops the item and waits for you instead (see [Troubleshooting](/troubleshooting/why-did-my-item-stop#git-and-the-worktree)).

## Workspace members

- The member's `.git` file, and the `commondir`, `gitdir` and `config.worktree` of its git directory, are read-only, as the root's are. Every directory from the worktree down to the member is a mount point, which the worker cannot rename or remove.
- Kraft takes every path it mounts from the connected repository, never from the member's `.git`.
- A member that is not the checkout Kraft made, or that is reached through a symlink, is not mounted, and the launch does not start.
- A task fanned out to one member still mounts the whole worktree, and starts in the member.
- A member branch Kraft could not move is its own `sandbox_branch_not_synced` event, naming the repository. Abandoning or archiving the item removes the members' copies too.

See [Workspaces](/reference/configuration/repos/workspaces#sandboxing).

## Home, results and git identity

- **`HOME`.** `$KRAFT_HOME/run/sandbox-home/<work item>`, read-write and kept across sessions, so an agent CLI keeps its state and can resume a paused session. A CLI config directory Kraft owns (Cursor's) lives there too, one per item.
- **Results.** From `$KRAFT_HOME/run/results`, only this [work item](/concepts/vocabulary#work-item)'s own files: its sessions' result files, review packages and long instructions, read-only, and the session's own result file, read-write. Nothing of another item's is mounted.
- **Git identity.** `GIT_AUTHOR_*` and `GIT_COMMITTER_*` from the server's environment, else your `user.name` and `user.email` for the repository. Repository hooks never run in the container, as they never run on a worker's commits outside one.

Abandoning or archiving an item removes its ref store and sandbox home.

## Forwarded credentials

The container's own environment still holds the values, so `docker inspect` shows them to anyone who can reach the container runtime. A name listed under [`credentials`](/reference/configuration/sandbox/credentials) is the exception: the container holds only its [sentinel](/concepts/vocabulary#sentinel), and `docker inspect` shows that. Logins kept in your home directory or the OS keychain do not reach a sandbox.

## Tool policy

- Amp's and OpenCode's rules travel with the launch, and Amp's rules file is mounted read-only.
- Codex's policy is held by Kraft's permission hook, which reaches Kraft only through a [network policy](/reference/configuration/sandbox/network-policy)'s channel (see [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks-and-limits#callbacks-from-a-sandbox)). Without `network:`, a Codex or Cursor task with `allowed_tools`, `deny_tools` or a grant other than `git-commit` is refused.
- Claude asks Kraft through an MCP tool on every launch, so a sandboxed Claude task without `network:` is refused whatever its policy.
- Codex runs with `sandbox_mode=danger-full-access` unless a task or [harness profile](/concepts/vocabulary#harness-profile) sets a mode other than `workspace-write`, because its own sandbox cannot start inside Docker.

## The image

It must hold the agent CLI (and, for a fallback, every [harness](/concepts/vocabulary#harness) it may fall back to) on its `PATH`, plus `git`, `sh` and CA certificates, and `curl` under a network policy (the `kraft` shim uses it).

Before a task's first launch in an image, Kraft asks the image, through its own entrypoint and with the repository's `env`, whether the command is there. An image that answers no stops the item as a configuration error before anything runs.

`kraft admin doctor` checks:

- that the runtime answers
- that SELinux has an answer in `sandbox.yaml` where it enforces
- that an extra CA can be read
- that the image is pulled

Pull it before filing work, or the first launch pulls it inside the task's time cap.

## Not yet covered

- Without a [network policy](/reference/configuration/sandbox/network-policy), the container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable.
- A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch.
- Without a network policy a worker has no channel to Kraft, and its network is not restricted: see [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks-and-limits#callbacks-from-a-sandbox).
- Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

## In this section

- [Callbacks and resource limits](/reference/configuration/sandbox/callbacks-and-limits): how a sandboxed worker reaches Kraft, and the CPU, memory and process limits of a container.
- [Network policy](/reference/configuration/sandbox/network-policy): which hosts a sandboxed task may reach.
- [Credentials](/reference/configuration/sandbox/credentials): secrets the container never holds.
- [Kits](/reference/configuration/sandbox/kits): a sandbox that an image's own descriptor declares.
- [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml): the host-wide file for the container CLI, SELinux, an extra CA and the proxy.
