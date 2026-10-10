"""Host git never runs what a sandboxed worker planted (Kraft-nx4id, Kraft-ju36l).

A sandboxed worker writes its worktree, and nothing stops it building a git
repository of its own in there -- a `filter.<x>.clean`, a `core.fsmonitor`, a
textconv in that repository's config -- and committing a gitlink to it with
`git update-index --cacheinfo 160000,...`. No `.gitmodules`, no declared
submodule. Host git that recurses into that gitlink loads the nested config
and runs its programs as the operator (review-g1 finding 1).

Every test here plants from the worker's side only (inside the worktree, with
an environment carrying none of Kraft's pins) and drives Kraft's own host-side
calls under the hardened environment the daemon runs with. The marker file is
the payload; nothing destructive is ever planted.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft import builtins, review
from kraft.adapters import forge
from kraft.worker import sandbox


def _worker_env() -> dict[str, str]:
    """The worker's side: none of Kraft's `GIT_CONFIG_*` pins."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}


def _w(cwd: Path, *args: str) -> str:
    """git as the sandboxed worker runs it, inside its own worktree."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=_worker_env()
    ).stdout.strip()


@pytest.fixture
def hardened():
    """The daemon's environment: `api.startup` pins it once on the process,
    and every git Kraft spawns inherits it. `tests/conftest.py` restores
    `GIT_CONFIG_*` afterwards."""
    sandbox.harden_host_git_env()


def _worktree(tmp_path: Path) -> Path:
    """A Kraft-shaped linked worktree: `.git` a file, its gitdir under the
    repo, an `origin` to push to, and a second branch for a checkout."""
    repo = make_repo(tmp_path)
    origin = tmp_path / "origin.git"
    _w(tmp_path, "init", "--bare", "-q", "-b", "main", str(origin))
    _w(repo, "remote", "add", "origin", str(origin))
    _w(repo, "push", "-q", "origin", "main")
    _w(repo, "branch", "kraft/other")
    wt = tmp_path / "worktrees" / "w1"
    _w(repo, "worktree", "add", "-q", "-b", "kraft/w1", str(wt))
    return wt


def _run(marker: Path) -> str:
    """A shell command that leaves `marker` behind and passes stdin through,
    so it serves as a clean/smudge filter, a textconv or a hook alike."""
    return f"touch {shlex.quote(str(marker))}; cat"


def _plant_gitlink(
    wt: Path, config: dict[str, str], *, name: str = "evil", gitmodules: bool = False
) -> Path:
    """review-g1's repro, from inside the worktree: a nested repository with
    `.gitattributes` selecting driver `evil`, committed as a gitlink, then
    its config armed and a tracked file dirtied so a recursing `status` has to
    run the clean filter to compare it.

    `gitmodules` also commits a `.gitmodules` naming it, with `ignore =
    none` -- the per-path override that beats a `diff.ignoreSubmodules`
    pinned in the environment, and what fetch recursion needs to find it."""
    nested = wt / name
    nested.mkdir()
    _w(nested, "init", "-q")
    _w(nested, "config", "user.email", "w@w")
    _w(nested, "config", "user.name", "w")
    (nested / ".gitattributes").write_text("* filter=evil diff=evil\n")
    (nested / "payload.txt").write_text("payload\n")
    _w(nested, "add", "-A")
    _w(nested, "commit", "-q", "-m", "x")
    sha = _w(nested, "rev-parse", "HEAD")
    _w(wt, "update-index", "--add", "--cacheinfo", f"160000,{sha},{name}")
    if gitmodules:
        (wt / ".gitmodules").write_text(
            f'[submodule "{name}"]\n\tpath = {name}\n\turl = ./{name}\n\tignore = none\n'
        )
        _w(wt, "add", ".gitmodules")
    _w(wt, "commit", "-q", "-m", "worker commits its gitlink, as instructed")
    for key, value in config.items():
        _w(nested, "config", key, value)
    (nested / "payload.txt").write_text("changed\n")
    return nested


def test_the_planted_filter_is_live(tmp_path):
    """The positive control for everything below: the exact command
    `assert_clean` used to run does execute the planted filter, even under
    the hooks-and-fsmonitor hardening that came before this fix."""
    wt = _worktree(tmp_path)
    marker = tmp_path / "PWNED"
    _plant_gitlink(wt, {"filter.evil.clean": _run(marker)})
    env = dict(os.environ)
    sandbox.harden_host_git_env(env)
    subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules=none"],
        cwd=wt,
        capture_output=True,
        env=env,
    )
    assert marker.exists()


def test_assert_clean_never_runs_a_filter_planted_behind_a_gitlink(tmp_path, hardened):
    """review-g1 finding 1, as Kraft runs it: `open_mr`'s clean check."""
    wt = _worktree(tmp_path)
    marker = tmp_path / "PWNED"
    _plant_gitlink(wt, {"filter.evil.clean": _run(marker)})
    try:
        asyncio.run(forge.assert_clean(wt, "main"))
    except forge.ForgeError:
        pass
    assert not marker.exists()


