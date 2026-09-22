"""Inspecting the V1 template library: lint, a saved chain resolved, and an
unsaved candidate or library resolved -- none of which writes or reloads
configuration (docs/templates-v1-design.md "Validation surface")."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from support.api import _client
from support.harness import fake_templates_dir

#: A chain an unsaved candidate or library can carry: one subprocess task.
SOLO = {
    "id": "solo",
    "nodes": [
        {
            "id": "run",
            "kind": "exec",
            "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
        }
    ],
}


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes: equal before and after means
    nothing was written, created or deleted."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def exec_node(id: str, **task) -> dict:
    return {"id": id, "kind": "exec", "tasks": [{"id": "t", **task}]}


# ── GET /templates/lint (template-lint-reports-library-validity) ──


def test_lint_of_the_installed_library_is_clean(client):
    body = client.get("/api/templates/lint").json()
    assert body["valid"] is True
    assert body["issues"] == []
    assert {"default", "quick-task"} <= set(body["chains"])


def test_lint_reports_all_library_errors_without_writing(client, templates_dir):
    """Every broken chain, each for its own reason -- a parse error, a
    reference that resolves to nothing, a duplicate identifier -- in one
    answer, not the first. The chains that do resolve are still listed."""
    chains = templates_dir / "chains"
    (chains / "garbled.yaml").write_text("nodes: [unclosed\n")
    (chains / "dangling.yaml").write_text(
        yaml.safe_dump({"id": "dangling", "nodes": [exec_node("n", extends="no_such_task")]})
    )
    twice = exec_node("n", kind="subprocess", command="true")
    (chains / "twice.yaml").write_text(yaml.safe_dump({"id": "twice", "nodes": [twice, twice]}))
    # A second file claiming a chain id `chains/default.yaml` already declares.
    (chains / "impostor.yaml").write_text(yaml.safe_dump({**SOLO, "id": "default"}))
    before = snapshot(templates_dir)

    response = client.get("/api/templates/lint")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    issues = {i["chain"]: i for i in body["issues"]}
    assert set(issues) == {"garbled", "dangling", "twice", "impostor"}
    assert "already declared by" in issues["impostor"]["message"]
    assert "cannot read/parse" in issues["garbled"]["message"]
    assert "no_such_task" in issues["dangling"]["message"]
    assert "duplicate" in issues["twice"]["message"]
    assert issues["garbled"]["file"] == str(chains / "garbled.yaml")
    assert {"default", "quick-task"} <= set(body["chains"])
    assert snapshot(templates_dir) == before
    # Not reloaded either: the daemon still runs the library it loaded, which
    # never had `dangling` in it. A reload would have met `garbled.yaml` and
    # left the daemon with no library at all (503).
    assert client.get("/api/templates/chains/dangling/resolved").status_code == 404


def test_lint_reports_an_unreadable_library_file_as_an_issue(client, templates_dir):
    (templates_dir / "library.yaml").write_text("tasks: [unclosed\n")
    body = client.get("/api/templates/lint").json()
    assert body["valid"] is False
    assert [i["file"] for i in body["issues"]] == [str(templates_dir / "library.yaml")]
    assert body["issues"][0]["chain"] is None


# ── GET /templates/chains/{id}/resolved (resolved-template-api-shows-saved-chain) ──


def test_resolved_shows_a_saved_chain_expanded_and_not_materialized(client):
    response = client.get("/api/templates/chains/default/resolved")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "default"
    spec = body["chain"]["nodes"][0]
    # Expanded: the library task's own fields are here and `extends` is gone.
    assert "extends" not in spec["tasks"][0]
    assert spec["tasks"][0]["skill"] == "kraft:spec"
    assert body["task_paths"][:2] == ["spec.main.author", "plan.main.author"]
    # Not materialized: no target, no policy, and the gates an attachment
    # would satisfy are still in the chain.
    assert set(body) == {"id", "chain", "task_paths", "steering", "nodes"}
    assert {"spec_approval", "plan_approval"} <= {n["id"] for n in body["chain"]["nodes"]}


def test_resolved_names_the_attachment_kind_each_node_is_covered_by(client):
    """What the intake preview strikes through (Kraft-ene04): the gate whose
    `artifact` is the kind *and* the node that would produce it. The chain's
    final-review gate is never covered, whatever its `artifact`."""
    nodes = client.get("/api/templates/chains/default/resolved").json()["nodes"]
    covered = {n["id"]: n["covered_by"] for n in nodes}
    assert covered["spec"] == covered["spec_approval"] == "spec"
    assert covered["plan"] == covered["plan_approval"] == "plan"
    assert covered["implementation"] is None
    assert covered["chain_review"] is None


def test_resolved_of_an_unknown_chain_is_404(client):
    assert client.get("/api/templates/chains/nope/resolved").status_code == 404


# ── POST /templates/resolve (resolve-api-supports-candidate-and-library-input) ──


def test_resolve_candidate_uses_installed_library_without_writing(client, templates_dir):
    """A candidate resolves against the installed library's components, and
    is written nowhere: not to disk, and not into the running daemon."""
    candidate = {"id": "candidate", "nodes": [exec_node("build", extends="implementer")]}
    before = snapshot(templates_dir)

    response = client.post("/api/templates/resolve", json={"chain": candidate})

    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    [resolved] = body["chains"]
    assert resolved["id"] == "candidate"
    assert resolved["task_paths"] == ["build.main.t"]
    # `implementer` came from the installed library.yaml.
    assert resolved["chain"]["nodes"][0]["tasks"][0]["kind"] == "agent"
    assert snapshot(templates_dir) == before
    assert client.get("/api/templates/chains/candidate/resolved").status_code == 404


def test_resolve_a_complete_library_in_isolation_without_writing(client, templates_dir):
    """An unsaved library resolves on its own: the installed components are
    not visible to it, so extending one is an error, not a silent borrow."""
    before = snapshot(templates_dir)
    borrowing = {"id": "borrowing", "nodes": [exec_node("build", extends="implementer")]}

    response = client.post(
        "/api/templates/resolve",
        json={"library": {"tasks": {}}, "chains": [SOLO, borrowing]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [c["id"] for c in body["chains"]] == ["solo"]
    assert [i["chain"] for i in body["issues"]] == ["borrowing"]
    assert "implementer" in body["issues"][0]["message"]
    assert snapshot(templates_dir) == before
    assert client.get("/api/templates/chains/solo/resolved").status_code == 404


def test_a_candidate_that_does_not_resolve_is_reported_not_raised(client):
    response = client.post(
        "/api/templates/resolve",
        json={"chain": {"id": "c", "nodes": [exec_node("n", extends="x")]}},
    )
    assert response.status_code == 200
    assert response.json()["chains"] == []
    assert [i["chain"] for i in response.json()["issues"]] == ["c"]


@pytest.mark.parametrize(
    "body",
    [{}, {"chain": SOLO, "library": {}}, {"chain": SOLO, "chains": [SOLO]}],
    ids=["neither", "both", "chains-without-library"],
)
def test_resolve_takes_exactly_one_input(client, body):
    assert client.post("/api/templates/resolve", json=body).status_code == 422


# ── template-v1-is-not-backward-compatible ──


def test_a_legacy_gate_after_chain_does_not_resolve(client):
    """A legacy chain -- hook-name tasks and a `gate_after` on the node -- is
    not a V1 chain, so the V1 resolver refuses it rather than converting it."""
    legacy = {
        "id": "old",
        "nodes": [{"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"}],
    }
    body = client.post("/api/templates/resolve", json={"chain": legacy}).json()
    assert body["chains"] == []
    assert [i["chain"] for i in body["issues"]] == ["old"]


def test_a_legacy_home_starts_degraded_and_names_the_update_command(tmp_path, monkeypatch):
    """A home still holding the pre-V1 layout (`registry.yaml`, no
    `library.yaml`) is what a legacy install's first V1 start finds. It is not
    converted and not overwritten: the daemon comes up degraded, refuses work,
    and says which command replaces the configuration."""
    legacy = fake_templates_dir(tmp_path, "true")
    (legacy / "library.yaml").unlink()
    shutil.rmtree(legacy / "chains")
    (legacy / "registry.yaml").write_text("hooks: {}\n")
    before = snapshot(legacy)

    with _client(tmp_path, monkeypatch, templates_dir=legacy) as client:
        health = client.get("/api/health").json()
        lint = client.get("/api/templates/lint").json()

    assert health["status"] == "degraded"
    assert "kraft admin update" in health["invalid_templates"]["library.yaml"]
    assert lint["valid"] is False
    assert "kraft admin update" in lint["issues"][0]["message"]
    assert snapshot(legacy) == before


def test_a_registry_beside_the_library_configures_no_task(tmp_path, monkeypatch):
    """`registry-is-not-a-task-configuration-source`. A home that has a V1
    library and still carries a `registry.yaml` -- here one the legacy loader
    would refuse outright, rebinding the implementer and a subprocess -- and a
    top-level legacy chain file: the daemon reads neither. It starts healthy,
    serves only `chains/`, and its tasks are the library's typed ones."""
    home = fake_templates_dir(tmp_path, "true")
    (home / "registry.yaml").write_text(
        "hooks:\n"
        "  on.implementation.start: {kind: subprocess, command: [rm, -rf, /]}\n"
        "  on.test.run: {kind: nope}\n"
    )
    (home / "legacy.yaml").write_text(
        "id: legacy\nnodes:\n  - {id: verify, tasks: [on.test.run], gate_after: null}\n"
    )

    with _client(tmp_path, monkeypatch, templates_dir=home) as client:
        health = client.get("/api/health").json()
        listed = [t["id"] for t in client.get("/api/templates/chains").json()]
        resolved = client.get("/api/templates/chains/quick-task/resolved").json()

    assert health["status"] == "ok", health
    assert health["invalid_templates"] == {}
    assert listed == ["default", "quick-task"]
    implement = resolved["chain"]["nodes"][0]["tasks"][0]
    assert implement["kind"] == "agent"
    assert implement["harness"] == "claude"


def test_with_no_library_loaded_the_library_reads_are_503(tmp_path, monkeypatch):
    """Kraft-p2011. A daemon whose `library.yaml` did not load has no saved
    chain to show and no library to resolve a candidate against: 503 naming the
    file, the same door intake answers through. An unsaved library needs no
    installed one, so it still resolves."""
    broken = fake_templates_dir(tmp_path, "true")
    (broken / "library.yaml").write_text("tasks: [unclosed\n")

    with _client(tmp_path, monkeypatch, templates_dir=broken) as client:
        saved = client.get("/api/templates/chains/default/resolved")
        candidate = client.post("/api/templates/resolve", json={"chain": SOLO})
        alone = client.post("/api/templates/resolve", json={"library": {}, "chains": [SOLO]})
        components = client.get("/api/templates/library")
        component = client.get("/api/templates/library/tasks.implementer")

    assert saved.status_code == candidate.status_code == 503
    assert components.status_code == component.status_code == 503
    assert "library.yaml" in components.json()["detail"]
    assert "library.yaml" in saved.json()["detail"]
    assert alone.status_code == 200
    assert [c["id"] for c in alone.json()["chains"]] == ["solo"]
