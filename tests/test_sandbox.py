import os
import subprocess
from pathlib import Path

import pytest

from kraft import sandbox


def test_resolve_falls_through_to_the_binding_when_repo_sets_nothing():
    binding = {"kind": "agent", "command": "claude", "sandbox": {"kind": "docker", "image": "x"}}
    assert sandbox.resolve(binding, {}) == {"kind": "docker", "image": "x"}
    assert sandbox.resolve(binding, None) == {"kind": "docker", "image": "x"}


def test_resolve_repo_overrides_wholesale_with_its_own_image():
    binding = {"kind": "agent", "command": "claude", "sandbox": {"kind": "docker", "image": "x"}}
    repo = {"sandbox": {"kind": "docker", "image": "y"}}
    assert sandbox.resolve(binding, repo) == {"kind": "docker", "image": "y"}


def test_resolve_repo_can_turn_off_a_binding_that_turned_sandboxing_on():
    binding = {"kind": "agent", "command": "claude", "sandbox": {"kind": "docker", "image": "x"}}
    assert sandbox.resolve(binding, {"sandbox": False}) is None


def test_resolve_repo_can_turn_on_sandboxing_a_binding_left_unset():
    binding = {"kind": "subprocess", "command": ["pytest"]}
    repo = {"sandbox": {"kind": "docker", "image": "y"}}
    assert sandbox.resolve(binding, repo) == {"kind": "docker", "image": "y"}


def test_resolve_is_none_when_neither_sets_one():
    assert sandbox.resolve({"kind": "agent", "command": "claude"}, {}) is None


def test_validate_rejects_a_non_mapping():
    with pytest.raises(sandbox.SandboxError, match="mapping"):
        sandbox.validate("docker", where="x")


def test_validate_rejects_an_unknown_kind():
    with pytest.raises(sandbox.SandboxError, match="podman"):
        sandbox.validate({"kind": "podman", "image": "y"}, where="x")


def test_validate_rejects_a_missing_image():
    with pytest.raises(sandbox.SandboxError, match="image"):
        sandbox.validate({"kind": "docker"}, where="x")


def test_validate_accepts_a_well_formed_sandbox():
    sandbox.validate({"kind": "docker", "image": "kraft-worker:py"}, where="x")


def test_docker_argv_wraps_the_command_and_forwards_the_fixed_env_set():
    cmd = ["pytest", "-q"]
    argv = sandbox.docker_argv(
        cmd, "/work/item-1", {"kind": "docker", "image": "kraft-worker:py"}, "/run/results"
    )
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[3:5] == ["-u", f"{os.getuid()}:{os.getgid()}"]
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


def test_docker_argv_mounts_the_repo_gitdir_read_only(tmp_path):
    repo, worktree, gitdir = _worktree(tmp_path)
    argv = sandbox.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    git = repo / ".git"
    assert f"{git}:{git}:ro" in argv
    # Read-write only where a commit from a linked worktree actually writes.
    for name in ("objects", "refs", "logs"):
        assert f"{git / name}:{git / name}" in argv
    assert f"{gitdir}:{gitdir}" in argv


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


def test_docker_argv_creates_the_reflog_dir_a_commit_needs(tmp_path):
    """`logs/` does not exist in a fresh clone and git makes it on the first
    ref update -- which it cannot do under a read-only mount."""
    repo, worktree, _ = _worktree(tmp_path)
    assert not (repo / ".git" / "logs").exists()
    sandbox.docker_argv(["git", "commit"], worktree, {"kind": "docker", "image": "x"}, "/run/res")
    assert (repo / ".git" / "logs").is_dir()


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
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks = tmp_path / "planted-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, check=True, capture_output=True, text=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("config", "core.hooksPath", str(hooks))
    (repo / "f.txt").write_text("x\n")
    run("add", "f.txt")
    return repo, marker


def test_a_planted_hook_runs_without_the_hardened_env(tmp_path):
    """The other half of the test below: without it, this hook really does
    execute, so the assertion there is pinning the hardening and not a repo
    that could never have run a hook in the first place."""
    repo, marker = _repo_with_a_planted_hook(tmp_path)
    subprocess.run(
        ["git", "commit", "-m", "x"], cwd=repo, check=True, capture_output=True, text=True
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