#: Config an operator may well have in `~/.gitconfig`, each of which makes
#: git walk into a submodule on its own. Kraft's pins must beat all of them.
_OPERATOR_CONFIG = """\
[submodule]
\trecurse = true
[fetch]
\trecurseSubmodules = yes
[push]
\trecurseSubmodules = on-demand
[diff]
\tsubmodule = diff
\tignoreSubmodules = none
[status]
\tsubmoduleSummary = true
"""


def _hook_dir(tmp_path: Path, marker: Path) -> str:
    hooks = tmp_path / "planted-hooks"
    hooks.mkdir()
    for name in ("post-checkout", "pre-commit", "pre-push"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
        (hooks / name).chmod(0o755)
    return str(hooks)


def _include(tmp_path: Path, marker: Path) -> str:
    extra = tmp_path / "planted-include"
    extra.write_text(f'[filter "evil"]\n\tclean = {_run(marker)}\n')
    return str(extra)


_STATUS = ["status", "--porcelain", "--ignore-submodules=none"]
_SUB_DIFF = ["-c", "diff.submodule=diff", "diff", "HEAD~1", "HEAD"]
_SUB_CHECKOUT = ["submodule", "update", "--init", "--force", "--checkout", "--", "evil"]
_RECURSE_FETCH = ["-c", "fetch.recurseSubmodules=yes", "fetch", "-q", "origin"]

#: Every program-source family a nested repository's config can carry:
#: (its config, given the marker and tmp_path; a git command that, run by
#: anything that *does* recurse, executes it -- the positive control).
FAMILIES = {
    "filter-clean": (lambda m, t: {"filter.evil.clean": _run(m)}, _STATUS),
    "filter-process": (lambda m, t: {"filter.evil.process": _run(m)}, _STATUS),
    "filter-smudge": (lambda m, t: {"filter.evil.smudge": _run(m)}, _SUB_CHECKOUT),
    "fsmonitor": (lambda m, t: {"core.fsmonitor": _run(m)}, _STATUS),
    "include-path": (lambda m, t: {"include.path": _include(t, m)}, _STATUS),
    "textconv": (lambda m, t: {"diff.evil.textconv": _run(m)}, _SUB_DIFF),
    "diff-command": (lambda m, t: {"diff.evil.command": _run(m)}, _SUB_DIFF),
    "hooks": (lambda m, t: {"core.hooksPath": _hook_dir(t, m)}, _SUB_CHECKOUT),
    "ext-remote": (
        lambda m, t: {
            "remote.origin.url": f"ext::sh -c touch% {m}",
            "protocol.ext.allow": "always",
        },
        _RECURSE_FETCH,
    ),
    "ssh-command": (
        lambda m, t: {
            "remote.origin.url": "ssh://kraft.invalid/x",
            "core.sshCommand": _run(m),
        },
        _RECURSE_FETCH,
    ),
}


async def _every_host_call(wt: Path) -> None:
    """Each host-side git Kraft runs in a worktree between two sessions, the
    ones a door does not stop first: the straggler sweep and branch restore
    after an agent, the clean check and push `open_mr` makes, and the diff
    endpoint (which a human can hit while a worker still runs)."""

    review.read_change(wt, "HEAD")
    review.read_change(wt, "HEAD~1", head="HEAD")
    # One file's read (`/compare?file=`): its rename lookup, then the diff by path.
    review.renamed_from(wt, "HEAD", None, "f")
    review.read_change(wt, "HEAD", paths=["f"])
    for call in (
        forge.commit_stragglers(wt, branch="kraft/w1", base="main", message="wip"),
        forge.assert_clean(wt, "main"),
        forge.git.push(wt, "kraft/w1"),
    ):
        try:
            await call
        except forge.ForgeError:
            pass
    builtins.restore_branch(wt, "kraft/other", "main")


@pytest.mark.parametrize("family", FAMILIES)
def test_no_host_call_runs_a_program_planted_in_a_nested_repository(
    tmp_path, hardened, family, monkeypatch
):
    """Kraft-nx4id, one family at a time, under an operator config that asks
    git to recurse every way it can. Then the control: the same plant, under
    a git that does recurse, runs -- so each pass here is the guard's."""
    config, trigger = FAMILIES[family]
    kraft, control = tmp_path / "kraft", tmp_path / "control"
    for side in (kraft, control):
        side.mkdir()
        _plant_gitlink(_worktree(side), config(side / "PWNED", side), gitmodules=True)
    operator = tmp_path / "operator.gitconfig"
    operator.write_text(_OPERATOR_CONFIG)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(operator))

    asyncio.run(_every_host_call(kraft / "worktrees" / "w1"))
    subprocess.run(
        ["git", *trigger], cwd=control / "worktrees" / "w1", capture_output=True, env=_worker_env()
    )

    assert not (kraft / "PWNED").exists()
    assert (control / "PWNED").exists(), f"control: {trigger} never ran the planted {family}"


