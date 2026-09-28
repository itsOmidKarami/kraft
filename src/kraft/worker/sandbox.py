"""Optional Docker isolation for a worker subprocess (Kraft-rki).

`--disallowed-tools` is the agent CLI policing itself at Kraft's request, not
a sandbox: it does not stop a worker that spawns a shell directly, and a
command-scoped allowlist does not help either -- an allowlisted `pytest`
still executes a `conftest.py` the implementation node itself wrote, in the
same host process, as the invoking user. What a `sandbox:` value looks like
is `policy.SandboxPolicy`'s, and which one an item runs in is
`dispatch.item_sandbox`'s; this module is how a command actually runs inside
one.

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

Off by default: `docker_argv` is only ever called for an item something
sandboxes, with a value its model already validated.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterable, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING

from kraft.paths import kraft_home

if TYPE_CHECKING:
    from kraft.worker.refstore import RefStore

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


#: The label every Kraft container carries, valued with the resolved
#: `KRAFT_HOME`: what `sweep_orphans` lists by, so one Kraft never reaps
#: another's containers (a dev instance beside an installed one).
HOME_LABEL = "kraft.home"


def home_label() -> str:
    return f"{HOME_LABEL}={kraft_home().resolve()}"


def docker_argv(
    cmd: list[str],
    cwd: str | Path,
    sandbox: dict,
    results_dir: str | Path | None,
    env: dict | None = None,
    name: str | None = None,
    result_path: str | Path | None = None,
    cidfile: str | Path | None = None,
    *,
    refstore: RefStore | None = None,
    home: str | Path | None = None,
    passthrough: Iterable[str] = (),
    ro_paths: Iterable[str | Path] = (),
    rw_paths: Iterable[str | Path] = (),
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
    session's own `result_path` is mounted back read-write. `None` for a
    launch that is no session and has no results to read (a repository's
    `setup_command`, `builtins.run_setup_command`): nothing is mounted.

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

    `cidfile`: docker writes the container's id there once it has created
    the container, and leaves no file when it never got that far -- the one
    thing that tells docker's own launch failure from the sandboxed
    command's (`subprocess._docker_launch_failed`, Kraft-6ltwh).

    `refstore`: the worktree's private ref store (`worker.refstore`), mounted
    over the repository's common gitdir so the worker's ref writes never reach
    the operator's refs. Without one, the common gitdir is read-only whole.

    `home`: a directory mounted read-write and set as `HOME`, so an agent CLI
    has somewhere to keep its state across sessions. `passthrough`: names
    forwarded bare, like `FORWARDED_ENV` -- a repo's `env_passthrough`, whose
    values must never be written onto this argv. `ro_paths`/`rw_paths`: extra
    host paths mounted at the same path (a rules file, a CLI config dir).

    `cwd` is resolved first: git records a worktree's real path, and a
    worktree mounted only at a symlinked path looks gone to `git worktree
    prune`, which then deletes its admin directory.
    """
    cwd = str(Path(cwd).resolve())
    argv = [
        "docker",
        "run",
        "--rm",
        # PID 1 ignores a signal it installed no handler for, so without an
        # init neither a pause's SIGINT nor a cap's SIGTERM reaches the agent.
        "--init",
        "--label",
        home_label(),
        "-u",
        f"{os.getuid()}:{os.getgid()}",
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        "-v",
        f"{cwd}:{cwd}",
        "-w",
        cwd,
    ]
    if results_dir is not None:
        argv += ["-v", f"{results_dir}:{results_dir}:ro"]
    if result_path is not None:
        argv += ["-v", f"{result_path}:{result_path}"]
    argv += _gitdir_mounts(Path(cwd), refstore)
    for path in ro_paths:
        argv += ["-v", f"{path}:{path}:ro"]
    for path in rw_paths:
        argv += ["-v", f"{path}:{path}"]
    if home is not None:
        argv += ["-v", f"{home}:{home}", "-e", f"HOME={home}"]
    if name is not None:
        argv += ["--name", name]
    if cidfile is not None:
        argv.append(f"--cidfile={cidfile}")
    for env_name in dict.fromkeys((*FORWARDED_ENV, *passthrough)):
        argv += ["-e", env_name]
    pins: dict[str, str] = {}
    _pin(pins, _HARDENED_GIT_CONFIG)
    for env_name, value in {**pins, **(env or {})}.items():
        argv += ["-e", f"{env_name}={value}"]
    argv.append(sandbox["image"])
    argv += cmd
    return argv


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


