import asyncio
import os
import time
from pathlib import Path

import pytest
from support.harness import fake_docker_bin

from kraft.config import ConfigError
from kraft.worker import sandbox
from kraft.worker.backends import docker
from kraft.worker.refstore import RefStore


def test_docker_argv_wraps_the_command_and_forwards_the_fixed_env_set():
    cmd = ["pytest", "-q"]
    argv = docker.docker_argv(
        cmd, "/work/item-1", {"kind": "docker", "image": "kraft-worker:py"}, "/run/results"
    )
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("-u") + 1] == f"{os.getuid()}:{os.getgid()}"
    # A setuid-root binary in the operator's image (su, mount, sudo) would
    # otherwise let the worker regain root and defeat the -u uid:gid above.
    assert "--security-opt=no-new-privileges" in argv
    assert "--cap-drop=ALL" in argv
    # PID 1 ignores a signal it has no handler for: without an init, pause and
    # cap signals never reach the agent.
    assert "--init" in argv
    # What `sweep_orphans` finds this Kraft's containers by.
    assert argv[argv.index("--label") + 1] == docker.home_label()
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    argv = docker.docker_argv(
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
    before = docker.docker_argv(
        ["true"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    assert before.count("-v") == 2  # cwd and results_dir only


def test_docker_argv_forwards_env_as_literal_values():
    argv = docker.docker_argv(
        ["pytest"],
        "/work/item-1",
        {"kind": "docker", "image": "x"},
        "/run/results",
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    i = argv.index("PYTHONDONTWRITEBYTECODE=1")
    assert argv[i - 1] == "-e"


def test_docker_argv_names_the_container_when_asked():
    argv = docker.docker_argv(
        ["pytest"],
        "/work/item-1",
        {"kind": "docker", "image": "x"},
        "/run/results",
        name="kraft-s1",
    )
    n_i = argv.index("--name")
    assert argv[n_i + 1] == "kraft-s1"


def test_container_name_is_prefixed_and_stable():
    assert docker.container_name("s1") == "kraft-s1"


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
    argv = docker.docker_argv(
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
    docker.docker_argv(
        ["git", "status"], worktree, {"kind": "docker", "image": "x"}, "/run/results"
    )
    assert sorted(p.name for p in (sm_gitdir / "refs" / "remotes" / "origin").iterdir()) == ["HEAD"]


def test_docker_argv_mounts_a_symlinked_worktree_at_its_real_path(tmp_path):
    """git records a worktree's real path in its `gitdir` backlink; mounted
    only at a symlinked path, the worktree looks gone to `git worktree prune`
    inside the container, which then deletes its admin directory."""
    _, worktree, gitdir = _worktree(tmp_path)
    (gitdir / "gitdir").write_text(f"{worktree}/.git\n")
    link = tmp_path / "link"
    link.symlink_to(worktree)
    argv = docker.docker_argv(["git", "status"], link, {"kind": "docker", "image": "x"}, None)
    assert f"{worktree}:{worktree}" in argv
    assert argv[argv.index("-w") + 1] == str(worktree)
    backlink = gitdir / "gitdir"
    assert f"{backlink}:{backlink}:ro" in argv


def test_docker_argv_gives_the_worker_a_home_of_its_own(tmp_path):
    """A uid with no passwd entry in the image gets HOME=/, where no agent CLI
    can write its state; codex and gemini then refuse to start at all."""
    home = tmp_path / "home"
    argv = docker.docker_argv(["claude"], "/w", {"kind": "docker", "image": "x"}, None, home=home)
    assert f"{home}:{home}" in argv
    assert argv[argv.index(f"HOME={home}") - 1] == "-e"


def test_docker_argv_forwards_passthrough_names_bare():
    """A repo's `env_passthrough` is how a worker gets a credential; forwarded
    by name, its value never lands on the argv (`ps`, `docker inspect`)."""
    argv = docker.docker_argv(
        ["codex"], "/w", {"kind": "docker", "image": "x"}, None, passthrough=["OPENAI_API_KEY"]
    )
    assert argv[argv.index("OPENAI_API_KEY") - 1] == "-e"
    assert not [a for a in argv if a.startswith("OPENAI_API_KEY=")]


def test_a_name_given_both_bare_and_literal_crosses_once_as_the_literal():
    """Two `-e` for one name leave the winner to the runtime; the literal (the
    relay's proxy, a CA path, a pin) is the one that must hold."""
    argv = docker.docker_argv(
        ["codex"],
        "/w",
        {"kind": "docker", "image": "x"},
        None,
        env={"HTTPS_PROXY": "http://127.0.0.1:3128"},
        passthrough=["HTTPS_PROXY"],
    )
    assert [a for a in argv if a.startswith("HTTPS_PROXY")] == ["HTTPS_PROXY=http://127.0.0.1:3128"]


def test_docker_argv_pins_hooks_off_for_git_in_the_container():
    """A hook manager's hook points at a host interpreter, so it fails every
    commit in the container; and a hook is the worker's code anyway."""
    argv = docker.docker_argv(["git"], "/w", {"kind": "docker", "image": "x"}, None)
    env = dict(a.split("=", 1) for a in argv if a.startswith("GIT_CONFIG_"))
    pinned = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert pinned["core.hooksPath"] == os.devnull


def test_docker_argv_mounts_extra_paths_read_only_at_the_same_path():
    argv = docker.docker_argv(
        ["amp"], "/w", {"kind": "docker", "image": "x"}, None, ro_paths=["/k/rules.json"]
    )
    assert "/k/rules.json:/k/rules.json:ro" in argv


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
    assert await docker.sweep_orphans(keep={"kraft-live"}) == ["kraft-orphan"]
    assert fake_docker.read_text().split() == ["kraft-orphan"]


async def test_teardown_gives_up_on_a_daemon_that_never_answers(tmp_path, monkeypatch):
    """A wedged daemon hung `docker rm -f` forever, and teardown is awaited on
    every session's way out and for every row at startup."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexec sleep 30\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(docker, "DOCKER_CALL_TIMEOUT_S", 0.2)
    started = time.monotonic()
    await asyncio.wait_for(docker.teardown("s1"), 5)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "executable, missing", [("sh", False), ("kraft-no-such-cli", True)], ids=["present", "absent"]
)
async def test_an_image_without_the_command_is_told_apart(fake_docker, executable, missing):
    assert await docker.missing_executable("img", executable) is missing


async def test_an_inconclusive_image_check_never_blocks_a_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no docker at all
    assert await docker.missing_executable("img", "kraft-no-such-cli") is False


def test_docker_argv_keeps_objects_info_read_only(tmp_path):
    """`objects/info/alternates` names directories every later launch on the
    repository mounts: writable, it let a worker mount any host directory."""
    repo, worktree, gitdir = _worktree(tmp_path)
    info = repo / ".git" / "objects" / "info"
    info.mkdir()
    store = RefStore(tmp_path / "store", repo / ".git", gitdir, "kraft/x")
    argv = docker.docker_argv(
        ["git"], worktree, {"kind": "docker", "image": "x"}, None, refstore=store
    )
    assert f"{info}:{info}:ro" in argv


def test_docker_argv_keeps_another_gitdir_read_only(tmp_path):
    """A `.git` file naming a gitdir outside `worktrees/` (a submodule's
    checkout) has no ref store; the file and its gitdir stay read-only."""
    other = tmp_path / "super" / ".git" / "modules" / "sm"
    other.mkdir(parents=True)
    checkout = tmp_path / "sm"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {other}\n")
    argv = docker.docker_argv(["git"], checkout, {"kind": "docker", "image": "x"}, None)
    assert f"{checkout / '.git'}:{checkout / '.git'}:ro" in argv
    assert f"{other}:{other}:ro" in argv


async def test_the_image_check_asks_through_the_entrypoint_with_the_repo_env(tmp_path, monkeypatch):
    """A version manager's shim sets PATH in the image's entrypoint, and a
    repository may set it in `env`; a probe that skipped either stopped
    launches that would have worked."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv"
    (bin_dir / "docker").write_text(
        f'#!/bin/sh\necho "$@" > {log}\necho "$PATH" >> {log}\necho kraft-probe-yes\n'
    )
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert await docker.missing_executable("img", "claude", {"PATH": "/opt/bin"}) is False
    line, client_path = log.read_text().splitlines()
    argv = line.split()
    assert not [a for a in argv if a.startswith("--entrypoint")]
    # By name, like the launch: the value rides in the client's env.
    assert argv[argv.index("PATH") - 1] == "-e"
    assert "PATH=/opt/bin" not in argv
    assert client_path == "/opt/bin"


# --- runtimes: docker, podman, rootless, SELinux --------------------------------------


def _argv_on(monkeypatch, runtime, **kw):
    monkeypatch.setattr(docker, "_RUNTIME", runtime)
    return docker.docker_argv(["true"], "/w", {"kind": "docker", "image": "img"}, None, **kw)


@pytest.mark.parametrize(
    ("runtime", "user"),
    [
        (docker.Runtime("docker"), ["-u", "1234:5678"]),
        (docker.Runtime("docker", rootless=True), ["-u", "0:0"]),
        (docker.Runtime("podman"), ["-u", "1234:5678"]),
        (docker.Runtime("podman", rootless=True), ["--userns=keep-id", "-u", "1234:5678"]),
        (
            docker.Runtime("docker", engine="podman", rootless=True),
            ["--userns=keep-id", "-u", "1234:5678"],
        ),
    ],
    ids=["docker", "rootless-docker", "podman", "rootless-podman", "podman-docker-shim"],
)
def test_every_runtime_leaves_what_the_worker_writes_the_operators(monkeypatch, runtime, user):
    """Rootless docker maps container root to the operator and every other
    uid to a subordinate one; rootless podman has no operator uid inside at
    all without `keep-id`, so the worktree is not even writable. The
    operator is not root here, or root and "the operator" would read alike."""
    monkeypatch.setattr(os, "getuid", lambda: 1234)
    monkeypatch.setattr(os, "getgid", lambda: 5678)
    argv = _argv_on(monkeypatch, runtime)
    assert argv[0] == runtime.cli
    start = argv.index("--label") + 2
    assert argv[start : start + len(user)] == user


def test_selinux_relabel_shares_every_mount_and_never_privatises_one(monkeypatch, tmp_path):
    home = tmp_path / "home"
    argv = _argv_on(monkeypatch, docker.Runtime(selinux="relabel"), home=home, ro_paths=["/r"])
    specs = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert specs and all(s.endswith((":z", ",z")) for s in specs)
    assert "/r:/r:ro,z" in specs
    assert not any(s.endswith("Z") for s in specs)


def test_selinux_disable_turns_labels_off_for_the_container_only(monkeypatch):
    argv = _argv_on(monkeypatch, docker.Runtime(selinux="disable"))
    assert "--security-opt=label=disable" in argv
    assert not any(a.endswith(":z") for a in argv)


def test_an_enforcing_host_nobody_configured_refuses_the_launch(monkeypatch):
    """Both answers change something outside Kraft, so neither is chosen for
    the operator: the task stops and says which two there are."""
    with pytest.raises(docker.SandboxRefused, match="selinux: relabel"):
        _argv_on(monkeypatch, docker.Runtime(selinux="refuse"))


async def test_the_refusal_stops_a_session_before_it_starts(monkeypatch):
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(selinux="refuse"))
    reason = await docker.DockerBackend().probe({"kind": "docker", "image": "img"}, "x", None)
    assert reason is not None and "selinux: disable" in reason


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """`machine(yaml, enforcing=False, labels=True)`: this host's
    `sandbox.yaml`, whether SELinux enforces, and whether the runtime (a
    rootful docker) labels its containers."""
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))

    def go(yaml="", enforcing=False, labels=True):
        (templates / "sandbox.yaml").write_text(yaml)
        monkeypatch.setattr(docker, "_selinux_enforcing", lambda: enforcing)
        monkeypatch.setattr(docker, "_ask", lambda cli: ("docker", False, labels))
        monkeypatch.setattr(docker, "_ask_limits", lambda cli, engine: docker.LIMITS)
        return docker.detect_runtime()

    return go


@pytest.mark.parametrize(
    ("yaml", "enforcing", "selinux"),
    [
        ("", False, None),
        ("selinux: relabel", False, None),
        ("", True, "refuse"),
        ("selinux: relabel", True, "relabel"),
        ("selinux: disable", True, "disable"),
    ],
    ids=["off", "off-configured", "enforcing-auto", "enforcing-relabel", "enforcing-disable"],
)
def test_sandbox_yaml_decides_selinux_only_where_it_enforces(machine, yaml, enforcing, selinux):
    assert machine(yaml, enforcing).selinux == selinux


def test_a_runtime_that_labels_nothing_needs_no_selinux_answer(machine):
    """Docker applies no labels unless its daemon runs with SELinux support,
    and then its containers' mounts work on an enforcing host as they are."""
    assert machine("", enforcing=True, labels=False).selinux is None


def test_sandbox_yaml_picks_the_cli(machine):
    assert machine("cli: podman").cli == "podman"


def _cli(tmp_path, monkeypatch, name, version, info):
    """A `name` on PATH answering `--version` and `info` as given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text(
        f'#!/bin/sh\n[ "$1" = --version ] && {{ echo "{version}"; exit 0; }}\necho \'{info}\'\n'
    )
    (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("name", "version", "info", "answer"),
    [
        (
            "docker",
            "Docker version 29.3.1",
            '["name=seccomp,profile=builtin","name=rootless","name=selinux"]',
            ("docker", True, True),
        ),
        (
            "docker",
            "Docker version 29.3.1",
            '["name=seccomp,profile=builtin"]',
            ("docker", False, False),
        ),
        ("podman", "podman version 4.9.3", "true true", ("podman", True, True)),
        ("podman", "podman version 4.9.3", "false false", ("podman", False, False)),
        ("docker", "podman version 4.9.3", "true false", ("podman", True, False)),
    ],
    ids=["rootless-docker", "docker", "rootless-podman", "podman", "podman-docker-shim"],
)
def test_the_runtime_says_what_it_is(tmp_path, monkeypatch, name, version, info, answer):
    """A `docker` that is podman underneath (the podman-docker shim) removes
    cidfiles and needs `keep-id` exactly as podman does."""
    _cli(tmp_path, monkeypatch, name, version, info)
    assert docker._ask(name) == answer


@pytest.mark.parametrize(
    ("rc", "failed"), [(125, True), (3, False), (127, False)], ids=["podman", "task", "no-cli"]
)
def test_podman_launch_failures_are_told_apart_by_exit_code(monkeypatch, tmp_path, rc, failed):
    """Podman deletes the cidfile of a `--rm` container on its way out, so
    a session whose task failed would read as podman never starting it."""
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime("docker", engine="podman"))
    assert docker.launch_failed(tmp_path / "gone.cid", rc) is failed


async def test_doctor_names_the_selinux_choice_before_anything_else(machine):
    machine("", enforcing=True)
    ok, detail = await docker.DockerBackend().health({"kind": "docker", "image": "img"})
    assert not ok and "selinux: relabel" in detail


async def test_a_sandbox_yaml_that_does_not_parse_stops_the_session(machine, monkeypatch):
    with pytest.raises(ConfigError):
        machine("cli: lxc")
    monkeypatch.setattr(docker, "_RUNTIME", None)
    reason = await docker.DockerBackend().probe({"kind": "docker", "image": "img"}, "x", None)
    assert reason is not None and "sandbox.yaml" in reason
