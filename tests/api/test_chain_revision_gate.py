"""A chain revision gate over HTTP (Kraft-ze1yj): an approval applies what the
person was shown, or nothing."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml
from support.api import _await_gate

from kraft.templates.library import TemplateLibrary

PROPOSAL = {
    "rationale": "the plan wants one more check",
    "add": [
        {"after": "build", "node": {"id": "checked", "extends": "extra_check"}, "evidence": "plan"}
    ],
}


def _library(tdir: Path, command: str) -> None:
    path = tdir / "library.yaml"
    library = yaml.safe_load(path.read_text())
    library["tasks"]["check"] = {"kind": "subprocess", "command": command}
    library.setdefault("nodes", {})["extra_check"] = {
        "kind": "exec",
        "tasks": [{"id": "run", "extends": "check"}],
    }
    path.write_text(yaml.safe_dump(library))


def _revising(tdir: Path) -> None:
    """`revising`: a node that writes PROPOSAL as its chain_revision, then the
    gate over it, then work the revision may extend."""
    source = tdir / "proposal.md"
    source.write_text(f"---\nwork_item_ids: [x]\n---\n```json\n{json.dumps(PROPOSAL)}\n```\n")
    write = (
        "sh -c 'mkdir -p .engineering/chain_revisions && "
        f'cp {source} ".engineering/chain_revisions/$(basename "$PWD").md"\''
    )
    chain = {
        "id": "revising",
        "nodes": [
            {
                "id": "revise",
                "kind": "exec",
                "tasks": [{"id": "write", "kind": "subprocess", "command": write}],
            },
            {
                "id": "revision_approval",
                "kind": "gate",
                "artifact": "chain_revision",
                "reject_to": "revise",
            },
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            },
        ],
    }
    (tdir / "chains" / "revising.yaml").write_text(yaml.safe_dump(chain))
    _library(tdir, "echo safe")
    # The same line with an agent `build` and a change set that changes nothing:
    # only a plan's slices make its gate ask.
    quiet = tdir / "quiet.md"
    quiet.write_text('---\nwork_item_ids: [x]\n---\n```json\n{"rationale": "fits"}\n```\n')
    chain["id"] = "slicing"
    chain["nodes"][0]["tasks"][0]["command"] = write.replace(str(source), str(quiet))
    chain["nodes"][2]["tasks"] = [
        {"id": "run", "kind": "agent", "harness": "claude", "prompt": "b"}
    ]
    (tdir / "chains" / "slicing.yaml").write_text(yaml.safe_dump(chain))


pytestmark = pytest.mark.api_client(edit_templates=_revising)


def _filed(client, repo) -> str:
    wid = client.post(
        "/api/work-items",
        json={
            "autostart": True,
            "title": "revise me",
            "repo": str(repo),
            "chain_template": "revising",
        },
    ).json()["id"]
    _await_gate(client, wid, "revision_approval")
    return wid


def _chain_ids(client, wid) -> list[str]:
    return [
        n["id"] for n in client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
    ]


def _approve(client, wid, digest=None):
    body = {"digest": digest} if digest is not None else None
    return client.post(f"/api/work-items/{wid}/gates/revision_approval/approve", json=body)


def _unrevised(client, wid) -> None:
    assert _chain_ids(client, wid) == ["revise", "revision_approval", "build"]
    events = client.get(f"/api/work-items/{wid}/events").json()
    assert not [e for e in events if e["type"] == "chain_revised"]
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "revision_approval"


def test_an_approval_applies_what_its_approver_saw_not_a_later_render(client, repo, templates_dir):
    """Kraft-ec66w: A views (`echo safe`), the library is edited and reloaded,
    B views (`echo something-else`). A's approval carries A's digest, so it is
    refused, not applied as B's view; B's approval goes through."""
    wid = _filed(client, repo)
    seen_by_a = client.get(f"/api/work-items/{wid}/artifact").json()
    assert "echo safe" in seen_by_a["content"]
    _library(templates_dir, "echo something-else")
    client.app.state.library = TemplateLibrary.from_yaml_dir(templates_dir)
    seen_by_b = client.get(f"/api/work-items/{wid}/artifact").json()
    assert "echo something-else" in seen_by_b["content"]
    assert seen_by_a["digest"] != seen_by_b["digest"]

    r = _approve(client, wid, seen_by_a["digest"])

    assert r.status_code == 409, r.text
    assert "changed since you viewed it; review it again" in r.json()["detail"]
    _unrevised(client, wid)

    assert _approve(client, wid, seen_by_b["digest"]).status_code == 200
    assert _chain_ids(client, wid) == ["revise", "revision_approval", "build", "checked"]


def test_a_revision_read_by_its_kind_is_rendered_as_its_gate_shows_it(client, repo):
    """A task's Output reads its document by kind (`GET /artifacts/{kind}`): a
    chain revision is the change set rendered, not the JSON Kraft applies."""
    wid = _filed(client, repo)
    by_kind = client.get(f"/api/work-items/{wid}/artifacts/chain_revision").json()
    assert by_kind["content"].startswith("# Chain revision")
    assert by_kind["content"] == client.get(f"/api/work-items/{wid}/artifact").json()["content"]


def test_a_revision_approval_that_carries_no_digest_is_refused(client, repo):
    """Nobody's view is bound by an approval that says nothing about what it
    saw, so it applies nothing, and says where the digest comes from."""
    wid = _filed(client, repo)
    client.get(f"/api/work-items/{wid}/artifact")

    r = _approve(client, wid)

    assert r.status_code == 409, r.text
    assert "kraft view artifact" in r.json()["detail"]
    _unrevised(client, wid)


def _review(client, wid, digest=None):
    body = {"outcome": "approve"}
    if digest is not None:
        body["digest"] = digest
    return client.post(f"/api/work-items/{wid}/review", json=body)


def test_a_review_approval_carries_the_digest_to_the_gate(client, repo):
    wid = _filed(client, repo)
    digest = client.get(f"/api/work-items/{wid}/artifact").json()["digest"]

    r = _review(client, wid, digest)

    assert r.status_code == 200, r.text
    assert _chain_ids(client, wid) == ["revise", "revision_approval", "build", "checked"]


def test_a_review_approval_without_a_digest_or_with_a_stale_one_is_refused(
    client, repo, templates_dir
):
    wid = _filed(client, repo)
    stale = client.get(f"/api/work-items/{wid}/artifact").json()["digest"]
    _library(templates_dir, "echo something-else")
    client.app.state.library = TemplateLibrary.from_yaml_dir(templates_dir)
    client.get(f"/api/work-items/{wid}/artifact")

    none = _review(client, wid)
    old = _review(client, wid, stale)

    assert none.status_code == 409 and "kraft view artifact" in none.json()["detail"]
    assert old.status_code == 409
    assert "changed since you viewed it; review it again" in old.json()["detail"]
    _unrevised(client, wid)


def test_an_agent_reviewed_sliced_revision_applies_without_a_stale_error(client, repo):
    """The digest an agent's review captured (`revision.artifact_digest`) and the
    one the approval computes (`_revise`) parse the artifact the same way, slicing
    entry included, so a sliced revision does not fail as stale."""
    (repo / "plan.md").write_text(
        "## Slices\n\n- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2.\n\n"
        "### Task 1: a\n\n### Task 2: b\n"
    )
    wid = client.post(
        "/api/work-items",
        json={
            "autostart": True,
            "title": "slice me",
            "repo": str(repo),
            "chain_template": "slicing",
            "attachments": [{"kind": "plan", "path": "plan.md"}],
        },
    ).json()["id"]
    _await_gate(client, wid, "revision_approval")
    shown = client.get(f"/api/work-items/{wid}/artifact").json()
    assert "slice" in shown["content"]

    from kraft import executor, slices
    from kraft.templates import revision

    st = client.app.state
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    conn.close()
    captured = revision.artifact_digest(
        executor.chain_of(row),
        "revision_approval",
        (
            st.run_dirs.worktrees / wid / ".engineering" / "chain_revisions" / f"{wid}.md"
        ).read_text(),
        library=st.library,
        slicing=slices.slicing_input(st.run_dirs, row, 6),
    )
    assert captured == shown["digest"]

    r = _approve(client, wid, shown["digest"])

    assert r.status_code == 200, r.text
    assert "slices" in _chain_ids(client, wid)