# -- Kraft-ju36l: a workspace member's host git reads only its connected repository --
#
# A member is a linked worktree of its connected repository `M`, so every git
# Kraft runs in it resolves config, hooks and attributes from `M`'s common
# gitdir, which the container mounts read-only. What a sandboxed worker can
# write -- the member's admin dir in `M`, and the `modules/<rel>` in the
# root's worktree gitdir where the old layout kept a member's gitdir -- git in
# the member never reads. Each key below is planted at every one of those sites,
# every host path Kraft takes in a member runs, and nothing fires. The control
# plants the same key where git does read it, `M`'s own config, and shows it
# is live there. A site is planted only where the container's mounts let the
# worker write (`_container_read_only`): with `extensions.worktreeConfig` on in
# `M`, git also reads the admin dir's `config.worktree`, which only its
# read-only mount keeps the worker's pen off (J6).

import contextlib  # noqa: E402
import inspect  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from support import worktree as wtree  # noqa: E402
from support.harness import entry_of, v1_chain  # noqa: E402
from support.workspace import workspace_target  # noqa: E402

from kraft.adapters.forge.run import _point_at_merged_members  # noqa: E402
from kraft.api.routes import lifecycle  # noqa: E402
from kraft.executor import read_only  # noqa: E402
from kraft.worker import refstore  # noqa: E402
from kraft.worker.backends import docker  # noqa: E402

_REL = "repos/pkg"
#: Same length at every step, so git has to read a changed file's content --
#: running its clean filter -- rather than calling it changed by its size.
_BASE, _WORK, _DIRT = "aaaa\n", "bbbb\n", "cccc\n"
_MEMBER_NODES = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


def _repo_with_origin(side: Path, name: str) -> tuple[Path, Path]:
    """A repository whose `.gitattributes` selects driver `evil` for filter
    and diff, as a worker could commit it, pushed to a bare `origin`."""
    repo = make_repo(side, name)
    (repo / ".gitattributes").write_text("* filter=evil diff=evil\n")
    (repo / "payload.txt").write_text(_BASE)
    _w(repo, "add", "-A")
    _w(repo, "commit", "-q", "-m", "attributes")
    origin = side / f"{name}-origin.git"
    _w(side, "init", "--bare", "-q", "-b", "main", str(origin))
    _w(repo, "remote", "add", "origin", str(origin))
    _w(repo, "push", "-q", "origin", "main")
    return repo, origin


