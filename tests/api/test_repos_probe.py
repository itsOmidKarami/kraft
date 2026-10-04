"""`POST /repos/probe` and the probe behind `POST /repos`: the evidence they
tell, when they read detectors.yaml, and that they never block the server;
and the checkout `GET /repos` suggests connecting."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import commit_all, make_repo, make_repo_with_submodule
from support.probe import JEST

from kraft import config as config_mod


#: Reads real config: no default repo entry stands in for the one under test.
@pytest.mark.api_client(default_setup=False)
@pytest.mark.parametrize(
    "cwd, connect, suggested",
    [
        pytest.param("sample", False, "sample", id="a checkout is suggested"),
        pytest.param("sample/src", False, "sample", id="from inside it, its top"),
        pytest.param("plain", False, None, id="outside a checkout, nothing"),
        pytest.param("sample", True, None, id="already connected, nothing"),
    ],
)
def test_lists_the_servers_own_checkout_as_the_suggested_repo(
    tmp_path, client, monkeypatch, cwd, connect, suggested
):
    """First-run offers the git checkout the server was started in (BD-2)."""
    repo = make_repo(tmp_path)
    (repo / "src").mkdir(exist_ok=True)
    (tmp_path / "plain").mkdir()
    if connect:
        assert client.post("/api/repos", json={"path": str(repo)}).status_code == 201
    monkeypatch.chdir(tmp_path / cwd)
    toplevel_reads = []
    real = config_mod.git_read

    def counting(cwd, *args, **kw):
        if "--show-toplevel" in args:
            toplevel_reads.append(args)
        return real(cwd, *args, **kw)

    monkeypatch.setattr(config_mod, "git_read", counting)
    got = client.get("/api/repos").json()["suggested"]
    assert client.get("/api/repos").json()["suggested"] == got
    assert (got and Path(got).resolve()) == (suggested and (tmp_path / suggested).resolve())
    # About ten places in the UI read /repos: git is asked about the checkout once.
    assert len(toplevel_reads) == 1


@pytest.mark.api_client(default_setup=False)
def test_listing_repos_survives_the_servers_directory_being_deleted(tmp_path, client, monkeypatch):
    """A throwaway checkout the server was started in gets removed: `Path.cwd()`
    raises, and the listing answered 500 (R14e-02)."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()

    r = client.get("/api/repos")

    assert r.status_code == 200, r.text
    assert r.json()["suggested"] is None


@pytest.mark.api_client(default_setup=False)
def test_a_git_init_in_the_servers_directory_is_suggested_after_the_cache_expires(
    tmp_path, client, monkeypatch
):
    """The answer is held a few seconds, not for the life of the process
    (R14e-04): `git init` after the first read shows up without a restart."""
    from kraft.api.routes import repos

    here = tmp_path / "later"
    here.mkdir()
    monkeypatch.chdir(here)
    now = [1000.0]
    monkeypatch.setattr(repos.time, "monotonic", lambda: now[0])
    assert client.get("/api/repos").json()["suggested"] is None

    subprocess.run(["git", "init", "-q", str(here)], check=True)
    assert client.get("/api/repos").json()["suggested"] is None  # still held
    now[0] += repos.CHECKOUT_TTL_S + 1

    assert Path(client.get("/api/repos").json()["suggested"]).resolve() == here.resolve()


def test_a_repo_that_declares_no_tests_connects_enabled(tmp_path, client, templates_dir):
    """`test_command: ""` is the decision "this repo has no tests", not an
    absence: it is stored as written and the repo connects enabled."""
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "test_command": ""})
    assert r.status_code == 201, r.text
    assert (r.json()["test_command"], r.json()["enabled"]) == ("", True)
    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())["repos"][0]
    assert on_disk["test_command"] == ""


def test_add_repo_tells_the_candidates_and_does_not_store_them(tmp_path, client, templates_dir):
    repo = make_repo(tmp_path)
    (repo / "Makefile").write_text("test:\n\tctest\n")
    (repo / "go.mod").write_text("module x\n")
    (repo / "x_test.go").write_text("")
    commit_all(repo)
    body = client.post("/api/repos", json={"path": str(repo), "enabled": False}).json()
    assert body["test_command"] == "make test"
    assert ("go test ./...", False) in [(c["command"], c["chosen"]) for c in body["candidates"]]
    assert body["scopes"] == [{"dir": "", "test": "make test", "setup": "go mod download"}]
    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())["repos"][0]
    assert not {"candidates", "scopes", "missing_setup"} & set(on_disk)


