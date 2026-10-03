"""`forge.git`'s record of what a setup command wrote, and what the straggler
sweep leaves out because of it: only the setup's own lockfiles, as the setup
left them, and only a directory a package manager really installed into.
The sweep's other cases: test_git.py."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft.adapters import forge
from kraft.worker import sandbox


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=sandbox.unhardened_git_env(),
    ).stdout


def _record(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--absolute-git-dir").strip()) / forge.git.SETUP_WROTE


def _setup_writes(repo: Path, name: str = "uv.lock", text: str = "by the setup\n") -> None:
    """What `builtins._prepare` does around a setup that writes `name`."""
    before = asyncio.run(forge.lockfile_digests(repo))
    (repo / name).write_text(text)
    asyncio.run(forge.record_setup_writes(repo, before))


def _sweep(repo: Path) -> list[str]:
    """Run the sweep; what its commit took."""
    branch = _git(repo, "branch", "--show-current").strip()
    asyncio.run(forge.commit_stragglers(repo, branch=branch, base="main", message="wip: t"))
    return sorted(_git(repo, "show", "--name-only", "--format=", "HEAD").split())


def test_an_agents_edit_to_the_setups_lockfile_is_committed(tmp_path):
    """In a repo that commits no `uv.lock`, the setup always writes one first,
    so an agent that changes it (`uv add`) had its change left out. The
    record holds what the setup wrote; a different content is the agent's."""
    repo = make_repo(tmp_path)
    _setup_writes(repo)
    (repo / "uv.lock").write_text("by the setup\nplus the agent's dependency\n")
    (repo / "work.py").write_text("x = 1\n")

    assert asyncio.run(forge.setup_lockfiles(repo)) == set()
    assert _sweep(repo) == ["uv.lock", "work.py"]


def test_a_setup_rerun_keeps_its_own_lockfile_but_not_one_the_agent_edited(tmp_path):
    """Every walk entry runs the setup again. Its own untouched lockfile
    stays its own, with what it wrote this time; one the agent edited in
    between is the agent's, even after the setup wrote over it."""
    repo = make_repo(tmp_path)
    _setup_writes(repo, text="v1\n")
    _setup_writes(repo, text="v2\n")
    assert asyncio.run(forge.setup_lockfiles(repo)) == {"uv.lock"}

    (repo / "uv.lock").write_text("v2, edited by the agent\n")
    _setup_writes(repo, text="v3\n")

    assert asyncio.run(forge.setup_wrote(repo)) == {}


def test_only_a_lockfile_name_in_the_record_is_left_out(tmp_path):
    """The record sits in a git dir a worker can write. A path in it that is
    not a lockfile name was never the setup's: it is committed, as any
    file the agent left."""
    repo = make_repo(tmp_path)
    (repo / "evil_conftest.py").write_text("import os\n")
    (repo / "uv.lock").write_text("by the setup\n")
    _record(repo).write_text(json.dumps({"evil_conftest.py": None, "uv.lock": None}))

    assert asyncio.run(forge.setup_wrote(repo)) == {"uv.lock": None}
    assert _sweep(repo) == ["evil_conftest.py"]
    asyncio.run(forge.assert_clean(repo, "main"))


@pytest.mark.parametrize(
    "content",
    [b"uv.lock\n\xff\xfe\n", b'{"uv.lock": ', b'["uv.lock"]'],
    ids=["undecodable", "truncated", "not-an-object"],
)
def test_a_corrupt_record_counts_as_empty_and_never_fails_the_sweep(tmp_path, caplog, content):
    """A raw `UnicodeDecodeError` used to escape the sweep, failing a
    successful agent task and every retry after it. The record is ignored,
    and the log says so; the lockfile then reads as the agent's."""
    repo = make_repo(tmp_path)
    (repo / "uv.lock").write_text("by the setup\n")
    _record(repo).write_bytes(content)

    with caplog.at_level(logging.WARNING, logger="kraft.adapters.forge.git"):
        assert asyncio.run(forge.setup_wrote(repo)) == {}
    assert "setup record" in caplog.text
    assert _sweep(repo) == ["uv.lock"]


def test_a_record_from_before_digests_still_leaves_its_lockfile_out(tmp_path):
    """1.5.0rc14 wrote one path per line, with no digest: still honoured."""
    repo = make_repo(tmp_path)
    (repo / "uv.lock").write_text("by the setup\n")
    (repo / "work.py").write_text("x = 1\n")
    _record(repo).write_text("uv.lock\n")

    assert _sweep(repo) == ["work.py"]


@pytest.mark.parametrize(
    "files",
    [
        ("node_modules/fixpkg/index.js",),
        ("env/pyvenv.cfg", "env/lib/site.py"),
    ],
    ids=["node_modules-with-no-marker", "bare-pyvenv-cfg"],
)
def test_a_directory_that_only_looks_like_an_install_is_committed(tmp_path, files):
    """A resolver's test fixture named `node_modules`, or a directory with a
    `pyvenv.cfg` and no interpreter, is the agent's work, not an install:
    it used to be dropped from the merge request with no trace."""
    repo = make_repo(tmp_path)
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("work\n")

    assert asyncio.run(forge.environment_paths(repo, "main")) == []
    assert _sweep(repo) == sorted(files)


@pytest.mark.parametrize(
    ("files", "install"),
    [
        (("node_modules/.modules.yaml", "node_modules/x/i.js"), "node_modules"),
        (("web/package.json", "web/node_modules/x/i.js"), "web/node_modules"),
        (("env/pyvenv.cfg", "env/Scripts/python.exe"), "env"),
    ],
    ids=["pnpm-marker", "beside-package-json", "windows-venv"],
)
def test_a_real_install_is_left_out(tmp_path, files, install):
    """A `package.json` beside it is the repo's own, so committed first."""
    repo = make_repo(tmp_path)
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("installed\n")
        if f.endswith("package.json"):
            _git(repo, "add", f)
            _git(repo, "commit", "-q", "-m", "a package")

    assert asyncio.run(forge.environment_paths(repo, "main")) == [install]


def test_a_cargo_lock_the_setup_wrote_is_left_out(tmp_path):
    """`cargo fetch`, which connect proposes for a crate, writes one where
    the crate commits none."""
    repo = make_repo(tmp_path)
    _setup_writes(repo, "Cargo.lock")
    (repo / "work.py").write_text("x = 1\n")

    assert _sweep(repo) == ["work.py"]
