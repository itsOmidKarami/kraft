"""Repository steering names library profiles, and is frozen at intake
(Kraft-91i6p): the one steering store, from the repository's side."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import yaml
from support.api import _client
from support.harness import fake_templates_dir, make_repo

_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"
_HOUSE = "HOUSE TEXT AT INTAKE"


def _templates(tmp_path, monkeypatch):
    templates = fake_templates_dir(tmp_path, f"{sys.executable} {_FAKE_AGENT}")
    library = templates / "library.yaml"
    library.write_text(
        library.read_text().replace(
            "steering:\n", f"steering:\n  house:\n    instructions: {_HOUSE}\n", 1
        )
    )
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    return templates, argv_log


def _connect(client, repo, steering=("house",)) -> str:
    added = client.post(
        "/api/repos",
        json={
            "path": str(repo),
            "steering": list(steering),
            "test_command": "pytest",
            "setup_command": "",
        },
    )
    assert added.status_code == 201, added.text
    return added.json()["path"]


def _file_paused(client, repo) -> str:
    r = client.post(
        "/api/work-items",
        json={"title": "x", "repo": str(repo), "chain_template": "quick-task", "autostart": False},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _snapshot(client, wid) -> dict:
    return json.loads(client.get(f"/api/work-items/{wid}").json()["materialized_chain"])


def _edit_library(client, old, new):
    text = client.get("/api/templates/library").json()["text"]
    saved = client.put("/api/templates/library", json={"text": text.replace(old, new)})
    assert saved.status_code == 200, saved.text


def _prompt_after_resume(client, wid, argv_log) -> str:
    assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 200
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not argv_log.exists():
        time.sleep(0.2)
    assert argv_log.exists(), f"agent never launched for {wid}"
    argv = json.loads(argv_log.read_text().splitlines()[0])
    return argv[argv.index("--append-system-prompt") + 1]


def test_repository_steering_is_frozen_into_the_snapshot_at_intake(tmp_path, monkeypatch):
    """`repository-steering-is-frozen-at-intake`: the text an item was filed
    with is the text its agent gets, after the library profile is edited."""
    templates, argv_log = _templates(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        path = _connect(client, repo)
        wid = _file_paused(client, repo)
        assert _snapshot(client, wid)["repository_steering"] == {path: {"house": _HOUSE}}

        _edit_library(client, _HOUSE, "EDITED AFTER INTAKE")
        prompt = _prompt_after_resume(client, wid, argv_log)

    assert _HOUSE in prompt
    assert "EDITED AFTER INTAKE" not in prompt


def test_a_snapshot_from_before_the_freeze_runs_on_the_live_library(tmp_path, monkeypatch):
    """An item filed on an rc install carries no repository steering: its
    launches read its repository's names against today's library, the way
    they read the steering files it was filed with."""
    templates, argv_log = _templates(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        _connect(client, repo)
        wid = _file_paused(client, repo)
        conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
        (raw,) = conn.execute(
            "SELECT materialized_chain FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        stored = json.loads(raw)
        del stored["repository_steering"]
        conn.execute(
            "UPDATE work_items SET materialized_chain = ? WHERE id = ?", (json.dumps(stored), wid)
        )
        conn.commit()
        conn.close()

        _edit_library(client, _HOUSE, "THE LIVE LIBRARY")
        prompt = _prompt_after_resume(client, wid, argv_log)

    assert "THE LIVE LIBRARY" in prompt


def test_intake_refuses_a_repository_naming_a_profile_the_library_lacks(tmp_path, monkeypatch):
    templates, _ = _templates(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        path = _connect(client, repo)
        # A hand edit: the repo save itself would have refused it.
        (templates / "repos.yaml").write_text(
            yaml.safe_dump(
                {"repos": [{"path": path, "steering": ["gone"], "test_command": "pytest"}]}
            )
        )
        r = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        )

    assert r.status_code == 422, r.text
    assert "'gone' is not a steering profile in templates/library.yaml" in r.json()["detail"]


def test_a_library_save_removing_a_profile_a_repository_names_is_refused(tmp_path, monkeypatch):
    templates, _ = _templates(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        path = _connect(client, repo)
        text = client.get("/api/templates/library").json()["text"]
        r = client.put(
            "/api/templates/library",
            json={"text": text.replace(f"  house:\n    instructions: {_HOUSE}\n", "")},
        )

    assert r.status_code == 422, r.text
    assert f"repos.yaml: {path}: steering 'house' is not" in r.json()["detail"]
    assert _HOUSE in (templates / "library.yaml").read_text()


def test_steering_files_become_library_profiles_at_startup(tmp_path, monkeypatch):
    """`pre-1-0-steering-files-become-library-profiles`: a home carried over
    from 0.x or an rc, with `templates/steering/house.md` and a repos.yaml
    naming it, starts with `house` as a library profile holding the file's
    text, the files kept aside, and an item filed there frozen with it."""
    templates, _ = _templates(tmp_path, monkeypatch)
    library = templates / "library.yaml"
    library.write_text(library.read_text().replace(f"  house:\n    instructions: {_HOUSE}\n", ""))
    (templates / "steering").mkdir()
    (templates / "steering" / "house.md").write_text("From the file.\n")
    repo = make_repo(tmp_path)
    (templates / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": str(repo.resolve()),
                        "steering": ["house"],
                        "test_command": "pytest",
                        "setup_command": "",
                    }
                ]
            }
        )
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        components = client.get("/api/templates/library").json()["components"]
        wid = _file_paused(client, repo.resolve())
        snapshot = _snapshot(client, wid)

    assert {c["id"]: c["definition"] for c in components}["steering.house"] == {
        "instructions": "From the file.\n"
    }
    assert not (templates / "steering").exists()
    assert (templates / "steering.pre-1.0" / "house.md").read_text() == "From the file.\n"
    assert snapshot["repository_steering"] == {str(repo.resolve()): {"house": "From the file.\n"}}