def test_probe_reads_the_instances_own_detectors_file(tmp_path, client, templates_dir):
    (templates_dir / "detectors.yaml").write_text(
        "detectors:\n  - id: earthly\n    tier: runner\n    files: [Earthfile]\n"
        "    test: [{run: earthly +test}]\n"
    )
    repo = make_repo(tmp_path)
    (repo / "Earthfile").write_text("")
    commit_all(repo)
    body = client.post("/api/repos/probe", json={"path": str(repo)}).json()
    assert body["test_command"] == "earthly +test"


def test_a_broken_detectors_file_fails_the_probe_naming_it(tmp_path, client, templates_dir):
    (templates_dir / "detectors.yaml").write_text("detectorz: []\n")
    r = client.post("/api/repos/probe", json={"path": str(make_repo(tmp_path))})
    assert r.status_code == 400
    assert "detectors.yaml" in r.json()["detail"]


def test_a_connected_repo_answers_409_before_any_detection(tmp_path, client, templates_dir):
    """A reconnect (`ensure_repo` on every handoff) neither pays for the probe
    nor fails on an operator's broken detectors.yaml."""
    repo = make_repo(tmp_path)
    assert client.post("/api/repos", json={"path": str(repo), "enabled": False}).status_code == 201
    (templates_dir / "detectors.yaml").write_text("detectorz: []\n")
    r = client.post("/api/repos", json={"path": str(repo)})
    assert r.status_code == 409, r.text
    facts = client.post("/api/repos/probe", json={"path": str(repo), "detect": False}).json()
    assert facts["path"] == str(repo.resolve())
    assert "test_command" not in facts


def test_the_probe_runs_off_the_event_loop(tmp_path, client, monkeypatch):
    from kraft.api.routes import repos as routes

    ran = []
    real = routes.asyncio.to_thread

    async def spy(fn, *args, **kwargs):
        ran.append((fn.__name__, kwargs.get("detect", True)))
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", spy)
    repo = make_repo(tmp_path)
    client.post("/api/repos/probe", json={"path": str(repo)})
    client.post("/api/repos", json={"path": str(repo), "enabled": False})
    assert ("probe_repo", True) in ran
    assert ran.count(("probe_repo", True)) == 2, ran
    # "Already connected" too: it reads the working copy's .gitmodules and
    # beads config, which a FIFO or a YAML bomb would hold the loop on.
    assert ("probe_repo", False) in ran, ran


_SUBMODULE = '[submodule "x"]\n\tpath = libs/x\n\turl = ../x\n'


@pytest.mark.parametrize(
    "crafted",
    [
        # A quadratic backtrack in Python's INI option regex, holding the GIL.
        _SUBMODULE + "a" + " " * 30_000 + "b\n",
        # Python's INI interpolation: 400 bytes that expand to 10 MB, slowly.
        _SUBMODULE.replace("libs/x", "%(v0)s" * 6)
        + "".join(f"\tv{i} = {f'%(v{i + 1})s' * 6}\n" for i in range(8))
        + "\tv8 = x\n",
    ],
    ids=["long-run-of-spaces", "interpolation-bomb"],
)
def test_a_crafted_gitmodules_does_not_stop_the_server_answering(tmp_path, client, crafted):
    """`.gitmodules` is the repository's to write, and the "already connected"
    check reads it on every connect and every `ensure_repo`. Python's INI
    parser took seconds to hours on a crafted one, and on a long run of
    spaces held the GIL throughout: in its thread or not, `/api/health`
    waited for it."""
    import threading
    import time

    repo = make_repo(tmp_path)
    (repo / ".gitmodules").write_text(crafted)
    done = threading.Event()
    probe = threading.Thread(
        target=lambda: (
            client.post("/api/repos/probe", json={"path": str(repo), "detect": False}),
            done.set(),
        )
    )
    began = time.monotonic()
    probe.start()
    slowest = 0.0
    while not done.is_set():
        started = time.monotonic()
        assert client.get("/api/health").status_code == 200
        slowest = max(slowest, time.monotonic() - started)
    probe.join()
    assert slowest < 1.0, f"/api/health took {slowest:.1f}s while the probe read .gitmodules"
    assert time.monotonic() - began < 5.0, "reading .gitmodules took seconds"