async def _workspace(
    database, run_dirs, side: Path, wid: str, setup: str = "", worktree_config: bool = False
):
    """Item `wid`: a root with member `pkg` at `_REL`, its connected repository
    `M` with its own origin (`extensions.worktreeConfig` on when
    `worktree_config`), the checkout made by `ensure_worktree` (after
    `setup`, the repository's setup command), and one commit in the member
    and one moving the root's gitlink, as a worker makes them. Both then
    carry an uncommitted edit."""
    side.mkdir()
    m, m_origin = _repo_with_origin(side, "pkg")
    if worktree_config:
        subprocess.run(["git", "config", "extensions.worktreeConfig", "true"], cwd=m, check=True)
    root, _ = _repo_with_origin(side, "ws")
    _w(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(m_origin), _REL)
    _w(root, "commit", "-q", "-m", "add the member")
    _w(root, "push", "-q", "origin", "main")
    chain = v1_chain(_MEMBER_NODES, repo=root, target=workspace_target({"pkg": _REL}))
    await wtree.make_item(database, root, wid=wid, materialized_chain=chain.to_json())
    wt = await builtins.ensure_worktree(
        database,
        run_dirs,
        repo=str(root),
        work_item_id=wid,
        repo_entry=entry_of({"setup_command": setup}),
        repositories={"pkg": entry_of({"id": "pkg", "path": str(m)})},
    )
    member = wt / _REL
    for repo, add in ((member, "payload.txt"), (wt, ".")):
        (repo / "payload.txt").write_text(_WORK)
        _w(repo, "add", add)
        _w(repo, "commit", "-q", "-m", "the worker's work")
        (repo / "payload.txt").write_text(_DIRT)
    gitdirs = sandbox.member_gitdirs(m, wt, _REL)
    return SimpleNamespace(
        wid=wid,
        root=root,
        wt=wt,
        member=member,
        m=m,
        m_origin=m_origin,
        branch=_w(wt, "branch", "--show-current"),
        admin=gitdirs[1] if gitdirs else None,
        root_gitdir=Path(_w(wt, "rev-parse", "--path-format=absolute", "--git-dir")),
    )


def _program(planted: Path, marker: Path) -> str:
    """An executable for keys git runs without a shell: it leaves `marker`."""
    path = planted / "program"
    path.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\ncat >/dev/null\n")
    path.chmod(0o755)
    return str(path)


def _hooks(planted: Path, marker: Path) -> str:
    hooks = planted / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in _HOOK_NAMES:
        (hooks / name).write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
        (hooks / name).chmod(0o755)
    return str(hooks)


def _include_file(planted: Path, marker: Path) -> str:
    extra = planted / "include"
    extra.write_text(f'[filter "evil"]\n\tclean = {_run(marker)}\n')
    return str(extra)


_HOOK_NAMES = ("pre-push", "pre-commit", "post-checkout", "reference-transaction")
_STATUS_M = ["status", "--porcelain"]
_CHECKOUT_M = ["checkout", "-q", "-f", "HEAD~1"]
_DIFF_M = ["diff", "HEAD~1"]
_FETCH_M = ["fetch", "-q", "origin"]
_SSH = "ssh://kraft.invalid/x"

