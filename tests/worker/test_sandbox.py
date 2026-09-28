import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest
from support.harness import fake_docker_bin, make_repo

from kraft.worker import sandbox
from kraft.worker.refstore import RefStore


def test_docker_argv_wraps_the_command_and_forwards_the_fixed_env_set():
    cmd = ["pytest", "-q"]
    argv = sandbox.docker_argv(
        cmd, "/work/item-1", {"kind": "docker", "image": "kraft-worker:py"}, "/run/results"
    )
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("-u") + 1] == f"{os.getuid()}:{os.getgid()}"
    # A setuid-root binary in the operator's image (su, mount, sudo) would
    # otherwise let the worker regain root and defeat the -u uid:gid above.
    assert "--security-opt=no-new-privileges" in argv
    assert "--cap-drop=ALL" in argv
    assert "/work/item-1:/work/item-1" in argv
    assert "/run/results:/run/results:ro" in argv
    w_i = argv.index("-w")
    assert argv[w_i + 1] == "/work/item-1"
    for name in sandbox.FORWARDED_ENV:
        i = argv.index(name)
        assert argv[i - 1] == "-e"
    image_i = argv.index("kraft-worker:py")
    assert argv[image_i + 1 :] == cmd


def _worktree(tmp_path):
    """A Kraft worktree: `.git` is a file pointing at `<repo>/.git/worktrees/<id>`,
    outside the worktree itself."""
    repo = tmp_path / "repo"
    gitdir = repo / ".git" / "worktrees" / "item-1"
    gitdir.mkdir(parents=True)
    for name in ("objects", "refs", "hooks", "modules"):
        (repo / ".git" / name).mkdir()
    (repo / ".git" / "config").write_text("[core]\n")
    worktree = tmp_path / "item-1"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n")
    return repo, worktree, gitdir


def test_docker_argv_mounts_the_repo_gitdir_read_only_without_a_ref_store(tmp_path):
    """Without a ref store the worker gets no writable ref anywhere in the
    operator's repository: a commit fails rather than move a shared ref."""
    repo, worktree, gitdir = _worktree(tmp_path)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    git = repo / ".git"
    assert f"{git}:{git}:ro" in argv
    for name in ("objects", "refs", "logs"):
        assert f"{git / name}:{git / name}" not in argv
    assert f"{gitdir}:{gitdir}" in argv


def test_docker_argv_mounts_the_ref_store_over_the_repo_gitdir(tmp_path):
    """The worker's refs land in the store; only objects and LFS content,
    which are data, reach the real gitdir read-write."""
    repo, worktree, gitdir = _worktree(tmp_path)
    git = repo / ".git"
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "lfs").mkdir()
    store = RefStore(tmp_path / "store", git, gitdir, "kraft/x")
    argv = sandbox.docker_argv(
        ["git", "status"],
        worktree,
        {"kind": "docker", "image": "x"},
        "/run/results",
        refstore=store,
    )
    assert f"{store.shadow}:{git}" in argv
    assert f"{git / 'objects'}:{git / 'objects'}" in argv
    assert f"{git / 'lfs'}:{git / 'lfs'}" in argv
    assert f"{git / 'config'}:{git / 'config'}:ro" in argv
    assert f"{git / 'HEAD'}:{git / 'HEAD'}:ro" in argv
    rw_sources = [
        Path(argv[i + 1].split(":")[0])
        for i, a in enumerate(argv)
        if a == "-v" and not argv[i + 1].endswith(":ro")
    ]
    assert git not in rw_sources
    assert not [p for p in rw_sources if p.is_relative_to(git / "refs")]


def test_docker_argv_mounts_every_alternate_object_dir_read_only(tmp_path):
    """A `clone --shared` repository borrows objects from outside every other
    mount; without its own mount the container reads `bad object HEAD`."""
    repo, worktree, gitdir = _worktree(tmp_path)
    borrowed = tmp_path / "upstream" / "objects"
    borrowed.mkdir(parents=True)
    (repo / ".git" / "objects" / "info").mkdir()
    (repo / ".git" / "objects" / "info" / "alternates").write_text(f"{borrowed}\n")
    store = RefStore(tmp_path / "store", repo / ".git", gitdir, "kraft/x")
    argv = sandbox.docker_argv(
        ["git", "log"],
        worktree,
        {"kind": "docker", "image": "x"},
        "/run/results",
        refstore=store,
    )
    assert f"{borrowed}:{borrowed}:ro" in argv


