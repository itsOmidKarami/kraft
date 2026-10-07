---
title: Sandboxed workers
navigation:
  title: Overview
description: The sandbox key of repos.yaml, what reaches a sandboxed worker's container, and how its home, credentials, image and tool policy are handled.
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
| `network` | mapping | `docker`, optional. `kit` refuses it. | The hosts a task may reach. Set it or `unrestricted_network`: a launch into a sandbox with neither is refused. See [Network policy](/reference/configuration/sandbox/network-policy). |
| `unrestricted_network` | boolean | `docker`, optional, default `false`. `kit` refuses it, and so does `network` beside it. | `true` lets a launch run with no `network`: the worker's network is not restricted and gates are not enforced. See [Network policy](/reference/configuration/sandbox/network-policy#without-a-network-policy). |
| `credentials` | list of mappings | `docker`, optional, and it needs `network`. `kit` refuses it. | Secrets the container never holds. See [Credentials](/reference/configuration/sandbox/credentials). |

Set the key in `repos.yaml` or in `policy.sandbox`, not both. It is part of the repository policy layer: once it is set, no chain, node or task can turn it off or change it, limits and network policy included, and `sandbox: false` on a repo cannot turn off one a layer set.

## What reaches the container

A sandboxed task runs `docker run --init` (or `podman run`) with every capability dropped, within its [resource limits](/reference/configuration/sandbox/resource-limits). It runs as a user that leaves what it writes yours, on rootless Docker and Podman too.

[sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml) says which CLI runs it, and what happens on an SELinux-enforcing host. Only what is listed here reaches the container:

| What | How it reaches the container |
|---|---|
| The [worktree](/concepts/vocabulary#worktree) | Mounted read-write at its real path. |
| The repository's refs | A private copy per worktree, under `$KRAFT_HOME/run/sandbox-git/`. See [Repository refs](/reference/configuration/sandbox/git-in-a-sandbox#repository-refs). |
| Objects and LFS content | Your repository's `objects/` and `lfs/`, read-write: they are data a commit writes. `objects/info` (where alternates are named) and alternates themselves are read-only. |
| Workspace members | Each member's own repository, the same way as the root's: a private copy of its refs, from which Kraft moves the item's branch in that repository, and its objects. See [Workspace members](/reference/configuration/sandbox/git-in-a-sandbox#workspace-members). |
| `HOME`, results, git identity | See [Home, results and git identity](#home-results-and-git-identity). |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, and every name in `env_passthrough` and `env`, forwarded by name, so their values never appear on the `docker` command line or in `ps`. See [Forwarded credentials](#forwarded-credentials). |
| Proxy and CA | The server's proxy variables and an extra CA. [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml#proxy) says which, and how. |
| Tool policy | Enforced inside the container or refused. See [Tool policy](#tool-policy). |

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

- With `unrestricted_network: true` and no [network policy](/reference/configuration/sandbox/network-policy), the container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable.
- A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch.
- Without a network policy a worker has no channel to Kraft, and its network is not restricted, so Kraft refuses the launch unless `unrestricted_network: true` is set: see [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks).
- Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

## In this section

- [Resource limits](/reference/configuration/sandbox/resource-limits): the CPU, memory and process limits of a container, and what happens when a memory limit kills a process.
- [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks): how a sandboxed worker reaches Kraft, and what is refused without a network policy.
- [Network policy](/reference/configuration/sandbox/network-policy): which hosts a sandboxed task may reach.
- [Credentials](/reference/configuration/sandbox/credentials): secrets the container never holds.
- [Kits](/reference/configuration/sandbox/kits): a sandbox that an image's own descriptor declares.
- [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml): the host-wide file for the container CLI, SELinux, an extra CA and the proxy.
- [Git in a sandbox](/reference/configuration/sandbox/git-in-a-sandbox): the copy of the repository's refs, a workspace's members, and what Kraft's own git does in the worktree.