#: Every program-valued key the bead names: its config, given the marker and a
#: directory for what it points at, and what runs it -- a git command, or for
#: a key nothing non-interactive runs, `("var", NAME)`/`("get", KEY)`: whether
#: git in the member resolves it at all.
PROGRAM_KEYS = {
    "hooks": (lambda m, p: {"core.hooksPath": _hooks(p, m)}, _CHECKOUT_M),
    "ssh-command": (
        lambda m, p: {"remote.origin.url": _SSH, "core.sshCommand": _run(m)},
        _FETCH_M,
    ),
    "ssh-variant": (
        lambda m, p: {"remote.origin.url": _SSH, "ssh.variant": "ssh", "core.sshCommand": _run(m)},
        _FETCH_M,
    ),
    "filter-clean": (lambda m, p: {"filter.evil.clean": _run(m)}, _STATUS_M),
    "filter-smudge": (lambda m, p: {"filter.evil.smudge": _run(m)}, _CHECKOUT_M),
    "filter-process": (lambda m, p: {"filter.evil.process": _run(m)}, _STATUS_M),
    "diff-external": (lambda m, p: {"diff.external": _run(m)}, _DIFF_M),
    "diff-command": (lambda m, p: {"diff.evil.command": _run(m)}, _DIFF_M),
    "textconv": (lambda m, p: {"diff.evil.textconv": _run(m)}, _DIFF_M),
    "pager": (lambda m, p: {"core.pager": _run(m)}, ("var", "GIT_PAGER")),
    "editor": (lambda m, p: {"core.editor": _run(m)}, ("var", "GIT_EDITOR")),
    "sequence-editor": (lambda m, p: {"sequence.editor": _run(m)}, ("var", "GIT_SEQUENCE_EDITOR")),
    "gpg": (
        lambda m, p: {"gpg.program": _program(p, m), "commit.gpgsign": "true"},
        ["commit", "-q", "--allow-empty", "-m", "signed"],
    ),
    "credential-helper": (
        lambda m, p: {
            "remote.origin.url": "https://kraft.invalid/x",
            "credential.helper": f"!{_run(m)}",
        },
        ["credential", "fill"],
    ),
    "alternate-refs-command": (
        lambda m, p: {"core.alternateRefsCommand": _program(p, m)},
        ("get", "core.alternateRefsCommand"),
    ),
    "pack-objects-hook": (
        lambda m, p: {"uploadpack.packObjectsHook": _program(p, m)},
        ("get", "uploadpack.packObjectsHook"),
    ),
    "fsmonitor": (lambda m, p: {"core.fsmonitor": _run(m)}, _STATUS_M),
    "include-path": (lambda m, p: {"include.path": _include_file(p, m)}, _STATUS_M),
    "include-if": (lambda m, p: {"includeIf.gitdir:/.path": _include_file(p, m)}, _STATUS_M),
}


def _plant_config(files: list[Path], config: dict[str, str]) -> None:
    for file in files:
        file.parent.mkdir(parents=True, exist_ok=True)
        for key, value in config.items():
            _w(file.parent, "config", "--file", str(file), key, value)


#: Every place a sandboxed worker can write that git could mistake for a
#: member's config: `(config files, hooks directory)`. The admin dir's
#: `config.worktree` is mounted read-only in the container; it is planted
#: anyway. `old-modules` is where the old layout kept the member's gitdir,
#: made by the worker.
PLANT_SITES = {
    "admin-config": lambda ws: ([ws.admin / "config", ws.admin / "config.worktree"], None),
    "admin-hooks": lambda ws: ([], ws.admin / "hooks"),
    "old-modules": lambda ws: (
        [ws.root_gitdir / "modules" / _REL / "config"],
        ws.root_gitdir / "modules" / _REL / "hooks",
    ),
}


def _container_read_only(ws, run_dirs) -> set[Path]:
    """What a sandboxed session in `ws` has mounted read-only, as
    `docker_argv` mounts the root and the member from their ref stores."""
    stores = refstore.prepare_stores(
        run_dirs.base, ws.wt, ws.branch, members={_REL: sandbox.member_gitdirs(ws.m, ws.wt, _REL)}
    )
    argv = docker.docker_argv(
        ["true"], ws.wt, {"kind": "docker", "image": "x"}, None, refstores=stores
    )
    return {
        Path(argv[i + 1].split(":")[0])
        for i, a in enumerate(argv)
        if a == "-v" and argv[i + 1].endswith(":ro")
    }


def _plant(ws, site: str, key: str, config: dict[str, str], marker: Path, planted: Path) -> None:
    """`config` into the site's config files the container can write; for the
    hooks key, the hooks themselves into its hooks directory too."""
    files, hooks = PLANT_SITES[site](ws)
    _plant_config([f for f in files if f not in ws.read_only], config)
    if hooks is not None and key == "hooks":
        _hooks(planted, marker)
        hooks.mkdir(parents=True, exist_ok=True)
        for name in _HOOK_NAMES:
            (hooks / name).write_bytes((planted / "hooks" / name).read_bytes())
            (hooks / name).chmod(0o755)


class _MergedForge:
    """`find_mr` for `_point_at_merged_members`: the member's branch merged at `sha`."""

    def __init__(self, sha: str):
        self.sha = sha

    async def find_mr(self, *, repo: Path, branch: str):
        return SimpleNamespace(state="merged", merged_sha=self.sha)