def linked_gitdirs(cwd: Path) -> tuple[Path, Path] | None:
    """`(common gitdir, worktree gitdir)` for a linked worktree, resolved, or
    None when `cwd/.git` is a directory or not a `gitdir:` file."""
    git_path = cwd / ".git"
    if git_path.is_dir():
        return None
    try:
        text = git_path.read_text()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.removeprefix("gitdir:").strip()).resolve()
    if gitdir.parent.name != "worktrees":
        return None
    return gitdir.parent.parent, gitdir


#: What a ref store mounts from the real common gitdir, read-only: files
#: and directories git reads but a worker has no business writing.
SHADOW_FILES = ("HEAD", "config", "shallow")
#: `objects` and `lfs` are data a commit (or an LFS clean filter) writes, so
#: they stay read-write; `info` (exclude, attributes) is read-only.
SHADOW_DIRS = ("objects", "lfs", "info")
_RW_SHADOW_DIRS = frozenset({"objects", "lfs"})


def _alternates(objects: Path) -> list[Path]:
    """Object directories `objects/info/alternates` borrows from (`clone
    --shared`, `--reference`): outside every other mount, so without their own
    the container cannot read those objects at all."""
    try:
        lines = (objects / "info" / "alternates").read_text().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            path = Path(line) if Path(line).is_absolute() else objects / line
            if path.resolve().is_dir():
                found.append(path.resolve())
    return found


def _gitdir_mounts(cwd: Path, refstore: RefStore | None) -> list[str]:
    """`-v` arguments for the gitdirs a linked worktree's `.git` file points at.

    Empty when `cwd` is not a linked worktree: an ordinary `.git` directory is
    inside `cwd`, already mounted with it.

    The worktree's own gitdir `W` is read-write (a commit writes its index,
    HEAD and lockfiles there), with the files git redirects config and the
    common dir through shadowed read-only, created first so a worker cannot
    dodge a shadow by deleting its file. The common gitdir is the private ref
    store `refstore` when given, with the real `objects/` and `lfs/` inside
    it read-write and `HEAD`, `config`, `info/` and `shallow` read-only; the
    worker's refs, reflogs, `packed-refs` and `FETCH_HEAD` all land in the
    store. Without a store it is read-only whole, and a commit cannot move a
    ref at all.
    """
    dirs = linked_gitdirs(cwd)
    if dirs is None:
        return []
    common, worktree_gitdir = dirs
    # `.git` itself lives inside the read-write `cwd` mount. Left alone, a
    # sandboxed worker repoints it at a gitdir it builds inside the
    # worktree -- HEAD, objects/, refs/, a config with hooks -- and the next
    # host-side git command run there executes the worker's hook as the
    # invoking host user. It never legitimately changes for the life of the
    # worktree, so shadowing it read-only costs nothing.
    git_file = cwd / ".git"
    mounts = ["-v", f"{git_file}:{git_file}:ro"]
    if refstore is None:
        mounts += ["-v", f"{common}:{common}:ro"]
    else:
        mounts += ["-v", f"{refstore.shadow}:{common}"]
        for name in (*SHADOW_FILES, *SHADOW_DIRS):
            path = common / name
            if path.exists():
                mode = "" if name in _RW_SHADOW_DIRS else ":ro"
                mounts += ["-v", f"{path}:{path}{mode}"]
        for alternate in _alternates(common / "objects"):
            mounts += ["-v", f"{alternate}:{alternate}:ro"]
    mounts += ["-v", f"{worktree_gitdir}:{worktree_gitdir}"]
    # `commondir` names where `hooks/` and `config` resolve to, and
    # `config.worktree` is read as part of the config stack whenever
    # `extensions.worktreeConfig` is on: either one, rewritten, hands the next
    # host-side git in this worktree a config of the worker's choosing.
    # `gitdir` is the backlink `git worktree prune` checks: rewritten to a
    # path that does not exist, the next host prune deletes this worktree's
    # admin directory.
    for name in ("commondir", "config.worktree", "gitdir"):
        path = worktree_gitdir / name
        if name != "gitdir":
            try:
                path.touch(exist_ok=True)
            except OSError:
                pass
        if path.exists():
            mounts += ["-v", f"{path}:{path}:ro"]
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


