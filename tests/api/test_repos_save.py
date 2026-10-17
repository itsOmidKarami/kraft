"""Saving repos.yaml from the repo routes keeps the file's own text."""

from __future__ import annotations

import pytest
import yaml
from support.harness import make_repo

pytestmark = pytest.mark.api_client(default_setup=False)


@pytest.mark.parametrize("verb", ["connect", "patch", "delete"])
def test_a_save_keeps_the_files_comments_and_quoting(client, templates_dir, tmp_path, verb):
    """Connect, PATCH and DELETE re-saved repos.yaml with a plain dump: every
    comment gone (`# quiet` included) and a double-quoted "1" read back as
    '1'. A first connect also spelled out `forge: null`, `models: {}` and the
    other defaults of the entry it added."""
    other = make_repo(tmp_path)
    kept = (
        "# the repos I work in\nrepos:\n"
        '  - path: /r   # quiet\n    name: "1"\n    forge: ~\n'
        "  # the other one\n  - path: /other\n    test_command: pytest\n"
    )
    (templates_dir / "repos.yaml").write_text(kept)
    if verb == "connect":
        reply = client.post("/api/repos", json={"path": str(other), "enabled": False})
        assert reply.status_code == 201, reply.text
    elif verb == "patch":
        reply = client.patch("/api/repos?path=/other", json={"setup_command": "uv sync"})
        assert reply.status_code == 200, reply.text
    else:
        reply = client.delete("/api/repos?path=/other")
        assert reply.status_code == 204, reply.text
    text = (templates_dir / "repos.yaml").read_text()
    assert text.startswith(
        '# the repos I work in\nrepos:\n  - path: /r   # quiet\n    name: "1"\n    forge: ~\n'
    ), text
    added = yaml.safe_load(text)["repos"][-1]
    if verb == "connect":
        assert set(added) == {"path", "name", "default_chain", "managed", "enabled"}, text
    if verb == "patch":
        assert "# the other one\n" in text and "setup_command: uv sync" in text
    if verb == "delete":
        assert [e["path"] for e in yaml.safe_load(text)["repos"]] == ["/r"]


def test_a_save_turning_a_sandboxed_repo_in_place_is_refused(client, templates_dir):
    sandbox = {"kind": "docker", "image": "kraft-worker:node"}
    (templates_dir / "repos.yaml").write_text(
        yaml.safe_dump({"repos": [{"path": "/r", "sandbox": sandbox}]})
    )
    reply = client.patch("/api/repos?path=/r", json={"worktrees": False})
    assert reply.status_code == 422, reply.text
    assert "'worktrees: false'" in reply.json()["detail"]
    assert "drop 'sandbox'" in reply.json()["detail"]