def test_submodules_are_the_paths_git_reads_from_gitmodules(tmp_path):
    """git's own reading, quoting and all; a file git cannot parse lists none."""
    from kraft import config

    repo = make_repo(tmp_path)
    (repo / ".gitmodules").write_text(
        '[submodule "b"]\n\tpath = libs/b\n[submodule "a"]\n\tPath = "with space"\n'
        '[submodule "c"]\n\turl = ../c\n[other "d"]\n\tpath = not/a/submodule\n'
    )
    assert config.probe_repo(repo, detect=False)["submodules"] == ["libs/b", "with space"]
    (repo / ".gitmodules").write_text('[submodule "b"]\n\tpath = libs/b\nnot config\n')
    assert config.probe_repo(repo, detect=False)["submodules"] == []


@pytest.mark.parametrize("target", ["file", "fifo"])
def test_a_gitmodules_include_is_never_followed(tmp_path, target):
    """git follows `[include]` in a config it reads from stdin: the
    repository could have the server open any path, and a FIFO held the
    probe until git's timeout."""
    import os
    import time

    from kraft import config

    included = tmp_path / "included"
    if target == "fifo":
        os.mkfifo(included)
    else:
        included.write_text('[submodule "b"]\n\tpath = included-path\n')
    repo = make_repo(tmp_path)
    (repo / ".gitmodules").write_text(
        f'[include]\n\tpath = {included}\n[includeIf "gitdir:/"]\n\tpath = {included}\n'
        '[submodule "a"]\n\tpath = a\n'
    )
    started = time.monotonic()
    assert config.probe_repo(repo, detect=False)["submodules"] == ["a"]
    assert time.monotonic() - started < 5.0


def test_a_probe_that_fails_still_connects_a_repo_given_both_commands(
    tmp_path, client, monkeypatch
):
    """The error said to pass --test-command and --setup-command, to someone
    who had: with both given, the proposal they replace is not needed."""
    from kraft import detect

    monkeypatch.setattr(detect, "_PROBE_TIMEOUT_S", 0.001)
    repo = make_repo(tmp_path)
    r = client.post("/api/repos", json={"path": str(repo), "test_command": "make check"})
    assert r.status_code == 400, r.text
    both = {"path": str(repo), "test_command": "make check", "setup_command": ""}
    r = client.post("/api/repos", json=both)
    assert r.status_code == 201, r.text
    assert (r.json()["test_command"], r.json()["setup_command"]) == ("make check", "")
    assert "took more than" in r.json()["probe_failed"]


def test_a_probe_past_the_few_at_once_is_told_to_wait(tmp_path, client, monkeypatch):
    """Each probe is a process of up to 2 GB for up to two minutes."""
    import asyncio

    monkeypatch.setattr(client.app.state, "probing", asyncio.Semaphore(0), raising=False)
    repo = make_repo(tmp_path)
    assert client.post("/api/repos/probe", json={"path": str(repo)}).status_code == 429
    quick = client.post("/api/repos/probe", json={"path": str(repo), "detect": False})
    assert quick.status_code == 200, "what needs no detector table is never queued"


def test_a_gitmodules_path_at_or_above_the_parent_or_twice_is_not_probed(tmp_path, monkeypatch):
    """`path = .` probed the parent again, four times over, holding the
    connect lock; `..` probed outside it."""
    from kraft.api.routes import repos as routes

    probed = []
    monkeypatch.setattr(
        routes.config_mod, "probe_repo", lambda where, **kw: probed.append(where) or {}
    )
    parent = tmp_path / "parent"
    (parent / "libs" / "x").mkdir(parents=True)
    paths = [".", "./", "..", "../other", "libs/x", "libs/../libs/x", "libs/x/"]
    assert list(routes._probe_children(str(parent), paths, tmp_path)) == ["libs/x"]
    assert probed == [(parent / "libs" / "x").resolve()]


