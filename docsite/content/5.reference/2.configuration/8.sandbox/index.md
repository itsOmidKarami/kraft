---
title: Sandboxed workers
navigation:
  title: Overview
description: What reaches a sandboxed worker's container, and how its refs, credentials, proxy and tool policy are handled.
---

A repository's [`sandbox`](/reference/configuration/repos#sandbox) setting runs its workers in a container. This page covers what reaches that container and how each part gets there.

A sandboxed task runs `docker run --init` (or `podman run`) with every capability dropped, within its [resource limits](/reference/configuration/sandbox/callbacks-and-limits#resource-limits), as a user that leaves what it writes yours, on rootless Docker and Podman too. Which CLI runs it, and what happens on an SELinux-enforcing host, is [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml)'s. Only what is listed here reaches the container.

| What | How it reaches the container |
|---|---|
| The worktree | Mounted read-write at its real path. |
| The repository's refs | A private copy per worktree, under `$KRAFT_HOME/run/sandbox-git/`. See [Repository refs](#repository-refs). |
| Objects and LFS content | Your repository's `objects/` and `lfs/`, read-write: they are data a commit writes. `objects/info` (where alternates are named) and alternates themselves are read-only. |
| Workspace members | Each member's own repository, the same way as the root's: a private copy of its refs, from which Kraft moves the item's branch in that repository, and its objects. See [Workspace members](#workspace-members). |
| `HOME` | `$KRAFT_HOME/run/sandbox-home/<work item>`, read-write and kept across sessions, so an agent CLI keeps its state and can resume a paused session. A CLI config directory Kraft owns (Cursor's) lives there too, one per item. |
| Results | From `$KRAFT_HOME/run/results`, only this work item's own files: its sessions' result files, review packages and long instructions, read-only, and the session's own result file, read-write. Nothing of another item's is mounted. |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, and every name in `env_passthrough` and `env`, forwarded by name, so their values never appear on the `docker` command line or in `ps`. See [Forwarded credentials](#forwarded-credentials). |
| Proxy and CA | The daemon's `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` (any case), forwarded by name. A proxy on the daemon's loopback is left out, and under a network policy none is forwarded. See [Proxy and CA](#proxy-and-ca). |
| Git identity | `GIT_AUTHOR_*` and `GIT_COMMITTER_*` from the daemon's environment, else your `user.name` and `user.email` for the repository. Repository hooks never run in the container, as they never run on a worker's commits outside one. |
| Tool policy | Enforced inside the container or refused. See [Tool policy](#tool-policy). |

Abandoning or archiving an item removes its ref store and sandbox home.

## Repository refs

The worker sees every branch and tag as of its session's start, and may create, move or delete refs, but only in that copy.

When a session ends, Kraft moves the item's branch in your repository to where the worker left it, and only if nothing else moved it since; any other ref the worker changed is dropped. What Kraft relies on to decide that lives outside the copy, where the worker cannot write.

A branch Kraft could not move is recorded as a `sandbox_branch_not_synced` event, and its commit is kept at `refs/kraft/unsynced/<branch>`. A setup command sees the copy but never moves a branch.

A HEAD the worker points at another branch, or a rebase, merge or other git operation it leaves in progress, is never acted on: Kraft stops the item for you instead (see [Troubleshooting](/troubleshooting#why-did-my-item-stop)).

## Workspace members

- The member's `.git` file, and the `commondir`, `gitdir` and `config.worktree` of its git directory, are read-only, as the root's are, and every directory from the worktree down to the member is a mount point, which the worker cannot rename or remove.
- Kraft takes every path it mounts from the connected repository, never from the member's `.git`.
- A member that is not the checkout Kraft made, or that is reached through a symlink, is not mounted, and the launch does not start.
- A task fanned out to one member still mounts the whole worktree, and starts in the member.
- A member branch Kraft could not move is its own `sandbox_branch_not_synced` event, naming the repository, and abandoning or archiving the item removes the members' copies too.

See [Workspaces](/reference/configuration/repos/workspaces#sandboxing).

## Forwarded credentials

The container's own environment still holds the values, so `docker inspect` shows them to anyone who can reach the container runtime. A name listed under [`credentials`](/reference/configuration/sandbox/credentials) is the exception: the container holds only its sentinel, and `docker inspect` shows that. Logins kept in your home directory or the OS keychain do not reach a sandbox.

## Proxy and CA

A proxy on the daemon's loopback (`127.0.0.0/8`, `::1`, `localhost` or any `*.localhost` name, or the unspecified address `0.0.0.0` or `::`) is left out, since the container's loopback is its own and nothing listens there; `kraft admin doctor` warns about it. Under a [network policy](/reference/configuration/sandbox/network-policy) none of these is forwarded: the container's proxy is Kraft's own.

When there is an extra CA, [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml#ca-certificates)'s `ca_bundle` or else a usable `SSL_CERT_FILE` of the daemon's, Kraft combines it with the image's own roots into one bundle, mounted read-only at `/etc/kraft/ca-bundle.pem`, and points `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`, `CODEX_CA_CERTIFICATE`, `PIP_CERT` and `npm_config_cafile` at it. No host path is forwarded. With no extra CA, nothing is mounted or set.

## Tool policy

- Amp's and OpenCode's rules travel with the launch, and Amp's rules file is mounted read-only.
- Codex's policy is held by Kraft's permission hook, which reaches Kraft only through a [network policy](/reference/configuration/sandbox/network-policy)'s channel (see [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks-and-limits#callbacks-from-a-sandbox)); without `network:` a Codex or Cursor task with `allowed_tools`, `deny_tools` or a grant other than `git-commit` is refused.
- Claude asks Kraft through an MCP tool on every launch, so a sandboxed Claude task without `network:` is refused whatever its policy.
- Codex runs with `sandbox_mode=danger-full-access` unless a task or harness profile sets a mode other than `workspace-write`, because its own sandbox cannot start inside Docker.

## The image

It must hold the agent CLI (and, for a fallback, every harness it may fall back to) on its `PATH`, plus `git`, `sh` and CA certificates, and `curl` under a network policy (the `kraft` shim uses it).

Before a task's first launch in an image, Kraft asks the image, through its own entrypoint and with the repository's `env`, whether the command is there; an image that answers no stops the item as a configuration error before anything runs.

`kraft admin doctor` checks:

- that the runtime answers
- that SELinux has an answer in `sandbox.yaml` where it enforces
- that an extra CA can be read
- that the image is pulled

Pull it before filing work, or the first launch pulls it inside the task's time cap.

## Not yet covered

- Without a [network policy](/reference/configuration/sandbox/network-policy), the container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable.
- A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch.
- Without a network policy a worker cannot reach Kraft at all: see [Callbacks from a sandbox](/reference/configuration/sandbox/callbacks-and-limits#callbacks-from-a-sandbox).
- Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

## In this section

- [Callbacks and resource limits](/reference/configuration/sandbox/callbacks-and-limits): how a sandboxed worker reaches Kraft, and the CPU, memory and process limits of a container.
- [Network policy](/reference/configuration/sandbox/network-policy): which hosts a sandboxed task may reach.
- [Credentials](/reference/configuration/sandbox/credentials): secrets the container never holds.
- [Kits](/reference/configuration/sandbox/kits): a sandbox that an image's own descriptor declares.
- [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml): the host-wide file for the container CLI, SELinux and an extra CA.