async def _every_member_host_call(ws, database) -> None:
    """Each host git Kraft runs with its cwd in a member, or in the root on
    the member's behalf -- one call per row of spec 2b's map -- then teardown.
    A git failure is swallowed: what is asserted is what ran, not what worked."""
    branch, identity = ws.branch, builtins.item_identity(database, ws.wid)
    moved = ws.m.parent / "mover"
    _w(ws.m.parent, "clone", "-q", str(ws.m_origin), str(moved))
    (moved / "upstream.txt").write_text("landed meanwhile\n")
    _w(moved, "add", "upstream.txt")
    _w(
        moved,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "origin main moves on",
    )
    _w(moved, "push", "-q", "origin", "main")
    await database.write(
        lambda c: c.execute(
            "UPDATE work_item_repos SET merge_state = 'merged' "
            "WHERE work_item_id = ? AND role = 'submodule'",
            (ws.wid,),
        )
    )
    calls = [
        # M3: the straggler sweep and the clean check, member then root.
        lambda: forge.commit_stragglers(
            ws.member, branch=branch, base="main", message="wip", identity=identity
        ),
        lambda: forge.assert_clean(ws.member, "main"),
        lambda: forge.commit_stragglers(
            ws.wt, branch=branch, base="main", message="wip", mounts=[_REL], identity=identity
        ),
        lambda: forge.assert_clean(ws.wt, "main"),
        # M2: publication's reads of the member's branch.
        lambda: forge.git.commits_ahead(ws.member, branch, "main"),
        lambda: forge.git.commits_on(ws.member, branch, "main"),
        lambda: forge.git.source_changed(ws.member, branch, base="main", exclude=set()),
        lambda: forge.git._head_sha(ws.member),
        # M9: the root's coverage check, which describes each member.
        lambda: forge.git._assert_submodules_covered(ws.wt, {ws.member.resolve()}),
        # M1: the push, unhardened, member and root.
        lambda: forge.git.push(ws.member, branch),
        lambda: forge.git.push(ws.wt, branch),
        # M5/M6: the member rebased onto its moved origin; forced, as
        # `mr_rebase_forced` is, since the push above published the branch.
        lambda: builtins.refresh_worktree_base(
            ws.member, ws.m, branch, base="main", force=True, identity=identity
        ),
        # M4: the root pointed at the member's merge, landed on origin's main.
        lambda: _point_at_merged_members(
            _MergedForge(_w(ws.m_origin, "rev-parse", "main")), database, ws.wt, branch, ws.wid
        ),
        # M10: the read-only check's reads.
        lambda: read_only._read(ws.wt),
        lambda: read_only._read(ws.member),
        # M7: identity, root only now.
        lambda: builtins._pin_identity(ws.root, ws.wt, ws.wid),
        lambda: review.read_change(ws.wt, "HEAD~1"),
        lambda: builtins.restore_branch(ws.wt, branch, "main"),
        lambda: builtins.restore_branch(ws.member, branch, "main"),
        # Teardown last.
        lambda: lifecycle._remove_worktree(ws.root, ws.wt, branch, ws.wid, [ws.m]),
    ]
    for call in calls:
        with contextlib.suppress(forge.ForgeError, RuntimeError):
            done = call()
            if inspect.isawaitable(done):
                await done


