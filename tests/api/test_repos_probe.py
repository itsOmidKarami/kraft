"""`POST /repos/probe` and the probe behind `POST /repos`: the evidence they
tell, when they read detectors.yaml, and that they never block the server."""

from __future__ import annotations

import subprocess

import pytest
import yaml
from support.harness import commit_all, make_repo, make_repo_with_submodule
from support.probe import JEST


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


@pytest.mark.parametrize(
    ("lockfile", "expected"), [(True, "uv sync"), (False, None)], ids=["uv-lock", "no-uv-lock"]
)
@pytest.mark.parametrize("route", ["/api/repos/probe", "/api/repos"], ids=["probe", "add"])
def test_add_repo_writes_the_probed_setup_command(tmp_path, client, route, lockfile, expected):
    """The first-run and Templates › Repos probe proposes what connecting
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
    for rel, text in {"api/go.mod": "module a\n", "web/package.json": JEST}.items():
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