def test_docker_argv_leaves_every_host_code_execution_path_read_only(tmp_path):
    """Kraft-rki: the escape this closes is a hook (or a `core.hooksPath`) the
    container writes into the repo gitdir, which then runs as the invoking
    host user the next time Kraft runs git in `<repo>` itself. Nothing under
    the read-only mount is carved back out -- not `hooks/`, not `config`, and
    not `modules/`, where a submodule's own gitdir (and its own `hooks/`)
    lives one directory over."""
    repo, worktree, _ = _worktree(tmp_path)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    git = repo / ".git"
    rw_mounts = [
        argv[i + 1] for i, a in enumerate(argv) if a == "-v" and not argv[i + 1].endswith(":ro")
    ]
    for mount in rw_mounts:
        source = Path(mount.split(":")[0])
        assert not source.is_relative_to(git / "hooks")
        assert not source.is_relative_to(git / "modules")
        assert source != git / "config"
        assert source != git


def test_docker_argv_mounts_the_worktree_git_file_read_only(tmp_path):
    """`<worktree>/.git` is a plain file inside the read-write `cwd` mount --
    left writable, a worker repoints it at a gitdir of its own making, hooks
    and all, and the next host-side git command in the worktree runs it."""
    _, worktree, _ = _worktree(tmp_path)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    git_file = worktree / ".git"
    assert f"{git_file}:{git_file}:ro" in argv


def test_docker_argv_shadows_commondir_read_only(tmp_path):
    """`commondir` inside the read-write worktree gitdir names where `hooks/`
    and `config` resolve to -- a worker that rewrites it to point at its own
    directory gets its own hooks run as the host user next time git runs
    here."""
    _, worktree, gitdir = _worktree(tmp_path)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    commondir = gitdir / "commondir"
    # Created, not guarded with exists(), for the same reason `modules` is.
    assert commondir.is_file()
    assert f"{commondir}:{commondir}:ro" in argv


def test_docker_argv_shadows_config_worktree_read_only(tmp_path):
    """`config.worktree` is the other file inside the read-write worktree
    gitdir git reads as part of its config stack once
    `extensions.worktreeConfig = true` (which `git sparse-checkout set`
    enables) -- a worker that plants `core.hooksPath` there gets it run as
    the host user next time git runs here, same as through `commondir`."""
    _, worktree, gitdir = _worktree(tmp_path)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    worktree_config = gitdir / "config.worktree"
    assert worktree_config.is_file()
    assert f"{worktree_config}:{worktree_config}:ro" in argv


def test_docker_argv_mounts_only_this_session_s_result_file_read_write(tmp_path):
    """The results dir is one directory for every work item: a worker that
    could write it could forge another session's status and findings. It
    still has to *read* it (its own review package, the previous fix
    session's result file), so it is mounted read-only with just this
    session's own result file carved back out."""
    argv = sandbox.docker_argv(
        ["claude"],
        "/work/item-1",
        {"kind": "docker", "image": "x"},
        "/run/results",
        result_path="/run/results/s1.json",
    )
    assert "/run/results:/run/results:ro" in argv
    assert "/run/results/s1.json:/run/results/s1.json" in argv


