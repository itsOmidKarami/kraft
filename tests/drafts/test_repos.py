"""The `repos` draft area's ops and its resolve (W13 E): per-field sources,
the relax check, the path and chain checks, and the running-item guard."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import connect_repo

from kraft import store
from kraft.paths import RunDirs

pytestmark = pytest.mark.api_client(default_setup=False)

URL = "/api/drafts/repos/repos"


def ops(client, *batch):
    return client.post(f"{URL}/ops", json={"ops": list(batch)})


def resolved(client, *batch):
    r = ops(client, *batch)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def view(body, path, section="repos"):
    return next(v for v in body["resolved"][section] if v["path"] == path)


def messages(body):
    return [p["message"] for p in body["problems"]]


@pytest.fixture
def connected(client, repo, templates_dir):
    connect_repo(repo, templates_dir, name="r", enabled=False, managed=True)
    return str(repo.resolve())


def seed_item(client, path):
    conn = sqlite3.connect(RunDirs(Path(os.environ["KRAFT_RUN_DIR"])).db)
    try:
        store.create_work_item(
            conn,
            id="w-run",
            bead_id="B-1",
            title="t",
            repo=path,
            chain_template="quick-task",
            chain_definition="{}",
            status="paused",
        )
        conn.commit()
    finally:
        conn.close()


def test_steering_is_the_repos_own_else_the_librarys(client, connected):
    body = resolved(client, {"op": "set_repo", "path": connected, "patch": {"steering": []}})
    own = view(body, connected)
    assert own["sources"]["steering"] == "library"
    assert "project-standards" in own["resolved"]["steering"]

    body = resolved(
        client,
        {
            "op": "set_repo",
            "path": connected,
            "patch": {"steering": ["never-signal-processes-you-didnt-start"]},
        },
    )
    own = view(body, connected)
    assert own["sources"]["steering"] == "repo"
    assert own["resolved"]["steering"] == ["never-signal-processes-you-didnt-start"]


@pytest.fixture
def capped(templates_dir):
    path = templates_dir / "policy.yaml"
    policy = yaml.safe_load(path.read_text())
    policy["maxima"] = {"max_attempts": 3}
    path.write_text(yaml.safe_dump(policy))


def test_a_repo_policy_that_raises_a_maximum_is_a_problem_naming_the_field(
    capped, client, connected
):
    """Mutate: skip the `deps._layered` call in `_view` and this passes with no problem."""
    body = resolved(
        client,
        {"op": "set_repo", "path": connected, "patch": {"policy": {"max_attempts": 4}}},
    )
    (problem,) = [p for p in body["problems"] if p["repo"] == connected]
    assert problem["field"] == "max_attempts"
    assert connected in problem["message"]
    assert client.post(f"{URL}/publish").status_code == 422


def test_a_policy_inside_the_maximum_shows_its_source(client, connected):
    body = resolved(
        client, {"op": "set_repo", "path": connected, "patch": {"policy": {"max_attempts": 1}}}
    )
    own = view(body, connected)
    assert own["sources"]["policy"]["max_attempts"] == "repo"
    assert own["resolved"]["policy"]["max_attempts"] == 1
    assert own["sources"]["policy"]["timeout_minutes"] == "default"
    assert not [p for p in body["problems"] if p["repo"] == connected]


def test_add_repo_of_a_path_that_is_not_a_git_repo_is_a_problem(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    body = resolved(client, {"op": "add_repo", "path": str(plain)})
    assert any("not a git repository" in m for m in messages(body))
    body = resolved(client, {"op": "add_repo", "path": str(tmp_path / "missing")})
    assert any("not a directory" in m for m in messages(body))


def test_add_repo_of_a_repo_with_no_commit_is_refused(client, tmp_path):
    """As `POST /repos` refuses it: its items would run on an empty orphan branch."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fresh, check=True)
    r = ops(client, {"op": "add_repo", "path": str(fresh)})
    assert r.status_code == 422
    assert "has no commit yet" in r.text


def test_a_default_chain_template_naming_no_chain_is_a_problem(client, connected):
    body = resolved(
        client,
        {"op": "set_repo", "path": connected, "patch": {"default_chain_template": "nope"}},
    )
    (problem,) = [p for p in body["problems"] if p["field"] == "default_chain_template"]
    assert "'nope'" in problem["message"]


def test_connect_detected_sets_managed(client, repo, templates_dir):
    connect_repo(repo, templates_dir, managed=False, enabled=False)
    path = str(repo.resolve())
    detected = resolved(client)
    assert view(detected, path, "detected")["managed"] is False
    assert detected["resolved"]["repos"] == []
    body = resolved(client, {"op": "connect_detected", "path": path})
    assert view(body, path)["managed"] is True
    assert body["resolved"]["detected"] == []
    assert ops(client, {"op": "connect_detected", "path": path}).status_code == 422


def test_set_repo_is_a_touch_like_patch_repos(client, repo, templates_dir):
    connect_repo(repo, templates_dir, managed=False, enabled=False)
    path = str(repo.resolve())
    body = resolved(client, {"op": "set_repo", "path": path, "patch": {"name": "x"}})
    assert view(body, path)["managed"] is True


def test_remove_repo_with_a_running_item_is_a_problem_and_blocks_publish(client, connected):
    """Mutate: have `open_counts_by_repo` count only `status = 'active'`, or only managed
    repos, and the paused item below is not counted."""
    seed_item(client, connected)
    body = resolved(client, {"op": "remove_repo", "path": connected})
    assert body["impact"]["running"] == {connected: 1}
    assert any("open item(s); finish or cancel them first" in m for m in messages(body))
    assert [(c["path"], c["kind"]) for c in body["changes"]] == [(connected, "remove")]
    assert client.post(f"{URL}/publish").status_code == 422


def test_one_field_on_one_repo_is_one_change(client, connected):
    """The file-level `repos.yaml · repos` row repeated the entry's own: ticking
    No setup needed, or setting a setup command, read "DRAFT · 2 CHANGES" (R8b-05). Mutate: keep the
    file-level row beside the entry's, and this reads two."""
    body = resolved(
        client, {"op": "set_repo", "path": connected, "patch": {"setup_command": "make deps"}}
    )
    assert [(c["path"], c["kind"], c["summary"]) for c in body["changes"]] == [
        (connected, "change", "setup_command")
    ]


def test_remove_repo_with_no_running_item_publishes(client, connected, templates_dir):
    assert resolved(client, {"op": "remove_repo", "path": connected})["problems"] == []
    assert client.post(f"{URL}/publish").status_code == 200
    assert yaml.safe_load((templates_dir / "repos.yaml").read_text())["repos"] == []
