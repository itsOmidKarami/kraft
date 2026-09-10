"""GET /work-items/{wid}/artifact — what a human reads at a spec or plan gate.

The document is read off the worktree, not out of the index: a reviewer sitting
at an open gate must see the file the agent just wrote, whether or not the
indexer has caught up.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import api, templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _templates(tmp_path: Path) -> Path:
    """fake_templates_dir, with the spec hook bound to the fake agent and a
    one-node template that stops at spec_approval."""
    d = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    registry = yaml.safe_load((d / "registry.yaml").read_text())
    registry["hooks"]["on.spec.requested"] = {
        "kind": "agent",
        "command": str(_FAKE_CLAUDE),
        "artifact": "spec",
    }
    (d / "registry.yaml").write_text(yaml.safe_dump(registry))
    (d / "spec-only.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "spec-only",
                "nodes": [
                    {
                        "id": "spec",
                        "tasks": ["on.spec.requested"],
                        "gate_after": "spec_approval",
                    }
                ],
            }
        )
    )
    return d


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(_templates(tmp_path)))
    # No operator overlay: the bundled method files are what the hook resolves.
    monkeypatch.setenv("KRAFT_SKILLS_DIR", str(tmp_path / "no-skills"))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as c:
        yield c


def _await_gate(client, wid, gate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/api/work-items/{wid}").json().get("pending_gate") == gate:
            return
        time.sleep(0.2)
    raise AssertionError(f"{wid} never reached {gate}")


@pytest.fixture
def item_at_spec_gate(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "add a flag", "chain_template": "spec-only"},
    ).json()["id"]
    _await_gate(client, wid, "spec_approval")
    return wid


@pytest.fixture
def worktree(client, item_at_spec_gate) -> Path:
    return Path(client.get(f"/api/work-items/{item_at_spec_gate}").json()["worktree_path"])


def _write_artifact(worktree: Path, wid: str, text: str) -> Path:
    """The document a compliant agent would have written.

    Written by the test, overwriting whatever `fixtures/fake-claude.sh` already
    wrote for this hook (it has honoured the artifact contract since Task 8):
    these tests are about the endpoint's contract, not about the fake's own
    fixed content.
    """
    path = worktree / ".engineering" / "specs" / f"{wid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_gate_artifact_names_the_file_the_hook_wrote(client, item_at_spec_gate, worktree):
    _write_artifact(worktree, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")

    body = client.get(f"/api/work-items/{item_at_spec_gate}").json()
    assert body["gate_artifact"] == f".engineering/specs/{item_at_spec_gate}.md"

    art = client.get(f"/api/work-items/{item_at_spec_gate}/artifact").json()
    assert art["title"] == "A spec"
    assert art["content"].strip() == "the body"
    assert art["truncated"] is False


def test_a_gate_whose_agent_wrote_nothing_reports_no_artifact(client, item_at_spec_gate, worktree):
    """An agent can report done without honouring the contract. The gate stays
    answerable; there is just nothing to read.

    `fixtures/fake-claude.sh` always honours the contract now, so "wrote
    nothing" is simulated by removing what it wrote rather than by picking a
    fake invocation that skips it.
    """
    (worktree / ".engineering" / "specs" / f"{item_at_spec_gate}.md").unlink()
    assert client.get(f"/api/work-items/{item_at_spec_gate}").json()["gate_artifact"] is None
    # A UI polls the detail endpoint while it waits for the agent's session to
    # finish; this case must not write an event on every poll, only the ones
    # below where the file was there and something then went wrong reading it.
    client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    assert client.get(f"/api/work-items/{item_at_spec_gate}/artifact").status_code == 404
    events = client.get(f"/api/work-items/{item_at_spec_gate}/events").json()
    assert not [e for e in events if e["type"] == "artifact_refused"]


def test_a_symlink_out_of_the_worktree_is_a_404(
    client, item_at_spec_gate, worktree, tmp_path, caplog
):
    outside = tmp_path / "secret.md"
    outside.write_text("not yours")
    path = worktree / ".engineering" / "specs" / f"{item_at_spec_gate}.md"
    path.unlink()  # fake-claude.sh already wrote a real file here; swap it out
    path.symlink_to(outside)

    with caplog.at_level("WARNING", logger="kraft.api"):
        resp = client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    assert resp.status_code == 404
    # Pins the escape branch specifically, not merely "some 404 happened": a
    # regression that made the containment check a no-op would still 404 (the
    # read would fail some other way) without ever logging this.
    assert item_at_spec_gate in caplog.text

    # Spec §4: the reason has to reach a reviewer with no access to this log,
    # so it belongs on the item's own timeline too, not only in caplog.
    events = client.get(f"/api/work-items/{item_at_spec_gate}/events").json()
    refusals = [e for e in events if e["type"] == "artifact_refused"]
    assert len(refusals) == 1
    assert refusals[0]["payload"]["reason"] == "escaped_containment"


def test_a_symlinked_directory_out_of_the_worktree_is_a_404(
    client, item_at_spec_gate, worktree, tmp_path, caplog
):
    """The escape doesn't have to be the artifact file itself — a symlinked
    ancestor directory resolves outside the worktree exactly the same way."""
    outside_dir = tmp_path / "outside-engineering"
    (outside_dir / "specs").mkdir(parents=True)
    (outside_dir / "specs" / f"{item_at_spec_gate}.md").write_text("not yours either")

    engineering = worktree / ".engineering"
    shutil.rmtree(engineering)  # fake-claude.sh already wrote a real tree here
    engineering.symlink_to(outside_dir, target_is_directory=True)

    with caplog.at_level("WARNING", logger="kraft.api"):
        resp = client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    assert resp.status_code == 404
    assert item_at_spec_gate in caplog.text


def test_open_no_symlinks_refuses_an_ancestor_directory_swapped_for_a_symlink(tmp_path):
    """The case plain `O_NOFOLLOW` on the resolved leaf path misses: swapping an
    *ancestor* directory for a symlink after `resolve()` ran. This is the exact
    scenario a round-1 fix (leaf-only `O_NOFOLLOW`) let through -- the walk
    below must refuse it at the `.engineering` component, not just the leaf.
    """
    from kraft.api import _open_no_symlinks

    root = tmp_path / "root"
    (root / ".engineering" / "specs").mkdir(parents=True)
    (root / ".engineering" / "specs" / "x.md").write_text("legitimate")

    outside = tmp_path / "outside"
    (outside / "specs").mkdir(parents=True)
    (outside / "specs" / "x.md").write_text("not yours")

    shutil.rmtree(root / ".engineering")
    (root / ".engineering").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        _open_no_symlinks(root, ".engineering/specs/x.md")


def test_artifact_over_the_cap_truncates_without_500ing_on_a_split_codepoint(
    client, item_at_spec_gate, worktree
):
    """Spec §7's named-but-uncovered branch: `fh.read(DIFF_MAX_BYTES + 1)`,
    `len(data) > DIFF_MAX_BYTES`, and `data[:DIFF_MAX_BYTES].decode(errors=
    "replace")`. The last of those only earns its keep if the cut can land mid-
    codepoint, so the file is built to put the two-byte lead byte of an "é"
    exactly at the cut point.
    """
    from kraft.api import DIFF_MAX_BYTES

    before = b"a" * (DIFF_MAX_BYTES - 1)  # cut lands right after this
    split_char = "é".encode()  # 2 bytes; the cut falls between them
    after = b"a" * 9
    content = before + split_char + after
    assert len(content) == DIFF_MAX_BYTES + 10

    path = worktree / ".engineering" / "specs" / f"{item_at_spec_gate}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    resp = client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    assert resp.status_code == 200  # not a 500 from a decode error
    body = resp.json()
    assert body["truncated"] is True
    assert len(body["content"]) <= DIFF_MAX_BYTES


def test_approving_a_gate_ingests_its_artifact_into_the_index(client, item_at_spec_gate, worktree):
    """Nothing Kraft wrote under `.engineering/` is committed anymore
    (`forge._work_product_pathspec` excludes it), so gate approval is what
    makes the spec durable:
    it must land in the index synchronously, before the approve response comes
    back — not on the indexer's own scan schedule."""
    _write_artifact(worktree, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")

    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200

    docs = client.get(f"/api/work-items/{item_at_spec_gate}/documents").json()["documents"]
    artifacts = [d for d in docs if d["source_kind"] == "artifact"]
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "specs"
    assert artifacts[0]["path"] == f".engineering/specs/{item_at_spec_gate}.md"

    doc = client.get(f"/api/documents/{artifacts[0]['document_id']}").json()
    assert doc["title"] == "A spec"
    assert doc["content"] == "\nthe body\n"


def test_approving_a_gate_survives_a_broken_index(client, item_at_spec_gate, worktree, monkeypatch):
    """A locked index or an unwritable index.db must not block a gate the
    agent already satisfied. Same contract `ingest_session_summary` gives the
    event-drain loop (the index never blocks the executor) -- enforced here
    instead, since approval is a synchronous request a reviewer is waiting on.
    The approve response must still succeed and the gate must still clear even
    though nothing got indexed."""
    _write_artifact(worktree, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")

    async def _boom(*args, **kwargs):
        raise RuntimeError("index is locked")

    monkeypatch.setattr(api.app.state.indexer, "ingest_gate_artifact", _boom)

    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200

    docs = client.get(f"/api/work-items/{item_at_spec_gate}/documents").json()["documents"]
    assert [d for d in docs if d["source_kind"] == "artifact"] == []


def test_approving_a_gate_with_no_artifact_ingests_nothing(client, item_at_spec_gate, worktree):
    """An agent that reported done without honouring the artifact contract
    (Task 8) must not make gate approval 500, and must leave nothing behind
    for this gate's artifact -- there is nothing to remember. (The fake
    agent's own session summary still ingests separately; this only pins the
    artifact side.)"""
    (worktree / ".engineering" / "specs" / f"{item_at_spec_gate}.md").unlink()

    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200

    docs = client.get(f"/api/work-items/{item_at_spec_gate}/documents").json()["documents"]
    assert [d for d in docs if d["source_kind"] == "artifact"] == []


def test_the_review_gate_still_resolves_its_brief(tmp_path):
    """§5. Splitting `on.mr.sync` onto its own node must not move the gate off
    the node that carries the review brief: `_gate_artifact` scans the gate
    node's own tasks, so a gate on a sync-only node would resolve nothing and
    the human would be asked to approve a merge request with no document."""
    reg = templates.load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    chain = templates.materialize(
        templates.load_templates(_REPO_ROOT / "templates", reg).valid["default"]
    )
    brief = tmp_path / "w1" / ".engineering" / "review_briefs" / "w1.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("---\ntitle: The brief\n---\n\nbody\n")

    st = SimpleNamespace(run_dirs=SimpleNamespace(worktrees=tmp_path), registry=reg)
    row = {"id": "w1", "chain_definition": json.dumps(chain)}

    assert (
        api._gate_artifact(st, row, "human_review_approval") == ".engineering/review_briefs/w1.md"
    )
