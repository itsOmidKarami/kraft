"""Chain templates (design 5b) and the registry that validates them (5c)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
import yaml
from support.api_settings import _client
from support.harness import fake_templates_dir, make_repo

_FAKE_AGENT = Path(__file__).resolve().parents[0] / "support" / "fake_agent.py"


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, "claude")


@pytest.fixture
def client(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir) as c:
        yield c


NODES = [
    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
    {"id": "verify", "tasks": ["on.test.run"], "gate_after": None, "fix_loop": "verify_fix_loop"},
]


def test_template_put_validates_before_it_writes(client, templates_dir):
    bad = [{"id": "x", "tasks": ["on.does.not.exist"], "gate_after": None}]
    r = client.post("/api/templates/scratch/validate", json={"nodes": bad})
    assert r.json()["valid"] is False
    assert "not in the registry" in r.json()["error"]

    assert client.put("/api/templates/scratch", json={"nodes": bad}).status_code == 422
    assert not (templates_dir / "scratch.yaml").exists()

    assert client.put("/api/templates/scratch", json={"nodes": NODES}).status_code == 200
    assert yaml.safe_load((templates_dir / "scratch.yaml").read_text())["id"] == "scratch"
    # the new template is live without a restart
    assert any(t["id"] == "scratch" for t in client.get("/api/templates").json())
    # GET echoes the nodes as written -- no derived `steps` key.
    assert client.get("/api/templates/scratch").json()["nodes"] == NODES
    assert client.get("/api/templates/nope").status_code == 404


def test_reload_picks_up_a_template_added_on_disk_without_a_restart(client, templates_dir):
    assert not any(t["id"] == "hand-edited" for t in client.get("/api/templates").json())
    (templates_dir / "hand-edited.yaml").write_text(
        yaml.safe_dump({"id": "hand-edited", "nodes": NODES})
    )

    r = client.post("/api/templates/reload")
    assert r.status_code == 200
    assert "hand-edited" in r.json()["valid"]
    assert r.json()["invalid_templates"] == {}
    assert any(t["id"] == "hand-edited" for t in client.get("/api/templates").json())


def test_reload_with_a_broken_registry_keeps_the_last_good_config(client, templates_dir):
    good = (templates_dir / "registry.yaml").read_text()
    (templates_dir / "registry.yaml").write_text(
        yaml.safe_dump({"hooks": {"on.x": {"kind": "nope"}}})
    )

    r = client.post("/api/templates/reload")
    assert r.status_code == 422

    # the running server kept serving the last good config, not the broken file
    assert client.get("/api/registry").json()["hooks"] == yaml.safe_load(good)["hooks"]
    valid_ids = {t["id"] for t in client.get("/api/templates").json()}
    assert {"quick-task", "default"} <= valid_ids


def test_an_unknown_gate_is_refused(client):
    nodes = [{"id": "a", "tasks": ["on.test.run"], "gate_after": "made_up_gate"}]
    r = client.post("/api/templates/scratch/validate", json={"nodes": nodes})
    assert r.json()["valid"] is False and "gate_after" in r.json()["error"]


def test_validate_names_the_node_and_task_that_do_not_resolve(client):
    """Kraft-3e6e. The old `by_repo.resolvable` was
    `all(h in st.registry.hooks for h in hooks)` -- the same bit repeated once
    per connected repo, computed without consulting the repo at all -- and
    "unresolvable" named a repo, when the human editing a chain needs the node.
    """
    nodes = [
        {"id": "measure", "tasks": ["on.test.run"], "gate_after": None},
        {"id": "x", "tasks": ["on.does.not.exist"], "gate_after": None},
    ]
    body = client.post("/api/templates/scratch/validate", json={"nodes": nodes}).json()
    assert body["unresolved"] == [{"node": "x", "task": "on.does.not.exist"}]
    assert "by_repo" not in body

    ok = client.post(
        "/api/templates/scratch/validate",
        json={"nodes": [{"id": "measure", "tasks": ["on.test.run"], "gate_after": None}]},
    ).json()
    assert ok["valid"] is True
    assert ok["unresolved"] == []


def test_registry_save_reruns_the_chain_validator(client, templates_dir):
    hooks = client.get("/api/registry").json()["hooks"]
    assert "on.test.run" in hooks

    broken = {k: v for k, v in hooks.items() if k != "on.test.run"}
    body = client.put("/api/registry", json={"hooks": broken})
    assert body.status_code == 200
    # quick-task measures with on.test.run, so dropping the binding breaks it
    assert "quick-task" in body.json()["invalid_templates"]
    assert client.get("/api/health").json()["status"] == "degraded"

    assert (
        client.put("/api/registry", json={"hooks": {"on.x": {"kind": "nope"}}}).status_code == 422
    )
    # the refused save left the file alone
    assert "on.x" not in yaml.safe_load((templates_dir / "registry.yaml").read_text())["hooks"]


def test_registry_carries_the_interactive_flag(client):
    hooks = client.get("/api/registry").json()["hooks"]
    hooks["on.implementation.start"]["interactive"] = True
    assert client.put("/api/registry", json={"hooks": hooks}).status_code == 200
    assert (
        client.get("/api/registry").json()["hooks"]["on.implementation.start"]["interactive"]
        is True
    )


def test_put_registry_validates_steering_against_the_real_templates_dir(client, templates_dir):
    """Regression guard: `put_registry` validates the candidate against
    `st.templates_dir / "steering"`, not the empty scratch dir it writes the
    candidate registry into — that would 422 every save naming a real file."""
    (templates_dir / "steering").mkdir(exist_ok=True)
    (templates_dir / "steering" / "house-style.md").write_text("# House style\nBe direct.\n")
    hooks = client.get("/api/registry").json()["hooks"]
    hooks["on.implementation.start"]["steering"] = ["house-style"]
    r = client.put("/api/registry", json={"hooks": hooks})
    assert r.status_code == 200, r.text
    assert client.get("/api/registry").json()["hooks"]["on.implementation.start"]["steering"] == [
        "house-style"
    ]


def test_get_put_registry_round_trip_is_byte_identical(client, templates_dir):
    before = (templates_dir / "registry.yaml").read_text()
    hooks = client.get("/api/registry").json()["hooks"]
    assert client.put("/api/registry", json={"hooks": hooks}).status_code == 200
    assert (templates_dir / "registry.yaml").read_text() == before


def test_put_registry_preserves_the_defaults_block(client, templates_dir):
    registry_path = templates_dir / "registry.yaml"
    data = yaml.safe_load(registry_path.read_text())
    data["defaults"] = {"agent": {"steering": ["never-signal-processes-you-didnt-start"]}}
    registry_path.write_text(yaml.safe_dump(data))
    assert client.post("/api/templates/reload").status_code == 200

    hooks = client.get("/api/registry").json()["hooks"]
    assert client.put("/api/registry", json={"hooks": hooks}).status_code == 200

    after = yaml.safe_load(registry_path.read_text())
    assert after["defaults"] == {"agent": {"steering": ["never-signal-processes-you-didnt-start"]}}


def test_templates_lists_only_resolvable_sorted(tmp_path, monkeypatch):
    bad = fake_templates_dir(tmp_path, "claude")
    (bad / "broken.yaml").write_text(
        "id: broken\nnodes:\n  - {id: x, tasks: [on.nope], gate_after: null}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        got = client.get("/api/templates").json()
        ids = [t["id"] for t in got]
        assert "broken" not in ids
        assert ids == sorted(ids)
        assert "quick-task" in ids


def test_parse_template_yaml_round_trips_default(client):
    text = (Path(__file__).resolve().parents[1] / "templates" / "default.yaml").read_text()
    body = client.post("/api/templates/parse", json={"text": text}).json()
    assert body["error"] is None
    assert body["nodes"][0]["id"] == "spec"
    assert len(body["nodes"]) == 11


def test_parse_template_yaml_reports_a_syntax_error(client):
    body = client.post("/api/templates/parse", json={"text": "nodes: [unterminated"}).json()
    assert body["nodes"] is None
    assert body["error"]


def test_parse_template_yaml_reports_a_shape_error(client):
    body = client.post("/api/templates/parse", json={"text": "id: x\n"}).json()
    assert body["nodes"] is None
    assert "nodes" in body["error"]


def test_hook_runs_lists_recent_sessions_for_the_hook(tmp_path, client, templates_dir):
    repo = make_repo(tmp_path)
    client.post("/api/repos", json={"path": str(repo), "test_command": "pytest"})
    wid = client.post(
        "/api/work-items",
        json={"title": "x", "repo": str(repo), "chain_template": "quick-task", "autostart": False},
    ).json()["id"]

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
            "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at, "
            "round, head_sha) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'done', 1, ?, NULL, ?, NULL)",
            (
                "s1",
                wid,
                "implementation",
                "on.test.run",
                "/tmp/a",
                "/tmp/a.json",
                "2026-01-01T00:00:00Z",
                0,
            ),
        )
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
            "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at, "
            "round, head_sha) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'done', 1, ?, NULL, ?, NULL)",
            (
                "s2",
                wid,
                "verify",
                "on.test.run",
                "/tmp/b",
                "/tmp/b.json",
                "2026-01-02T00:00:00Z",
                2,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    runs = client.get("/api/registry/on.test.run/runs").json()["runs"]
    assert runs[0]["node_id"] == "verify"  # newest first
    assert len(runs) == 2


@pytest.fixture
def shapes(client, templates_dir):
    authored = [
        {"id": "env_setup", "tasks": ["on.env.prepare"]},
        {"id": "verify", "steps": [["on.test.run"]]},
    ]
    (templates_dir / "shapes.yaml").write_text(
        yaml.safe_dump({"id": "shapes", "nodes": authored}, sort_keys=False)
    )
    assert client.post("/api/templates/reload").status_code == 200
    return authored


def test_get_template_returns_the_authored_nodes_not_the_normalized_ones(client, shapes):
    """The Templates page rendered a node carrying BOTH tasks and steps -- a
    shape the chain-review skill and docsite both call illegal and no operator
    wrote -- because GET returned the normalized nodes. PUT writes what it is
    handed straight back to disk, so one save rewrote the operator's file."""
    by_id = {n["id"]: n for n in client.get("/api/templates/shapes").json()["nodes"]}
    assert "steps" not in by_id["env_setup"], "a tasks-only node must round-trip as tasks-only"
    assert "tasks" not in by_id["verify"], "a steps-only node must round-trip as steps-only"


def test_a_template_survives_a_get_then_put_round_trip(client, templates_dir, shapes):
    """Open the page, save: an untouched round trip must be a no-op."""
    nodes = client.get("/api/templates/shapes").json()["nodes"]
    assert client.put("/api/templates/shapes", json={"nodes": nodes}).status_code == 200
    after = yaml.safe_load((templates_dir / "shapes.yaml").read_text())
    assert after["nodes"] == shapes
