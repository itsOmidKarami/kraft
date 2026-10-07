---
title: Sandboxed workers
navigation:
  title: Overview
description: The sandbox key of repos.yaml, what reaches a sandboxed worker's container, and how its refs, credentials, proxy and tool policy are handled.
---

A repository's [`sandbox`](/reference/configuration/repos#sandbox) setting runs its [workers](/concepts/vocabulary#worker) in a container. This page lists the keys of that setting and what reaches the container from the host. The other pages in this section cover each part in turn.

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
| `resources` | mapping | `docker`, optional. `kit` refuses it. | CPU, memory and process limits. See [Resource limits](/reference/configuration/sandbox/resource-limits). |
| `network` | mapping | `docker`, optional. `kit` refuses it. | The hosts a task may reach. Unset, egress is open. See [Network policy](/reference/configuration/sandbox/network-policy). |
| `credentials` | list of mappings | `docker`, optional, and it needs `network`. `kit` refuses it. | Secrets the container never holds. See [Credentials](/reference/configuration/sandbox/credentials). |

Set the key in `repos.yaml` or in `policy.sandbox`, not both. It is part of the repository policy layer: once it is set, no chain, node or task can turn it off or change it, limits and network policy included, and `sandbox: false` on a repo cannot turn off one a layer set.

## What reaches the container

A sandboxed task runs `docker run --init` (or `podman run`) with every capability dropped, within its [resource limits](/reference/configuration/sandbox/resource-limits). It runs as a user that leaves what it writes yours, on rootless Docker and Podman too.

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

A HEAD the worker points at another branch, or a rebase, merge or other git operation it leaves in progress, is never acted on: Kraft stops the item and waits for you instead (see [Troubleshooting](/troubleshooting/why-did-my-item-stop#git-state-of-the-worktree)).

## Workspace members

A workspace with members can be [sandboxed](/concepts/vocabulary#sandbox) like a single repository. A sandbox on the root or on any member (`sandbox` or `policy.sandbox`), or one that comes from a chain, node or task, wraps the item's whole checkout.

The container sees each member as it sees the root: the member's refs go through a [ref store](/concepts/vocabulary#ref-store) of its own (see [Repository refs](#repository-refs)), and the files git trusts for it are read-only.

- The member's `.git` file, and the `commondir`, `gitdir` and `config.worktree` of its git directory, are read-only, as the root's are. Every directory from the worktree down to the member is a mount point, which the worker cannot rename or remove.
- Kraft takes every path it mounts from the connected repository, never from the member's `.git`.
- A member that is not the checkout Kraft made, or that is reached through a symlink, is not mounted, and the launch does not start.
- A task fanned out to one member still mounts the whole worktree, and starts in the member.
- A member branch Kraft could not move is its own `sandbox_branch_not_synced` event, naming the repository. Abandoning or archiving the item removes the members' copies too.

Before Kraft runs git in a member, it checks that the member is still the checkout Kraft made from its connected repository. If it is not, the item stops and waits for you, naming the member. A member whose repository is not connected stops a sandboxed item too: connect it, then retry.

## Git in a sandboxed worktree

What Kraft's own git does, and does not do, in a sandboxed item's worktree.

### Nested repositories

A sandboxed [worker](/concepts/vocabulary#worker) can create a git repository of its own inside its worktree, even on a plain repository, and commit a gitlink to it. A gitlink is the entry a commit holds for a nested repository: it records one commit of that repository, not its files.

The nested repository's config belongs to the worker, so Kraft's own git never works inside it:

- Its status and diff calls compare only the commit a gitlink records.
- Its automatic commit of leftover work skips nested repositories.
- Kraft pins `submodule.recurse`, `fetch.recurseSubmodules`, `push.recurseSubmodules`, `diff.submodule`, `status.submoduleSummary` and `diff.ignoreSubmodules`, whatever your own git config says.

### When a nested repository stops the item

If a sandboxed item's worktree holds a nested repository Kraft did not create, the item stops and waits for you, and the stop names the paths. That includes:

- an untracked nested repository
- a populated gitlink
- a gitlink the branch added or moved

To go on, remove them, or run `git rm --cached` on the gitlinks, and retry. A submodule your repository already had, left unpopulated, does not stop anything.

### Git while a session runs

While one of a sandboxed item's sessions is still running, Kraft runs no git in its worktree at all.

- When one of those sessions ends, Kraft may move the item's branch in your repository to the commit the worker left, through the ref store, without reading the worktree.
- While a sandboxed session runs, the diff view says the diff will be available when the session ends.
- A task that needs the review package (the commit list and diff a review task reads) stops and waits for you.
- Kraft's automatic commit of leftover work waits for the last task of the step.

### The clean check under a sandbox

Under a sandbox, the clean check before a merge request does not look for uncommitted edits inside a submodule. It still catches a submodule whose commit moved, and each declared workspace member is checked on its own.

Leftover work is committed into a submodule's pointer only when that submodule is one of the item's declared members.

## Home, results and git identity

- **`HOME`.** `$KRAFT_HOME/run/sandbox-home/<work item>`, read-write and kept across sessions, so an agent CLI keeps its state and can resume a paused session. A CLI config directory Kraft owns (Cursor's) lives there too, one per item.
- **Results.** From `$KRAFT_HOME/run/results`, only this [work item](/concepts/vocabulary#work-item)'s own files: its sessions' result files, review packages and long instructions, read-only, and the session's own result file, read-write. Nothing of another item's is mounted.
- **Git identity.** `GIT_AUTHOR_*` and `GIT_COMMITTER_*` from the server's environment, else your `user.name` and `user.email` for the repository. Repository hooks never run in the container, as they never run on a worker's commits outside one.

Abandoning or archiving an item removes its ref store and sandbox home.

## Forwarded credentials

The container's own environment still holds the values, so `docker inspect` shows them to anyone who can reach the container runtime. A name listed under [`credentials`](/reference/configuration/sandbox/credentials) is the exception: the container holds only its [sentinel](/concepts/vocabulary#sentinel), and `docker inspect` shows that. Logins kept in your home directory or the OS keychain do not reach a sandbox.

## Tool policy

- Amp's and OpenCode's rules travel with the launch, and Amp's rules file is mounted read-only.
- Which harness's tool policy is accepted or refused, with and without a [network policy](/reference/configuration/sandbox/network-policy), is told in [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks#permission-checks-under-network).
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

## Limits

- Without a [network policy](/reference/configuration/sandbox/network-policy), the container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable.
- A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch.
- Without a network policy a worker has no channel to Kraft, and its network is not restricted: see [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks).
- Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

## In this section

- [Resource limits](/reference/configuration/sandbox/resource-limits): the CPU, memory and process limits of a container, and what happens when a memory limit kills a process.
- [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks): how a sandboxed worker reaches Kraft, and what is refused without a network policy.
- [Network policy](/reference/configuration/sandbox/network-policy): which hosts a sandboxed task may reach.
- [Credentials](/reference/configuration/sandbox/credentials): secrets the container never holds.
- [Kits](/reference/configuration/sandbox/kits): a sandbox that an image's own descriptor declares.
- [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml): the host-wide file for the container CLI, SELinux, an extra CA and the proxy.
