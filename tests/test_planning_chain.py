"""The planning chain end to end against the fake agent: the spec node writes
and commits a document, the gate offers it, a rejection re-runs the node with
the human's note, and approving moves on to the plan node."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from support.harness import fake_templates_dir, make_repo

#: No default repo entry for an unconnected repo, as before this used the shared client.
pytestmark = pytest.mark.api_client(default_setup=False)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _templates(tmp_path: Path) -> Path:
    return fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))


@pytest.fixture
def prompt_log(tmp_path) -> Path:
    return tmp_path / "prompts.log"


@pytest.fixture
def templates_dir(tmp_path):
    return _templates(tmp_path)


@pytest.fixture(autouse=True)
def _agent_env(tmp_path, monkeypatch, prompt_log):
    """Read at startup, so set before `client` starts the app (autouse runs first)."""
    monkeypatch.setenv("KRAFT_SKILLS_DIR", str(tmp_path / "no-skills"))
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompt_log))


def _await_gate(client, wid, gate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/api/work-items/{wid}").json().get("pending_gate") == gate:
            return
        time.sleep(0.2)
    raise AssertionError(f"{wid} never reached {gate}")


@pytest.mark.slow
def test_spec_gate_offers_the_document_then_reject_and_approve(client, tmp_path, prompt_log):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items",
        json={"title": "add a flag", "repo": str(repo), "chain_template": "default"},
    ).json()["id"]

    _await_gate(client, wid, "spec_approval")
    body = client.get(f"/api/work-items/{wid}").json()
    assert body["gate_artifact"] == f".engineering/specs/{wid}.md"
    assert client.get(f"/api/work-items/{wid}/artifact").json()["title"] == "fake spec"

    client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "too vague"})
    _await_gate(client, wid, "spec_approval")
    # The rejection note reaches the relaunched agent as its instruction, which
    # is the whole of how "revise the same document" works (spec §5).
    assert "too vague" in prompt_log.read_text()

    client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")
    _await_gate(client, wid, "plan_approval")
    assert (
        client.get(f"/api/work-items/{wid}").json()["gate_artifact"]
        == f".engineering/plans/{wid}.md"
    )
    assert client.get(f"/api/work-items/{wid}/artifact").json()["title"] == "fake plan"


@pytest.mark.slow
def test_a_rejected_plan_rerun_is_framed_as_a_revision(client, tmp_path, prompt_log):
    """Kraft-bol end to end: the plan node's re-run is told to edit the file it
    already wrote, not to start again from the brief."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items",
        json={"title": "add a flag", "repo": str(repo), "chain_template": "default"},
    ).json()["id"]

    _await_gate(client, wid, "spec_approval")
    client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")
    _await_gate(client, wid, "plan_approval")

    client.post(
        f"/api/work-items/{wid}/gates/plan_approval/reject", json={"note": "task 4 has no test"}
    )
    _await_gate(client, wid, "plan_approval")

    sent = [p for p in prompt_log.read_text().split("\n\x00\n") if p.strip()]
    revised = [p for p in sent if "task 4 has no test" in p]
    assert len(revised) == 1
    assert f".engineering/plans/{wid}.md" in revised[0]
    assert not revised[0].startswith("A human has steered this run:")