def _visible(cwd: Path, config: dict[str, str], env: dict[str, str] | None = None) -> list[str]:
    """Which of `config` git in `cwd` resolves, as `config --show-origin` lists it."""
    listed = subprocess.run(
        ["git", "config", "--show-origin", "--get-regexp", "."],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    return [k for k, v in config.items() if f"{k.lower()} {v}" in listed]


def _trigger(cwd: Path, trigger) -> str:
    """Run `trigger` as the worker would, outside Kraft's reach; its stdout."""
    env = {**_worker_env(), "GIT_TERMINAL_PROMPT": "0"}
    if trigger[0] in ("var", "get"):
        argv = ["var", trigger[1]] if trigger[0] == "var" else ["config", "--get", trigger[1]]
    else:
        argv = trigger
    return subprocess.run(
        ["git", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        input="url=https://kraft.invalid/x\n\n",
        timeout=60,
    ).stdout.strip()


_CASES = [
    pytest.param(key, site, worktree_config, id=f"{key}-{site}{suffix}")
    for worktree_config, suffix in ((False, ""), (True, "-worktree-config"))
    for key in PROGRAM_KEYS
    for site in PLANT_SITES
    # A hooks directory holds hooks, not config: only the hooks key plants there.
    if site != "admin-hooks" or key == "hooks"
]


@pytest.fixture
def no_env_programs(monkeypatch):
    """No pager, editor or sequence editor from the environment, which would
    outrank any config and make the control say nothing about it."""
    for name in ("GIT_PAGER", "PAGER", "GIT_EDITOR", "VISUAL", "EDITOR", "GIT_SEQUENCE_EDITOR"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(("key", "site", "worktree_config"), _CASES)
async def test_no_host_path_runs_a_program_planted_for_a_member(
    tmp_path, database, run_dirs, hardened, no_env_programs, monkeypatch, key, site, worktree_config
):
    """Kraft-ju36l, one key at one site, under an operator config that asks
    git to recurse every way it can: nothing runs, and the key is not even
    visible to git in the member or the root. Drift -- a member swapped for
    one that does read the site -- is `refuse_planted_repos`', not this."""
    config, trigger = PROGRAM_KEYS[key]
    operator = tmp_path / "operator.gitconfig"
    operator.write_text(_OPERATOR_CONFIG)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(operator))
    kraft, control = tmp_path / "kraft", tmp_path / "control"
    ws = await _workspace(database, run_dirs, kraft, "w1", worktree_config=worktree_config)
    ws.read_only = _container_read_only(ws, run_dirs)
    (kraft / "planted").mkdir()
    planted = config(kraft / "PWNED", kraft / "planted")
    _plant(ws, site, key, planted, kraft / "PWNED", kraft / "planted")

    seen = [_visible(cwd, planted) for cwd in (ws.member, ws.wt)]
    await _every_member_host_call(ws, database)

    assert not (kraft / "PWNED").exists()
    assert seen == [[], []]

    # The control: the same key in `M`'s own config, where git does read it.
    cws = await _workspace(database, run_dirs, control, "w2")
    (control / "planted").mkdir()
    live = config(control / "PWNED", control / "planted")
    _plant_config([cws.m / ".git" / "config"], live)
    out = _trigger(cws.member, trigger)
    if trigger[0] in ("var", "get"):
        assert out == next(iter(live.values())), f"control: {key} is not live"
    else:
        assert (control / "PWNED").exists(), f"control: {key} is not live"


async def test_a_setup_command_cannot_seed_a_members_gitdir(tmp_path, database, run_dirs, hardened):
    """M8: a sandboxed setup command runs before the members are checked out,
    with the root's worktree gitdir writable. It builds a complete gitdir
    where the old layout put the member's -- smudge filter, include, and a
    post-checkout hook -- which `submodule update --init` would have reused,
    running them on the host. The member is a worktree of `M` instead.
    (Run on the host here, writing only what the container could.)"""
    marker = tmp_path / "PWNED"
    include = tmp_path / "planted-include"
    include.write_text(f'[filter "evil"]\n\tsmudge = {_run(marker)}\n')
    seed = tmp_path / "seed.sh"
    seed.write_text(
        f"""set -e
g="$(git rev-parse --path-format=absolute --git-dir)/modules/{_REL}"
git clone -q --bare {shlex.quote(str(tmp_path / "side" / "pkg"))} "$g"
git --git-dir="$g" config core.bare false
git --git-dir="$g" config filter.evil.smudge {shlex.quote(_run(marker))}
git --git-dir="$g" config include.path {shlex.quote(str(include))}
mkdir -p "$g/hooks"
printf '#!/bin/sh\\ntouch %s\\n' {shlex.quote(str(marker))} > "$g/hooks/post-checkout"
chmod +x "$g/hooks/post-checkout"
"""
    )
    ws = await _workspace(database, run_dirs, tmp_path / "side", "w1", setup=f"sh {seed}")

    assert (ws.root_gitdir / "modules" / _REL / "config").is_file(), "the seed never landed"
    assert not marker.exists()
    common = _w(ws.member, "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert common == str((ws.m / ".git").resolve())
