"""A chain revision gate over HTTP (Kraft-ze1yj): an approval applies what the
person was shown, or nothing."""

from __future__ import annotations

import json
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


pytestmark = pytest.mark.api_client(edit_templates=_revising)


def _filed(client, repo) -> str:
    wid = client.post(
        "/api/work-items",
        json={"title": "revise me", "repo": str(repo), "chain_template": "revising"},
    ).json()["id"]
    _await_gate(client, wid, "revision_approval")
    return wid


def _chain_ids(client, wid) -> list[str]:
    return [
        n["id"] for n in client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
    ]


def test_a_library_change_after_the_gate_was_shown_refuses_the_approval(
    client, repo, templates_dir
):
    """The reviewer's scenario: the gate shows `checked` running `echo safe`;
    the library is edited and reloaded; approving must not write the node the
    edited library now resolves to."""
    wid = _filed(client, repo)
    shown = client.get(f"/api/work-items/{wid}/artifact").json()["content"]
    assert "echo safe" in shown
    _library(templates_dir, "echo something-else")
    client.app.state.library = TemplateLibrary.from_yaml_dir(templates_dir)

    r = client.post(f"/api/work-items/{wid}/gates/revision_approval/approve")

    assert r.status_code == 409, r.text
    assert "review it again" in r.json()["detail"]
    assert _chain_ids(client, wid) == ["revise", "revision_approval", "build"]
    events = client.get(f"/api/work-items/{wid}/events").json()
    assert not [e for e in events if e["type"] == "chain_revised"]
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "revision_approval"

    # Shown again, it is what gets applied.
    assert "echo something-else" in client.get(f"/api/work-items/{wid}/artifact").json()["content"]
    assert client.post(f"/api/work-items/{wid}/gates/revision_approval/approve").status_code == 200
    assert _chain_ids(client, wid) == ["revise", "revision_approval", "build", "checked"]


def test_an_approval_nobody_rendered_applies_what_it_computes(client, repo):
    wid = _filed(client, repo)

    assert client.post(f"/api/work-items/{wid}/gates/revision_approval/approve").status_code == 200

    assert _chain_ids(client, wid) == ["revise", "revision_approval", "build", "checked"]
