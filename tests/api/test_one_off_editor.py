"""POST /one-off-chain/ops: the editor's ops on a one-off chain's text, stored nowhere."""

from __future__ import annotations

import re

import pytest
from support.api import LIBRARY_ONLY_ONE_OFF

CUSTOM_LOAD_TEST = """\
extends: default
add:
  - after: verification
    node:
      id: load_test
      kind: exec
      tasks:
        - id: run
          kind: agent
          harness: claude
          prompt: run k6
"""

LIBRARY_NODE = """\
extends: default
add:
  - after: verification
    node:
      id: v2
      extends: verification
"""


def _ops(client, text, ops=(), **body):
    return client.post("/api/one-off-chain/ops", json={"text": text, "ops": list(ops), **body})


def test_ops_add_a_node(client):
    add = {"op": "add_node", "at": 1, "id": "load_test", "kind": "exec"}
    r = _ops(client, LIBRARY_ONLY_ONE_OFF, [add])
    assert r.status_code == 200, r.text
    assert "load_test" in r.json()["text"]
    assert r.json()["ops"] == [{"op": "add_node"}]
    assert client.get("/api/drafts").json() == []


def test_ops_resolve_only(client):
    body = _ops(client, LIBRARY_ONLY_ONE_OFF).json()
    assert re.fullmatch(r"[0-9a-f]{64}", body["digest"])
    ids = [n["id"] for n in body["nodes"]]
    assert "implementation" in ids and "work_brief" not in ids
    assert {"id", "kind", "fix_loop", "auto_escalate", "covered_by"} <= body["nodes"][0].keys()
    assert (body["area"], body["key"], body["draft"], body["updated_at"]) == (
        "chains",
        "one-off",
        True,
        None,
    )


def test_ops_bad_yaml(client):
    r = _ops(client, "a: [", [{"op": "add_node", "at": 0, "id": "x", "kind": "gate"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "yaml_error" in body["result"]
    assert body["digest"] is None
    assert body["nodes"] == []


@pytest.mark.parametrize(
    "op",
    [
        pytest.param(
            {"op": "move_to_library", "path": "work_brief", "name": "w"}, id="move-to-library"
        ),
        pytest.param({"op": "new_chain"}, id="new-chain"),
        pytest.param({"op": "delete_chain"}, id="delete-chain"),
        pytest.param({"op": "add_component"}, id="add-component"),
        pytest.param({"op": "copy_component"}, id="copy-component"),
        pytest.param({"op": "rename", "path": "", "id": "other"}, id="rename-the-chain"),
    ],
)
def test_ops_refuse_library_writes(client, op):
    r = _ops(client, LIBRARY_ONLY_ONE_OFF, [op])
    assert r.status_code == 422, r.text
    assert "library or other chains" in r.json()["detail"]


@pytest.mark.parametrize(
    ("text", "named"),
    [
        pytest.param(CUSTOM_LOAD_TEST, ["load_test"], id="inline-prompt"),
        pytest.param(LIBRARY_NODE, [], id="library-node"),
        pytest.param(
            "extends: default\noverrides:\n  implementation.main.implement:\n    prompt: do it\n",
            ["implementation"],
            id="override-prompt",
        ),
    ],
)
def test_ops_name_custom_nodes(client, text, named):
    assert _ops(client, text).json()["custom_nodes"] == named


def test_ops_custom_id_has_no_problem(client):
    body = _ops(client, "id: my-chain\n" + LIBRARY_ONLY_ONE_OFF).json()
    assert body["key"] == "my-chain"
    assert body["result"]["problems"] == []
    assert body["nodes"]


def test_ops_refuse_too_much_text(client):
    assert _ops(client, "a: " + "x" * (256 * 1024)).status_code == 422


def test_ops_report_a_failed_op(client):
    r = _ops(
        client, LIBRARY_ONLY_ONE_OFF, [{"op": "add_node", "at": 99, "id": "x", "kind": "gate"}]
    )
    assert r.status_code == 422
    assert r.json()["op"] == 0


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("id: my-chain\n" + CUSTOM_LOAD_TEST, id="plain"),
        pytest.param(CUSTOM_LOAD_TEST, id="no-id"),
        pytest.param(
            "# why\n\n" + CUSTOM_LOAD_TEST.replace("run k6", "run  k6   # why"), id="comments"
        ),
    ],
)
def test_ops_digest_is_accepted_at_create(client, repo, text):
    digest = _ops(client, text).json()["digest"]
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "one_off_chain": text, "approve_one_off": digest},
    )
    assert r.status_code == 201, r.text
    assert r.json()["one_off"]["approved"] is True
    assert r.json()["one_off"]["digest"] == digest


@pytest.mark.parametrize(
    ("text", "words"),
    [
        pytest.param("id: ../x\nextends: default\n", "lowercase letter", id="id-traversal"),
        pytest.param("id: A b\nextends: default\n", "lowercase letter", id="id-bad-chars"),
        pytest.param("id: default\nextends: default\n", "names a library chain", id="library-id"),
        pytest.param("a: 2020-01-01\n", "plain YAML values", id="date"),
        pytest.param("1: x\nb: y\n", "plain YAML values", id="int-key"),
        pytest.param("a: .inf\n", "plain YAML values", id="inf"),
        pytest.param("a: 2020-13-45\n", "not YAML", id="bad-date"),
    ],
)
def test_ops_refuse_hostile_text_with_one_sentence(client, text, words):
    r = _ops(client, text)
    assert r.status_code == 422, r.text
    assert words in r.json()["detail"]


def test_ops_answer_the_custom_parts(client):
    body = _ops(client, CUSTOM_LOAD_TEST).json()
    assert [p["path"] for p in body["custom"]] == ["add[0].node"]
    assert _ops(client, LIBRARY_ONLY_ONE_OFF).json()["custom"] == []


def test_ops_a_chain_with_no_base_is_custom_throughout(client):
    text = (
        "id: mine\nnodes:\n  - id: a\n    kind: exec\n    tasks:\n"
        "      - id: t\n        kind: agent\n        harness: claude\n        prompt: x\n"
    )
    body = _ops(client, text).json()
    assert [p["path"] for p in body["custom"]] == [""]
    assert body["custom_nodes"] == [n["id"] for n in body["nodes"]] != []
