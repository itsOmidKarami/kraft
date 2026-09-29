"""What every sandbox backend shares (Kraft-rki): host git hardening, and the
guards on a sandboxed worktree.

`--disallowed-tools` is the agent CLI policing itself at Kraft's request, not
a sandbox: it does not stop a worker that spawns a shell directly, and a
command-scoped allowlist does not help either -- an allowlisted `pytest`
still executes a `conftest.py` the implementation node itself wrote, in the
same host process, as the invoking user. How a command runs inside a sandbox
is `kraft.worker.backends`'.

Isolation is two halves, and the second is the load-bearing one. A backend
keeps the sandbox out of everything it has no business writing. But a
worker's own worktree gitdir must stay writable for it to commit at all, and
that is where every file git redirects hooks and config through lives -- so
`harden_host_git_env` pins `core.hooksPath` (and the other program-valued
config keys) for every git Kraft runs, which makes what a worker writes there
unable to execute regardless of which redirect file it used.

Read docs/superpowers/specs/2026-09-13-worker-sandbox-docker-design.md for
the design this implements.
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Collection, Mapping, MutableMapping
from pathlib import Path
from typing import NamedTuple

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


class SandboxNotReady(RuntimeError):
    """A backend's `prepare` could not ready the host side of a launch: the
    launch stops as `config_error` with this message, never goes ahead
    without what it needed."""


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


_IDENTITY = {
    "GIT_AUTHOR_NAME": "user.name",
    "GIT_AUTHOR_EMAIL": "user.email",
    "GIT_COMMITTER_NAME": "user.name",
    "GIT_COMMITTER_EMAIL": "user.email",
}


def git_identity(cwd: Path) -> dict[str, str]:
    """The identity a worker commits under, as git's env form: the daemon's
    own `GIT_AUTHOR_*`/`GIT_COMMITTER_*` where set, else the host's
    `user.name`/`user.email` for this repository. The container has neither
    the host's `~/.gitconfig` nor a writable repo config to set one in, so
    without this every commit fails "unable to auto-detect email address".

    Read through the common gitdir, never the worktree: a co-task's container
    may be writing that worktree right now."""
    dirs = linked_gitdirs(cwd)
    gitdir = dirs[0] if dirs is not None else cwd / ".git"
    env = dict(os.environ)
    harden_host_git_env(env)
    found: dict[str, str] = {}
    looked_up: dict[str, str | None] = {}
    for name, key in _IDENTITY.items():
        if os.environ.get(name):
            found[name] = os.environ[name]
            continue
        if key not in looked_up:
            done = subprocess.run(
                ["git", f"--git-dir={gitdir}", "config", "--get", key],
                capture_output=True,
                text=True,
                env=env,
            )
            looked_up[key] = done.stdout.strip() if done.returncode == 0 else None
        if looked_up[key]:
            found[name] = looked_up[key]
    return found


def _gitdir_of(cwd: Path) -> Path | None:
    """The gitdir `cwd/.git` names, resolved, when it is a `gitdir:` file."""
    git_path = cwd / ".git"
    if git_path.is_dir():
        return None
    try:
        text = git_path.read_text()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    return Path(text.removeprefix("gitdir:").strip()).resolve()


def linked_gitdirs(cwd: Path) -> tuple[Path, Path] | None:
    """`(common gitdir, worktree gitdir)` for a linked worktree, resolved, or
    None when `cwd/.git` is a directory or does not name `<repo>/.git/worktrees/<id>`."""
    gitdir = _gitdir_of(cwd)
    if gitdir is None or gitdir.parent.name != "worktrees":
        return None
    return gitdir.parent.parent, gitdir


def inside(worktree: Path, rel: str) -> bool:
    """Whether `worktree/rel` is genuinely inside `worktree`: it resolves
    under it, and no component from `worktree` down to `rel` is a symlink a
    worker could have swapped in to point Kraft somewhere else."""
    path = worktree
    for part in Path(rel).parts:
        path = path / part
        if os.path.islink(path):
            return False
    return (worktree / rel).resolve().is_relative_to(worktree.resolve())


#: The most of one worker-writable file Kraft reads: `packed-refs` of a very
#: large repository fits many times over, and a worker cannot make Kraft read more.
_MAX_READ = 64 * 1024 * 1024


def read_regular(path: Path, within: Path) -> str | None:
    """`path`'s text, only if it is a regular file genuinely inside `within`:
    the worker writes there, so a symlink could point Kraft at any host file,
    and a FIFO would block the read (and any lock around it) forever."""
    try:
        if not path.resolve().is_relative_to(within.resolve()):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, _MAX_READ).decode(errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def repository_top(path: Path) -> bool:
    """Whether `path` is the top of a git working tree. git in any directory
    inside one answers for the enclosing repository instead, so a connected
    path that is only such a directory is no repository of its own."""
    if not path.is_dir():
        return False
    done = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True
    )
    return done.returncode == 0 and Path(done.stdout.strip()).resolve() == path.resolve()


def member_gitdirs(repo_path: Path, worktree: Path, rel: str) -> tuple[Path, Path] | None:
    """The trusted `(common gitdir, admin dir)` of member `rel` of `worktree`,
    whose connected repository is `repo_path`, or None when it has none.

    Derived from the operator's repository, never from `rel/.git`, which the
    worker writes (Kraft-ju36l, spec I3): the common gitdir is what git in
    `repo_path` says, and the admin dir is the `worktrees/*` entry of it whose
    `gitdir` file names this member's `.git`."""
    if not repository_top(repo_path):
        return None
    done = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        return None
    common = Path(done.stdout.strip()).resolve()
    want = os.path.realpath(worktree / rel / ".git")
    for admin in sorted((common / "worktrees").glob("*")):
        named = read_regular(admin / "gitdir", common)
        if named is not None and os.path.realpath(named.strip()) == want:
            return common, admin
    return None


