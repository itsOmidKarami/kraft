"""Filing an item with a one-off chain: POST /work-items `one_off_chain`."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from support.api import CUSTOM_ONE_OFF as CUSTOM
from support.api import _one_off, _orchestrator_db, _paused

LIBRARY_ONLY = "extends: default\nremove: [work_brief]\n"
MCP = {"X-Kraft-Client": "mcp"}


def _row(client, wid):
    with _orchestrator_db() as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone())


def _create(client, repo, text, *, headers=None, query="", **body):
    return client.post(
        f"/api/work-items{query}",
        json={"title": "t", "repo": str(repo), "one_off_chain": text, **body},
        headers=headers or {},
    )


def _digest(client, repo, text):
    r = _create(client, repo, text, query="?dry_run=1")
    assert r.status_code == 200, r.text
    return r.json()["one_off"]["digest"]


def test_files_a_library_only_one_off(client, repo):
    r = _create(client, repo, LIBRARY_ONLY)
    assert r.status_code == 201, r.text
    one_off = r.json()["one_off"]
    assert re.fullmatch(r"[0-9a-f]{64}", one_off.pop("digest"))
    assert one_off == {"custom": 0, "approved": False}
    row = _row(client, r.json()["id"])
    assert row["chain_template"] == "@one-off"
    assert row["one_off_pending"] == 0
    nodes = json.loads(row["materialized_chain"])["chain"]["nodes"]
    assert "work_brief" not in [n["id"] for n in nodes]


def test_files_a_custom_one_off_paused(client, repo):
    r = _create(client, repo, CUSTOM)
    assert r.status_code == 201, r.text
    assert r.json()["one_off"]["custom"] == 1
    row = _row(client, r.json()["id"])
    assert (row["status"], row["one_off_pending"]) == ("paused", 1)


@pytest.mark.parametrize(
    ("text", "words"),
    [
        pytest.param("a: [", "not YAML", id="not-yaml"),
        pytest.param("- x", "mapping", id="a-list"),
        pytest.param("id: default\nextends: default", "names a library chain", id="library-id"),
        pytest.param("extends: nope", "one-off chain:", id="unknown-parent"),
        pytest.param(
            "\n".join(
                ["a0: &a0 [x, x]"] + [f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]" for i in range(1, 22)]
            ),
            "too large",
            id="alias-bomb",
        ),
        pytest.param("extends: default\ndescription: 2024-01-01", "plain YAML values", id="date"),
        pytest.param("extends: default\non: x", "plain YAML values", id="on-key"),
        pytest.param("#" * (257 * 1024), "256 KiB", id="oversize"),
    ],
)
def test_refuses_a_bad_one_off_chain(client, repo, text, words):
    r = _create(client, repo, text)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str) and words in r.json()["detail"]


def test_refuses_two_chains(client, repo):
    r = _create(client, repo, LIBRARY_ONLY, chain="default")
    assert r.status_code == 422
    assert "not both" in json.dumps(r.json())


def test_a_trigger_takes_no_one_off_chain(client, repo):
    r = client.post(
        "/api/triggers", json={"repo": str(repo), "title": "t", "one_off_chain": LIBRARY_ONLY}
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("headers", "stale", "status", "text"),
    [
        pytest.param({}, False, 201, CUSTOM, id="browser"),
        pytest.param({}, True, 409, CUSTOM, id="stale-digest"),
        pytest.param(MCP, False, 403, CUSTOM, id="mcp"),
        pytest.param({}, False, 201, LIBRARY_ONLY, id="library-only-ignored"),
    ],
)
def test_create_approval(client, repo, headers, stale, status, text):
    digest = _digest(client, repo, text)
    r = _create(client, repo, text, headers=headers, approve_one_off="0" * 64 if stale else digest)
    assert r.status_code == status, r.text
    items = client.get("/api/work-items").json()["items"]
    assert len(items) == (1 if status == 201 else 0)
    if status != 201:
        return
    row = _row(client, r.json()["id"])
    approved = text is CUSTOM
    assert r.json()["one_off"]["approved"] is approved
    assert row["one_off_pending"] == 0
    assert (row["one_off_approval"] is not None) is approved
    if approved:
        approval = json.loads(row["one_off_approval"])
        assert (approval["by"], approval["door"], approval["digest"]) == ("human", "web", digest)


def test_custom_one_off_autostart_files_paused(client, repo):
    r = _create(client, repo, CUSTOM, autostart=True)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "paused"
    assert _row(client, r.json()["id"])["status"] == "paused"


def test_dry_run_one_off(client, repo):
    r = _create(client, repo, CUSTOM, query="?dry_run=1")
    assert r.status_code == 200, r.text
    assert "k6" in json.dumps(r.json())
    assert client.get("/api/work-items").json()["items"] == []


def test_create_still_works_for_library_chains(client, repo):
    assert _paused(client, repo)


def test_item_payload_hides_raw_columns(client, repo):
    wid = _one_off(client, repo, CUSTOM)
    item = client.get(f"/api/work-items/{wid}").json()
    assert not {"one_off_chain", "one_off_approval", "one_off_pending"} & item.keys()
