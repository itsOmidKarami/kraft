"""Optional Docker isolation for a worker subprocess (Kraft-rki).

`--disallowed-tools` is the agent CLI policing itself at Kraft's request, not
a sandbox: it does not stop a worker that spawns a shell directly, and a
command-scoped allowlist does not help either -- an allowlisted `pytest`
still executes a `conftest.py` the implementation node itself wrote, in the
same host process, as the invoking user. This module is the one place that
knows what a `sandbox:` value looks like, which of a binding's and a repo's
wins, and how to wrap a command to actually run inside one.

Isolation here is two halves, and the second is the load-bearing one. The
mounts (`docker_argv`) keep the container out of everything it has no
business writing. But a worker's own worktree gitdir must stay writable for
it to commit at all, and that is where every file git redirects hooks and
config through lives -- so `harden_host_git_env` pins `core.hooksPath` (and
the other program-valued config keys) for every git Kraft runs, which makes
what a worker writes there unable to execute regardless of which redirect
file it used.

Read docs/superpowers/specs/2026-09-13-worker-sandbox-docker-design.md for
the design this implements.

Off by default: nothing calls `resolve` unless a binding or a repo entry sets
`sandbox:` at all, and `docker_argv` is only ever called with a resolved,
already-validated value.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import MutableMapping
from pathlib import Path

#: Only kind implemented. A binding naming any other kind is rejected at
#: config load, not at dispatch -- the same "unknown X" treatment an unknown
#: agent profile or forge backend already gets (`templates.py`).
_KNOWN_KINDS = {"docker"}

#: Kraft's own vars, plus whichever auth var this install's `claude` CLI
#: actually uses, forwarded bare (`-e NAME`, no value) so docker copies each
#: from its own process env -- never written as a literal value into
#: repos.yaml. A var absent from that env is simply not set
#: in the container; `docker run -e NAME` for an unset NAME is not an error.
#: `CLAUDE_CODE_OAUTH_TOKEN` is what a subscription install's headless
#: `claude -p` authenticates with (`claude setup-token`) -- the interactive
#: login session itself lives in the OS keychain, which a Linux container
#: cannot reach. `ANTHROPIC_API_KEY` covers an API-key install the same way;
#: the two are not mutually exclusive to forward, only one is ever actually
#: set on a given install.
FORWARDED_ENV = (
    "KRAFT_RESULT_PATH",
    "KRAFT_WORK_ITEM_ID",
    "KRAFT_SESSION_ID",
    "KRAFT_REVIEW_PACKAGE",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)


class SandboxError(Exception):
    pass


def validate(sandbox: object, *, where: str) -> None:
    """A binding's or a repo's own `sandbox:` value, or raise.

    Only called with a value that is neither `None` nor `False` -- both of
    those are meaningful ("no sandbox" / "explicitly off") and have no shape
    to check, so callers filter them out before reaching here.
    """
    if not isinstance(sandbox, dict):
        raise SandboxError(f"{where}: 'sandbox' must be a mapping, not {sandbox!r}")
    kind = sandbox.get("kind")
    if kind not in _KNOWN_KINDS:
        raise SandboxError(
            f"{where}: sandbox kind {kind!r} is not supported; known: {sorted(_KNOWN_KINDS)}"
        )
    image = sandbox.get("image")
    if not isinstance(image, str) or not image:
        raise SandboxError(f"{where}: sandbox needs a non-empty string 'image'")


def resolve(binding: dict, repo_entry: dict | None) -> dict | None:
    """The sandbox a hook launches under.

    A repo's own `sandbox` wins over the binding's wholesale -- the same
    override-wins-over-registry rule `test_scopes`/`test_command` already use
    for `command` (`executor/dispatch.py`), not a second rule and not a
    merge. `None` on the repo entry (no `sandbox` key at all, the common
    case) falls through to the binding's; `False` turns off a binding that
    turned sandboxing on.
    """
    repo_sandbox = (repo_entry or {}).get("sandbox")
    sandbox = repo_sandbox if repo_sandbox is not None else binding.get("sandbox")
    return sandbox or None


def submodule_refusal(who: str) -> str:
    """Why a sandbox and submodule members are refused together (Kraft-dshto,
    Ruling 180): one sentence, the same at load, connect, intake, doctor and
    for an item already in flight. `who` names what set the sandbox.

    A container must write its worktree's gitdir to commit, and a submodule's
    gitdir lives inside it (`<worktree gitdir>/modules/<sm>`), so a worker can
    plant a hook, `core.sshCommand` or a filter driver there. `push` runs
    unhardened, and nothing pins the other two, so host git would run them
    as the operator."""
    return (
        f"{who} sets a sandbox, and a sandboxed item cannot mount submodules: a "
        "sandboxed worker can write each submodule's gitdir and plant hooks or "
        "core.sshCommand there, which host git runs as you when Kraft pushes the "
        "submodule (Kraft-dshto). Drop the sandbox, or work on one repository"
    )


def container_name(session_id: str) -> str:
    """The `--name` a sandboxed session's container runs under.

    A handle a caller can `docker kill`/`docker rm -f` by, independent of
    whatever became of the `docker run` client's own pid -- that client is
    the process Kraft's group-kill signals, but the container itself is
    parented by the docker daemon, not that group, and does not die with it.
    `session_id` is already a Kraft-minted id (safe for docker's
    `[a-zA-Z0-9_.-]` name charset); prefixed so a stray `kraft-*` container is
    obviously this tool's to clean up.
    """
    return f"kraft-{session_id}"


def docker_argv(
    cmd: list[str],
    cwd: str | Path,
    sandbox: dict,
    results_dir: str | Path,
    env: dict | None = None,
    name: str | None = None,
    result_path: str | Path | None = None,
) -> list[str]:
    """Wrap `cmd` to run inside `sandbox['image']` instead of directly on the host.

    Mounts `cwd` (the git worktree) at its original host path, so every
    relative path the agent's own prompt already names stays meaningful
    unchanged inside the container -- nothing downstream of this has to know
    the process ran in one. Runs as the invoking host uid:gid so a container
    default of root does not leave root-owned files behind in the worktree
    for a later host-side step to trip over -- `--security-opt=no-new-privileges`
    and `--cap-drop=ALL` are what keep that true: without them, a setuid-root
    binary in the operator's image (`su`, `mount`, `sudo` all ship in most base
    images) lets the worker regain root inside the container and write
    root-owned or setuid-root files into the bind-mounted worktree anyway.

    Everything else the container can reach is mounted read-only with exactly
    the paths this session must write carved back out read-write, rather than
    mounted read-write with the dangerous paths shadowed read-only: a
    shadow list is a list of the escapes someone thought of (`.git/hooks`,
    then `.git/modules/<sub>/hooks`, then `.git/worktrees/<id>/modules/...`),
    and the container only has to find the one nobody listed.

    `results_dir`: read-only, because a hook legitimately *reads* its
    neighbours there (its own `<session>.review.md`, the previous fix
    session's result file), but a session writing another session's
    `<other>.json` would forge that session's status and findings. Only this
    session's own `result_path` is mounted back read-write.

    `<repo>/.git`: read-only, with `objects/`, `refs/`, `logs/` and this
    worktree's own gitdir (`<repo>/.git/worktrees/<id>`) read-write -- what a
    commit from inside a linked worktree actually writes. That worktree
    gitdir has to be read-write wholesale: a commit rewrites HEAD, the index
    and per-worktree refs through lockfiles beside them, so the directory
    itself must be writable, and every file git redirects config and hooks
    through (`commondir`, `config.worktree`, `modules/<sm>/config`) lives in
    it. Those are not enumerated here -- `harden_host_git_env` is what makes
    them harmless, by pinning `core.hooksPath` and friends on every git Kraft
    itself runs, so whatever a worker plants in a file nobody listed still
    has nothing to execute it.

    A Kraft worktree's `.git` is a file pointing at that gitdir (`git
    worktree`'s own layout), outside `cwd` -- without the mount every git
    command inside the container fails to find it. That file lives inside
    the read-write `cwd` mount, though, so it is shadowed read-only on top:
    otherwise a sandboxed worker rewrites it to point at a gitdir of its own
    making, complete with a `hooks/` it populates itself, and the next
    host-side git command run in the worktree executes that hook as the
    invoking host user.

    `env` is `run_task`'s own `env=` argument -- e.g. `PYTHONDONTWRITEBYTECODE`
    for a fix-loop re-measure -- which is otherwise silently dropped: only
    `FORWARDED_ENV` crosses into the container by default. Passed through as
    literal `-e NAME=VALUE`, unlike `FORWARDED_ENV`'s bare `-e NAME` (which
    copies from docker's own process env, never written to disk).
    """
    cwd = str(cwd)
    results_dir = str(results_dir)
    argv = [
        "docker",
        "run",
        "--rm",
        "-u",
        f"{os.getuid()}:{os.getgid()}",
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        "-v",
        f"{cwd}:{cwd}",
        "-w",
        cwd,
        "-v",
        f"{results_dir}:{results_dir}:ro",
    ]
    if result_path is not None:
        argv += ["-v", f"{result_path}:{result_path}"]
    argv += _gitdir_mounts(Path(cwd))
    if name is not None:
        argv += ["--name", name]
    for env_name in FORWARDED_ENV:
        argv += ["-e", env_name]
    for env_name, value in (env or {}).items():
        argv += ["-e", f"{env_name}={value}"]
    argv.append(sandbox["image"])
    argv += cmd
    return argv


#: The only paths under a repo's common gitdir a commit made from inside a
#: linked worktree writes. None of them is ever executed by git (objects and
#: refs are data, `logs/` is the reflog), so read-write here does not hand the
#: container host code execution the way `hooks/` or `config` would.
_WRITABLE_COMMON_GITDIR = ("objects", "refs", "logs")


def _gitdir_mounts(cwd: Path) -> list[str]:
    """`-v` arguments for the repo gitdir a worktree's `.git` file points at.

    Empty when `cwd` has no `.git` at all (never expected for a real
    invocation, but this is not the place to raise over it) or already has an
    ordinary `.git` directory of its own -- that one is inside `cwd`, already
    mounted with it, and nothing extra is needed.

    The `is_dir()` guards below are read-write *carve-outs*, not read-only
    shadows: a path that is missing stays covered by the read-only mount of
    its parent, so a container that creates one gains nothing. (An earlier
    revision guarded read-only shadows this way, where a missing path meant
    no shadow at all and the container could just create it.)
    """
    git_path = cwd / ".git"
    if git_path.is_dir():
        return []
    try:
        text = git_path.read_text()
    except OSError:
        return []
    if not text.startswith("gitdir:"):
        return []
    # `.git` itself lives inside the read-write `cwd` mount. Left alone, a
    # sandboxed worker repoints it at a gitdir it builds inside the
    # worktree -- HEAD, objects/, refs/, a config with hooks -- and the next
    # host-side git command run there executes the worker's hook as the
    # invoking host user. It never legitimately changes for the life of the
    # worktree, so shadowing it read-only costs nothing.
    mounts = ["-v", f"{git_path}:{git_path}:ro"]
    gitdir = Path(text.removeprefix("gitdir:").strip())
    # `<repo>/.git/worktrees/<id>` -> common gitdir `<repo>/.git`. Only that
    # one subdirectory is this session's to write; every sibling worktree's
    # gitdir stays read-only under the common mount.
    common, worktree_gitdir = (
        (gitdir.parent.parent, gitdir) if gitdir.parent.name == "worktrees" else (gitdir, None)
    )
    # `logs/` does not exist in a freshly cloned repo, and git creates it on
    # the first ref update -- which it cannot do under the read-only mount,
    # failing the commit. Creating it here is what git itself would do a
    # moment later.
    try:
        (common / "logs").mkdir(exist_ok=True)
    except OSError:
        pass
    mounts += ["-v", f"{common}:{common}:ro"]
    for name in _WRITABLE_COMMON_GITDIR:
        path = common / name
        if path.is_dir():
            mounts += ["-v", f"{path}:{path}"]
    if worktree_gitdir is not None:
        # A commit from this worktree writes all over its own gitdir (index,
        # HEAD, COMMIT_EDITMSG, per-worktree refs, lockfiles), so this one is
        # read-write wholesale -- with the exceptions below.
        mounts += ["-v", f"{worktree_gitdir}:{worktree_gitdir}"]
        # `commondir` is a file inside that read-write gitdir naming where
        # `hooks/` and `config` actually resolve to (the common gitdir
        # mounted above). Left writable, a worker repoints it at a directory
        # of its own inside the worktree, carrying its own `config` and
        # `hooks/pre-commit`, and the next host-side git command run here
        # runs that hook as the invoking host user -- the same escape the
        # read-only `hooks/`/`config` mounts exist to close, reached through
        # the one file that redirects to them. It always exists once a
        # linked worktree is created, but is created here too rather than
        # guarded with `exists()`, so the shadow can never be dodged by a
        # worker that deletes it first.
        commondir = worktree_gitdir / "commondir"
        try:
            commondir.touch(exist_ok=True)
        except OSError:
            pass
        mounts += ["-v", f"{commondir}:{commondir}:ro"]
        # `config.worktree` is the other file inside this read-write gitdir git
        # reads as part of its config stack -- whenever
        # `extensions.worktreeConfig = true` (`git sparse-checkout set` turns
        # this on), git reads it in addition to the common gitdir's `config`.
        # Left writable, a worker plants `core.hooksPath` there just as easily
        # as through `commondir`, and the next host-side `git commit` this
        # worktree runs executes that hook as the invoking host user -- the
        # same escape, a second file wide. Created here rather than guarded
        # with `exists()` for the same reason as `commondir`: it must not be
        # dodged by a worker that deletes it first.
        worktree_config = worktree_gitdir / "config.worktree"
        try:
            worktree_config.touch(exist_ok=True)
        except OSError:
            pass
        mounts += ["-v", f"{worktree_config}:{worktree_config}:ro"]
    return mounts


#: Config a repository must not be able to set for a git *Kraft itself* runs,
#: because each of these values is a program git executes.
#:
#: A worker's worktree gitdir is read-write by construction -- a commit writes
#: its index, HEAD, lockfiles and per-worktree refs, so the directory holding
#: them cannot be read-only, and every file git redirects through lives in
#: that same directory (`commondir`, `config.worktree`, `modules/<sm>/config`,
#: and whichever one the next git version adds). Enumerating them one shadow
#: mount at a time is a list of the escapes someone thought of; this is the
#: other side of the same problem, and it does not need the list: whatever a
#: worker plants, the host-side git that would have run it is launched with
#: these pinned instead.
#:
#: `/dev/null` as a hooks *directory* resolves every hook to `/dev/null/<name>`,
#: which is not a file, so git finds no hook to run. `core.fsmonitor` names a
#: program git spawns on `status`; empty is "unset", git's own default.
#:
#: `diff.external` is deliberately *not* here even though it is the same kind
#: of vector: git reads an empty value as the empty command and dies
#: ("cannot run : No such file or directory") on every `git diff`, which is
#: half of what Kraft does with a worktree. Nothing safe to pin it to exists,
#: so that one stays covered by the mounts -- the config files git could read
#: it from are the common gitdir's `config` (read-only mount), `config.worktree`
#: and whatever `commondir` redirects to (both shadowed read-only).
_HARDENED_GIT_CONFIG = (
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", ""),
)


def harden_host_git_env(env: MutableMapping[str, str] | None = None) -> None:
    """Pin `_HARDENED_GIT_CONFIG` for every git this process ever spawns.

    `GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n` is git's own environment form of
    `git -c key=value`, and carries `-c`'s precedence: it beats the system,
    global, repo *and* worktree config files, so a `core.hooksPath` a worker
    plants in any of them loses. Setting it on the server's own environment
    once covers every git Kraft runs -- `builtins`, `adapters.forge.git`,
    `adapters.forge.run`, `config.git_read`, `index.ingest`, the worktree
    teardown in `api.routes.lifecycle` -- and every git those spawn in turn
    (a submodule's, a rebase's), without each call site having to remember.
    Doing it per call site is what left `git worktree prune` and
    `git submodule update` running a worker's hook while `git commit` had
    `--no-verify`.

    Kraft's own worker sessions inherit it too (they are children of this
    process), so a repo hook does not fire on an agent's commits either --
    the same thing `adapters.forge.git` already asked for explicitly with
    `--no-verify`.

    `adapters.forge.git.push` is the one exception, running with
    `unhardened_git_env()` instead: see that function for why.
    """
    env = os.environ if env is None else env
    env["GIT_CONFIG_COUNT"] = str(len(_HARDENED_GIT_CONFIG))
    for i, (key, value) in enumerate(_HARDENED_GIT_CONFIG):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value


def unhardened_git_env() -> dict[str, str]:
    """A copy of the process env with `harden_host_git_env`'s override lifted.

    For `git push` alone: by the time Kraft pushes, the commit is already
    made and the worktree gitdir's hooks are either read-only-mounted
    (sandboxed) or none of a worker's business to have tampered with in the
    first place (unsandboxed) -- so the pinned `core.hooksPath=/dev/null`
    is no longer buying anything there, only breaking real hooks a human
    installed, like git-lfs's pre-push. Popping `GIT_CONFIG_COUNT` alone is
    enough: git reads none of the paired `GIT_CONFIG_KEY_n`/`_VALUE_n`
    without it (Kraft-rki).
    """
    env = dict(os.environ)
    env.pop("GIT_CONFIG_COUNT", None)
    return env


async def teardown(session_id: str) -> None:
    """`docker rm -f` this session's container, best-effort.

    The one place a session's container is torn down, called from every path
    a session can *end* on -- `run_task`'s `finally` (normal exit, pause,
    skip, timeout) and `reattach`'s (a session adopted or resolved after a
    Kraft restart). The pid Kraft signals is `docker run`'s own client
    process, not the container: the container is parented by the docker
    daemon, so a killed -- or restarted-away-from -- client leaves it running
    with the worktree still mounted (Kraft-rki).

    Safe to call for a session that never had a sandbox at all: the name
    (`container_name`) simply matches nothing, and `docker rm -f` on an
    unknown name is a no-op this swallows. That is deliberate -- the callers
    are "a session ended" points, and making them each first work out whether
    this one was sandboxed is how a path gets missed. Errors (already gone,
    docker not installed) are not this function's to raise: a container that
    is already down is the success case.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name(session_id),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await proc.wait()
    except OSError:
        pass