class Checkout(NamedTuple):
    """A sandboxed item's checkout as its containers mount it (Kraft-ju36l):
    the item's root worktree, and each declared workspace member's trusted
    `(common gitdir, admin dir)` by mount path (`member_gitdirs`), None for a
    member Kraft did not check out."""

    root: Path
    members: Mapping[str, tuple[Path, Path] | None]


def _names(path: Path, within: Path, target: Path, *, prefix: str = "") -> bool:
    """Whether the regular file `path` holds `prefix` plus a path that
    resolves to `target`, relative ones read against `path`'s directory."""
    text = read_regular(path, within)
    if text is None or not text.startswith(prefix):
        return False
    named = Path(text.removeprefix(prefix).strip())
    return (path.parent / named).resolve() == target.resolve()


def foreign_members(worktree: Path, expected: dict[str, tuple[Path, Path] | None]) -> list[str]:
    """The declared members of `worktree` whose checkout is not the one Kraft
    made (Kraft-ju36l, spec 3.3): host git in one would read config Kraft did
    not check. `expected[rel]` is `member_gitdirs`'s answer; None means no
    connected repository, which covers an old-layout member and an item
    filed before workspaces.

    A member is Kraft's only when its path is genuinely inside the worktree,
    its `.git` is a regular file naming exactly the expected admin dir, and
    that admin dir's `commondir` leads to the expected common gitdir."""
    foreign = []
    for rel, dirs in expected.items():
        gitfile = worktree / rel / ".git"
        ours = (
            dirs is not None
            and inside(worktree, rel)
            and not os.path.islink(gitfile)
            and _names(gitfile, worktree, dirs[1], prefix="gitdir:")
            and _names(dirs[1] / "commondir", dirs[1], dirs[0])
        )
        if not ours:
            foreign.append(rel)
    return sorted(foreign)