def planted_repos(worktree: Path, base: str | None) -> list[str] | None:
    """The nested repositories in a sandboxed item's `worktree` its worker
    made, which no host git may be let near -- or None when git cannot say.

    A sandboxed item declares no submodules (Ruling 180 refuses the pairing),
    so none of these is Kraft's: an untracked nested repository, a gitlink
    whose directory holds a `.git` (git enters those), and a gitlink the
    branch added or moved since `base`. A gitlink `base` already had, left
    unpopulated, is the repository's own and inert -- git has nothing to
    enter -- so it does not stop a sandboxed item on a repo with submodules."""
    found = nested_repos(worktree)
    at_base = _gitlinks_at(worktree, base) if base else {}
    if found is None or at_base is None:
        return None
    return sorted(
        path
        for path, sha in found.items()
        if sha is None
        or os.path.lexists(worktree / path / ".git")
        or (base and at_base.get(path) != sha)
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

    Bounded: a wedged daemon answers `docker rm` never, and this is awaited on
    every session's way out and for every row at startup. A container that
    outlives a timed-out call is `sweep_orphans`'s at the next start.
    """
    await _docker("rm", "-f", container_name(session_id))


#: How long any one best-effort `docker` call Kraft makes on its own account
#: (teardown, the orphan sweep, the image check) may take.
DOCKER_CALL_TIMEOUT_S = 30.0


async def _docker(*args: str, timeout: float | None = None) -> tuple[int, str] | None:
    """Run `docker args...`; `(returncode, stdout)`, or None when docker is
    missing or did not answer in time (its client is then killed)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout or DOCKER_CALL_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    return proc.returncode, out.decode(errors="replace")


async def sweep_orphans(keep: Iterable[str] = ()) -> list[str]:
    """`docker rm -f` every container this Kraft home started that no live
    session owns, returning their names: a teardown that failed or timed out
    (the daemon was briefly down) leaves one writing a worktree forever, and
    only a scan finds it. Listed by `home_label`, never by name, so another
    Kraft's containers on the same daemon are left alone."""
    listed = await _docker(
        "ps", "-a", "--filter", f"label={home_label()}", "--format", "{{.Names}}"
    )
    if listed is None or listed[0] != 0:
        return []
    keep = set(keep)
    orphans = [n for n in listed[1].split() if n not in keep]
    for name in orphans:
        await _docker("rm", "-f", name)
    return orphans


#: `(image, executable)` pairs an image was seen to hold. Only a hit is
#: remembered: an operator who fixes the image need not restart Kraft.
_HAS_EXECUTABLE: set[tuple[str, str]] = set()


async def missing_executable(image: str, executable: str) -> bool:
    """True only when `image` demonstrably lacks `executable`. A launch then
    stops as `config_error` naming both, instead of exiting 127 inside a
    container that exists -- which reads as the agent failing and opens a fix
    loop no agent can win. Anything inconclusive (no daemon, no `sh` in the
    image, a pull that failed) is False: the launch goes ahead and fails, or
    not, as it always did."""
    if (image, executable) in _HAS_EXECUTABLE:
        return False
    probed = await _docker(
        "run",
        "--rm",
        "--label",
        home_label(),
        "--entrypoint=",
        image,
        "sh",
        "-c",
        'command -v "$0" >/dev/null && echo yes || echo no',
        executable,
        timeout=300,
    )
    if probed is None:
        return False
    code, out = probed
    if code != 0:
        return False
    if out.strip() == "yes":
        _HAS_EXECUTABLE.add((image, executable))
        return False
    return out.strip() == "no"
