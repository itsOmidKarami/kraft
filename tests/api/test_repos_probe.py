"""`POST /repos/probe` and the probe behind `POST /repos`: the evidence they
tell, when they read detectors.yaml, and that they never block the server."""

from __future__ import annotations

import pytest
import yaml
from support.harness import commit_all, make_repo


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