#: What a ref store mounts from the real common gitdir, read-only: files
#: and directories git reads but a worker has no business writing.
SHADOW_FILES = ("HEAD", "config", "shallow")
#: `objects` and `lfs` are data a commit (or an LFS clean filter) writes, so
#: they stay read-write; `info` (exclude, attributes) is read-only.
SHADOW_DIRS = ("objects", "lfs", "info")
_RW_SHADOW_DIRS = frozenset({"objects", "lfs"})


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
_HOOK_PINS = (
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", ""),
)

#: Host git never walks into a repository nested in a worktree (Kraft-nx4id).
#:
#: A worker can build a git repository of its own anywhere in the worktree it
#: writes and commit a gitlink to it -- no `.gitmodules`, no declared member.
#: That repository's config is the worker's: `filter.<x>.clean`, a textconv,
#: an `ext::` remote, anything with a name git will not let `-c` enumerate.
#: The main worktree's config is safe to read (the common gitdir is mounted
#: read-only, `config.worktree` and `commondir` are shadowed, and git reads no
#: `config` from `<gitdir>/worktrees/<id>` itself); a nested repository's is
#: not, so what has to hold is that host git never *enters* one. Each of these
#: is a way it would, from config the operator (not the worker) may have set:
#: `submodule.recurse` makes checkout, reset and rebase recurse, the
#: `recurseSubmodules` pair makes fetch and push run inside a submodule with
#: its own remotes, `diff.submodule=diff`/`log` and `status.submoduleSummary`
#: run diff and log inside it, and `diff.ignoreSubmodules` is the default for
#: every `status`/`diff` below -- `dirty` compares only the commit a gitlink
#: records, which git reads without spawning anything in the nested repo.
#: That last one is only a default: a `.gitmodules` the worker writes can
#: override it per path, which is why the calls that compare a worktree also
#: pass `SUBMODULES_UNENTERED` on the command line.
_NO_RECURSION_PINS = (
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
    ("push.recurseSubmodules", "no"),
    ("diff.submodule", "short"),
    ("status.submoduleSummary", "false"),
    ("diff.ignoreSubmodules", "dirty"),
)

_HARDENED_GIT_CONFIG = _HOOK_PINS + _NO_RECURSION_PINS

#: For every host `status` and worktree `diff` Kraft runs in a worktree: a
#: gitlink counts as changed when the commit it points at moves (Kraft-qlsf
#: needs that much), but git never runs `status` inside it to look for
#: uncommitted edits -- that child process is what loads a planted repo's
#: config and runs its filters (review-g1 finding 1). On the command line,
#: because a worker's `.gitmodules` can override `diff.ignoreSubmodules`.
SUBMODULES_UNENTERED = "--ignore-submodules=dirty"


def _pin(env: MutableMapping[str, str], pins: tuple[tuple[str, str], ...]) -> None:
    env["GIT_CONFIG_COUNT"] = str(len(pins))
    for i, (key, value) in enumerate(pins):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value


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
    _pin(os.environ if env is None else env, _HARDENED_GIT_CONFIG)


def unhardened_git_env() -> dict[str, str]:
    """A copy of the process env with `harden_host_git_env`'s hook pin lifted.

    For `git push` alone: by the time Kraft pushes, the commit is already
    made and the worktree gitdir's hooks are either read-only-mounted
    (sandboxed) or none of a worker's business to have tampered with in the
    first place (unsandboxed) -- so the pinned `core.hooksPath=/dev/null`
    is no longer buying anything there, only breaking real hooks a human
    installed, like git-lfs's pre-push (Kraft-rki).

    Only that pin: `_NO_RECURSION_PINS` stay, so an operator's
    `push.recurseSubmodules=on-demand` still cannot make this push run
    `git push` inside a repository the worker nested in its worktree, with
    that repository's remotes and `core.sshCommand` (Kraft-nx4id).
    """
    env = dict(os.environ)
    _pin(env, tuple(p for p in _HARDENED_GIT_CONFIG if p[0] != "core.hooksPath"))
    return env


