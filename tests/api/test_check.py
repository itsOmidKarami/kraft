"""`POST /api/templates/check` over HTTP: positions against the buffer, and
the save routes refusing what the check reports, in the same words."""

from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.api_client(default_setup=False)

CHAIN = (
    "id: solo\n"
    "nodes:\n"
    "  - id: run\n"
    "    kind: exec\n"
    "    tasks:\n"
    "      - id: t\n"
    "        extends: no_such_task\n"
)


def check(client, file, text):
    return client.post("/api/templates/check", json={"file": file, "text": text})


def test_issue_positions_are_computed_against_the_buffer(client, templates_dir):
    (templates_dir / "chains" / "solo.yaml").write_text("id: solo\n")  # disk differs
    [issue] = check(client, "chains/solo.yaml", CHAIN).json()["issues"]
    assert (issue["line"], issue["column"]) == (7, 9)
    assert issue["file"] == str(templates_dir / "chains" / "solo.yaml")


def test_an_inherited_definition_points_related_into_the_library(client, templates_dir):
    library = yaml.safe_load((templates_dir / "library.yaml").read_text())
    library.setdefault("tasks", {})["broken"] = {"kind": "subprocess", "command": 7}
    (templates_dir / "library.yaml").write_text(yaml.safe_dump(library))
    client.post("/api/templates/reload")
    text = CHAIN.replace("no_such_task", "broken")
    [issue] = check(client, "chains/solo.yaml", text).json()["issues"]
    assert (issue["line"], issue["column"]) == (6, 9)
    assert issue["related"]["file"] == str(templates_dir / "library.yaml")
    assert issue["related"]["line"] > 1


def test_a_yaml_error_is_at_the_parser_position(client):
    [issue] = check(client, "policy.yaml", "default: {}\nloops: [\n").json()["issues"]
    assert issue["line"] >= 2


@pytest.mark.parametrize("file", ["../access.yaml", "chains/../../x.yaml", "notes.txt"])
def test_a_path_outside_the_config_files_is_400(client, file):
    assert check(client, file, "a: 1\n").status_code == 400


def test_the_check_and_the_chain_save_agree(client):
    [issue] = check(client, "chains/solo.yaml", CHAIN).json()["issues"]
    saved = client.put("/api/templates/chains/solo", json={"text": CHAIN})
    assert saved.status_code == 422 and saved.json()["detail"] == issue["message"]


def test_the_check_and_the_access_save_agree(client):
    [issue] = check(client, "access.yaml", "bind: 0.0.0.0\nport: 8765\n").json()["issues"]
    saved = client.put("/api/access", json={"bind": "0.0.0.0"})
    assert saved.status_code == 422 and saved.json()["detail"] == issue["message"]


def test_the_check_and_the_notify_save_agree(client):
    [issue] = check(client, "notify.yaml", "enabled: true\n").json()["issues"]
    saved = client.put("/api/notify", json={"enabled": True})
    assert saved.status_code == 422 and saved.json()["detail"] == issue["message"]


def test_the_check_and_the_policy_save_agree(client):
    [issue] = check(client, "policy.yaml", "loops: {}\ndefault: {}\n").json()["issues"]
    saved = client.put("/api/policy", json={"loops": {}, "default": {}})
    assert saved.status_code == 422 and saved.json()["detail"] == issue["message"]