def test_docker_argv_mounts_nothing_extra_for_a_plain_git_dir(tmp_path):
    worktree = tmp_path / "repo"
    (worktree / ".git").mkdir(parents=True)
    before = sandbox.docker_argv(
        ["true"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    assert before.count("-v") == 2  # cwd and results_dir only


def test_docker_argv_forwards_env_as_literal_values():
    argv = sandbox.docker_argv(
        ["pytest"],
        "/work/item-1",
        {"kind": "docker", "image": "x"},
        "/run/results",
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    i = argv.index("PYTHONDONTWRITEBYTECODE=1")
    assert argv[i - 1] == "-e"


def test_docker_argv_names_the_container_when_asked():
    argv = sandbox.docker_argv(
        ["pytest"],
        "/work/item-1",
        {"kind": "docker", "image": "x"},
        "/run/results",
        name="kraft-s1",
    )
    n_i = argv.index("--name")
    assert argv[n_i + 1] == "kraft-s1"


def test_container_name_is_prefixed_and_stable():
    assert sandbox.container_name("s1") == "kraft-s1"


def test_docker_argv_leaves_a_submodule_gitdir_writable(tmp_path):
    """A submodule `builtins._setup_submodules` initialized lives at
    `<worktree gitdir>/modules/<sm>`, inside the gitdir this session has to
    write: `forge.git._assert_submodules_covered` refuses to open the root
    merge request while a submodule has uncovered commits, so a read-only
    mount there either drops that work or stalls the chain. It is writable
    -- including its own `hooks/` -- and `harden_host_git_env`, not a shadow
    mount, is what stops what lands there from running on the host."""
    _, worktree, gitdir = _worktree(tmp_path)
    sm_gitdir = gitdir / "modules" / "libs" / "sm"
    sm_gitdir.mkdir(parents=True)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    assert f"{gitdir}:{gitdir}" in argv
    assert not [a for a in argv if a.startswith(f"{gitdir / 'modules'}") and a.endswith(":ro")]


def test_docker_argv_writes_nothing_into_a_submodule_gitdir(tmp_path):
    """The revision this replaced walked `modules` with `rglob("HEAD")` and
    `touch`ed a `config` beside every hit -- including `refs/remotes/origin/HEAD`
    in the operator's own repo, which `git fsck` then reports as
    badRefContent. Building an argv list must not write into a real gitdir."""
    _, worktree, gitdir = _worktree(tmp_path)
    sm_gitdir = gitdir / "modules" / "sm"
    (sm_gitdir / "refs" / "remotes" / "origin").mkdir(parents=True)
    (sm_gitdir / "refs" / "remotes" / "origin" / "HEAD").write_text("ref: refs/heads/main\n")
    sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    assert sorted(p.name for p in (sm_gitdir / "refs" / "remotes" / "origin").iterdir()) == ["HEAD"]


def test_harden_host_git_env_pins_config_as_git_s_own_env_form():
    env = {}
    sandbox.harden_host_git_env(env)
    count = int(env["GIT_CONFIG_COUNT"])
    pinned = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)}
    assert pinned["core.hooksPath"] == os.devnull
    assert pinned["core.fsmonitor"] == ""


def _repo_with_a_planted_hook(tmp_path):
    """A repo whose config points `core.hooksPath` at a hook that writes a
    marker -- what a worker plants through any of the redirect files inside
    the gitdir it must be able to write."""
    repo = make_repo(tmp_path)
    hooks = tmp_path / "planted-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.hooksPath", str(hooks)], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
    return repo, marker