def _ls_files(worktree: Path, *args: str) -> list[str] | None:
    done = subprocess.run(
        ["git", "ls-files", "-z", *args], cwd=worktree, capture_output=True, text=True
    )
    return done.stdout.split("\0")[:-1] if done.returncode == 0 else None


def nested_repos(worktree: Path) -> dict[str, str | None] | None:
    """Every path in `worktree` host git could treat as a repository of its
    own, mapped to the commit its gitlink records -- None for an untracked
    one, which `git add -A` would stage as a gitlink. None when git cannot
    read the worktree at all.

    Read without entering any of them (Kraft-nx4id): `ls-files -s` is the
    index alone, and `ls-files --others` lists an untracked directory holding
    a `.git` as `path/` without opening it -- neither parses the nested
    repository's config, let alone runs anything from it."""
    staged = _ls_files(worktree, "-s")
    untracked = _ls_files(worktree, "--others", "--exclude-standard")
    if staged is None or untracked is None:
        return None
    found: dict[str, str | None] = {}
    for entry in staged:
        meta, _, path = entry.partition("\t")
        mode, sha, _ = meta.split(" ", 2)
        if mode == "160000":
            found[path] = sha
    for path in untracked:
        if path.endswith("/"):
            found[path.rstrip("/")] = None
    return found


def _gitlinks_at(worktree: Path, rev: str) -> dict[str, str] | None:
    done = subprocess.run(
        ["git", "ls-tree", "-r", "-z", rev], cwd=worktree, capture_output=True, text=True
    )
    if done.returncode != 0:
        return None
    links = {}
    for entry in done.stdout.split("\0")[:-1]:
        meta, _, path = entry.partition("\t")
        mode, _, sha = meta.split(" ", 2)
        if mode == "160000":
            links[path] = sha
    return links


def planted_repos(
    worktree: Path, base: str | None, mounts: Collection[str] = ()
) -> list[str] | None:
    """The nested repositories in a sandboxed item's `worktree` its worker
    made, which no host git may be let near -- or None when git cannot say.

    An untracked nested repository, a gitlink whose directory holds a `.git`
    (git enters those), and a gitlink the branch added or moved since `base`.
    A gitlink `base` already had, left unpopulated, is the repository's own
    and inert -- git has nothing to enter -- so it does not stop a sandboxed
    item on a repo with submodules. A declared member in `mounts` is Kraft's,
    populated and moved alike: its caller has checked it with
    `foreign_members` first."""
    found = nested_repos(worktree)
    at_base = _gitlinks_at(worktree, base) if base else {}
    if found is None or at_base is None:
        return None
    return sorted(
        path
        for path, sha in found.items()
        if path not in mounts
        and (
            sha is None
            or os.path.lexists(worktree / path / ".git")
            or (base and at_base.get(path) != sha)
        )
    )


def planted_refusal(who: str, paths: list[str]) -> str:
    """Why a sandboxed item with a nested repository stops (Kraft-nx4id)."""
    return (
        f"{who} runs sandboxed, and its worktree holds git repositories Kraft did "
        f"not create: {', '.join(paths)}. Host git would read their config -- "
        "which a sandboxed worker writes -- so Kraft runs no git there until a "
        "person looks. Remove them (or `git rm --cached` the gitlinks) and retry, "
        "or abandon the item"
    )


def foreign_member_refusal(who: str, members: list[str], planted: list[str]) -> str:
    """Why a sandboxed item whose member checkout Kraft did not make stops
    (`foreign_members`, Kraft-ju36l), with any planted repository too."""
    also = (
        f" It also holds git repositories Kraft did not create: {', '.join(planted)}."
        if planted
        else ""
    )
    return (
        f"{who} runs sandboxed, and its workspace members {', '.join(members)} are not "
        "the checkouts Kraft made from their connected repositories, so host git would "
        "read config a sandboxed worker could write. Kraft runs no git there until a "
        f"person looks.{also} An item started before Kraft checked members out this "
        "way stops here too. Abandon the item and file it again"
    )