def test_submodules_share_one_time_budget(tmp_path, monkeypatch):
    from kraft.api.routes import repos as routes

    given = []
    monkeypatch.setattr(routes, "_CHILDREN_BUDGET_S", 0.0)
    monkeypatch.setattr(routes.config_mod, "probe_repo", lambda w, **kw: given.append(kw) or {})
    (tmp_path / "a").mkdir()
    assert routes._probe_children(str(tmp_path), ["a"], tmp_path) == {}
    assert given == []


@pytest.mark.parametrize(
    ("lockfile", "expected"), [(True, "uv sync"), (False, None)], ids=["uv-lock", "no-uv-lock"]
)
@pytest.mark.parametrize("route", ["/api/repos/probe", "/api/repos"], ids=["probe", "add"])
def test_add_repo_writes_the_probed_setup_command(tmp_path, client, route, lockfile, expected):
    """The first-run and Settings › Repos probe proposes what connecting
    writes: `uv sync` only beside a `uv.lock`, since it writes one otherwise."""
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    if lockfile:
        (repo / "uv.lock").write_text("version = 1\n")
    commit_all(repo)
    entry = client.post(route, json={"path": str(repo)}).json()
    assert entry["setup_command"] == expected


@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [(True, "uv run pytest"), (False, None)],
    ids=["uv-lock", "no-uv-lock"],
)
def test_a_pyproject_gets_uv_run_pytest_only_beside_a_uv_lock(tmp_path, client, lockfile, expected):
    """`uv run` writes a `uv.lock` when there is none, on every verify: the
    probe proposes no test command then, and the repo is added disabled."""
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n")
    if lockfile:
        (repo / "uv.lock").write_text("version = 1\n")
    commit_all(repo)
    assert (
        client.post("/api/repos/probe", json={"path": str(repo)}).json()["test_command"] == expected
    )
    entry = client.post("/api/repos", json={"path": str(repo)}).json()
    assert (entry["test_command"], entry["enabled"]) == (expected, lockfile)


def _workspace_with_tested_submodule(tmp_path):
    """A root with one submodule whose tests live in `api/` and `web/`. The
    files are committed to the submodule's origin and fetched: the probe
    reads origin's branch, as a work item's worktree is cut from it."""
    root, sub = make_repo_with_submodule(tmp_path)
    for rel, text in {
        "api/go.mod": "module a\n",
        "api/a_test.go": "",
        "web/package.json": JEST,
    }.items():
        (sub / rel).parent.mkdir(parents=True, exist_ok=True)
        (sub / rel).write_text(text)
    commit_all(sub)
    subprocess.run(["git", "-C", str(root / "repos" / "pkg"), "fetch", "-q", "origin"], check=True)
    return root


def test_an_auto_connected_submodule_keeps_every_nested_scope(tmp_path, client, templates_dir):
    root = _workspace_with_tested_submodule(tmp_path)
    r = client.post("/api/repos", json={"path": str(root), "enabled": False})
    assert r.status_code == 201, r.text
    on_disk = yaml.safe_load((templates_dir / "repos.yaml").read_text())["repos"]
    child = next(e for e in on_disk if e["path"].endswith("repos/pkg"))
    assert [s["paths"] for s in child["test_scopes"]] == [["api/**"], ["web/**"]]


def test_a_connect_awaits_nothing_between_reading_repos_yaml_and_saving_it(
    tmp_path, client, monkeypatch
):
    """PATCH and DELETE save without the connect lock: a connect that awaited
    after its read would save a stale list over theirs."""
    from kraft.api.routes import repos as routes

    order = []
    real_thread, real_read = routes.asyncio.to_thread, routes._editable_repos
    real_save = routes.config_mod.save_repos

    async def thread(fn, *args, **kwargs):
        order.append(f"thread:{fn.__name__}")
        return await real_thread(fn, *args, **kwargs)

    def read(*args, **kwargs):
        order.append("read")
        return real_read(*args, **kwargs)

    def save(*args, **kwargs):
        order.append("save")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", thread)
    monkeypatch.setattr(routes.config_mod, "save_repos", save)
    monkeypatch.setattr(routes, "_editable_repos", read)
    root = _workspace_with_tested_submodule(tmp_path)
    assert client.post("/api/repos", json={"path": str(root), "enabled": False}).status_code == 201
    assert "thread:_probe_children" in order
    last_read = len(order) - 1 - order[::-1].index("read")
    between = order[last_read : order.index("save")]
    assert not [o for o in between if o.startswith("thread:")], order