def test_a_planted_hook_runs_without_the_hardened_env(tmp_path):
    """The other half of the test below: without it, this hook really does
    execute, so the assertion there is pinning the hardening and not a repo
    that could never have run a hook in the first place.

    Runs with `sandbox.unhardened_git_env()` rather than the inherited
    process env: a test session started under Kraft is itself a child of a
    process that already called `harden_host_git_env` (Kraft-rki), so the
    ambient env cannot be trusted to be unhardened."""
    repo, marker = _repo_with_a_planted_hook(tmp_path)
    subprocess.run(
        ["git", "commit", "-m", "x"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=sandbox.unhardened_git_env(),
    )
    assert marker.exists()


def test_hardened_env_stops_a_planted_hook_from_running(tmp_path):
    """Kraft-rki, the load-bearing half: a worker's worktree gitdir is
    writable by construction, so the guarantee cannot come from shadowing
    whichever file it plants `core.hooksPath` in. It comes from here -- git's
    own `GIT_CONFIG_COUNT` env form carries `-c` precedence, so it beats the
    repo and worktree config files that hook was configured in."""
    repo, marker = _repo_with_a_planted_hook(tmp_path)
    env = dict(os.environ)
    sandbox.harden_host_git_env(env)
    subprocess.run(
        ["git", "commit", "-m", "x"], cwd=repo, check=True, capture_output=True, text=True, env=env
    )
    assert not marker.exists()


def test_hardened_env_still_lets_git_diff_run(tmp_path):
    """`diff.external` looks like it belongs in the pinned set and does not:
    git reads an empty value as the empty command and dies on every diff.
    This is the check that catches putting it back."""
    repo, _ = _repo_with_a_planted_hook(tmp_path)
    env = dict(os.environ)
    sandbox.harden_host_git_env(env)
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.stdout.split() == ["f.txt"]


def test_docker_argv_mounts_a_symlinked_worktree_at_its_real_path(tmp_path):
    """git records a worktree's real path in its `gitdir` backlink; mounted
    only at a symlinked path, the worktree looks gone to `git worktree prune`
    inside the container, which then deletes its admin directory."""
    _, worktree, gitdir = _worktree(tmp_path)
    (gitdir / "gitdir").write_text(f"{worktree}/.git\n")
    link = tmp_path / "link"
    link.symlink_to(worktree)
    argv = sandbox.docker_argv(["git", "status"], link, {"kind": "docker", "image": "x"}, None)
    assert f"{worktree}:{worktree}" in argv
    assert argv[argv.index("-w") + 1] == str(worktree)
    backlink = gitdir / "gitdir"
    assert f"{backlink}:{backlink}:ro" in argv


def test_docker_argv_gives_the_worker_a_home_of_its_own(tmp_path):
    """A uid with no passwd entry in the image gets HOME=/, where no agent CLI
    can write its state; codex and gemini then refuse to start at all."""
    home = tmp_path / "home"
    argv = sandbox.docker_argv(["claude"], "/w", {"kind": "docker", "image": "x"}, None, home=home)
    assert f"{home}:{home}" in argv
    assert argv[argv.index(f"HOME={home}") - 1] == "-e"


def test_docker_argv_forwards_passthrough_names_bare():
    """A repo's `env_passthrough` is how a worker gets a credential; forwarded
    by name, its value never lands on the argv (`ps`, `docker inspect`)."""
    argv = sandbox.docker_argv(
        ["codex"], "/w", {"kind": "docker", "image": "x"}, None, passthrough=["OPENAI_API_KEY"]
    )
    assert argv[argv.index("OPENAI_API_KEY") - 1] == "-e"
    assert not [a for a in argv if a.startswith("OPENAI_API_KEY=")]


def test_docker_argv_pins_hooks_off_for_git_in_the_container():
    """A hook manager's hook points at a host interpreter, so it fails every
    commit in the container; and a hook is the worker's code anyway."""
    argv = sandbox.docker_argv(["git"], "/w", {"kind": "docker", "image": "x"}, None)
    env = dict(a.split("=", 1) for a in argv if a.startswith("GIT_CONFIG_"))
    pinned = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert pinned["core.hooksPath"] == os.devnull


def test_docker_argv_mounts_extra_paths_at_the_same_path(tmp_path):
    argv = sandbox.docker_argv(
        ["amp"],
        "/w",
        {"kind": "docker", "image": "x"},
        None,
        ro_paths=["/k/rules.json"],
        rw_paths=["/k/config"],
    )
    assert "/k/rules.json:/k/rules.json:ro" in argv
    assert "/k/config:/k/config" in argv


def test_git_identity_comes_from_the_repository_when_the_daemon_has_none(
    repo, tmp_path, monkeypatch
):
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    subprocess.run(["git", "config", "user.name", "Op"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "op@x"], cwd=repo, check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "b", str(wt)], cwd=repo, check=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Daemon")
    assert sandbox.git_identity(wt) == {
        "GIT_AUTHOR_NAME": "Daemon",
        "GIT_AUTHOR_EMAIL": "op@x",
        "GIT_COMMITTER_NAME": "Op",
        "GIT_COMMITTER_EMAIL": "op@x",
    }


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """The fake `docker` first on PATH; returns the file `docker rm` logs to."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    log = tmp_path / "rm-log"
    monkeypatch.setenv("FAKE_DOCKER_RM_LOG", str(log))
    return log


async def test_sweep_removes_only_containers_no_live_session_owns(
    fake_docker, tmp_path, monkeypatch
):
    listed = tmp_path / "ps"
    listed.write_text("kraft-live\nkraft-orphan\n")
    monkeypatch.setenv("FAKE_DOCKER_PS", str(listed))
    assert await sandbox.sweep_orphans(keep={"kraft-live"}) == ["kraft-orphan"]
    assert fake_docker.read_text().split() == ["kraft-orphan"]


async def test_teardown_gives_up_on_a_daemon_that_never_answers(tmp_path, monkeypatch):
    """A wedged daemon hung `docker rm -f` forever, and teardown is awaited on
    every session's way out and for every row at startup."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexec sleep 30\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sandbox, "DOCKER_CALL_TIMEOUT_S", 0.2)
    started = time.monotonic()
    await asyncio.wait_for(sandbox.teardown("s1"), 5)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "executable, missing", [("sh", False), ("kraft-no-such-cli", True)], ids=["present", "absent"]
)
async def test_an_image_without_the_command_is_told_apart(fake_docker, executable, missing):
    assert await sandbox.missing_executable("img", executable) is missing


async def test_an_inconclusive_image_check_never_blocks_a_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no docker at all
    assert await sandbox.missing_executable("img", "kraft-no-such-cli") is False
