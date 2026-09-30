"""The config draft routes: a draft is kept server-side, published over the
files only when none changed since it began and it has no problem, and
discarded for good."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api_client(default_setup=False)

DEFAULT = "/api/drafts/chains/default"
SCRATCH = """# mine
id: scratch
nodes:
  - id: run
    kind: exec
    tasks:
      - {id: t, kind: subprocess, command: "true"}
"""


def put(client, key, text):
    return client.put(f"/api/drafts/chains/{key}/files/chains/{key}.yaml", json={"text": text})


def test_get_answers_the_published_files_until_a_put_makes_a_draft(client, templates_dir):
    shipped = (templates_dir / "chains" / "default.yaml").read_text()
    got = client.get(DEFAULT).json()
    assert (got["draft"], got["files"]) == (False, {"chains/default.yaml": shipped})
    assert got["result"]["resolved"]["id"] == "default"
    assert got["result"]["problems"] == got["result"]["changes"] == []

    edited = shipped.replace("Review and approve the specification.", "Read the spec.")
    r = put(client, "default", edited)
    assert r.status_code == 200
    assert r.json()["draft"] is True
    assert r.json()["result"]["changes"] == [
        {"path": "spec_approval", "kind": "change", "summary": "message", "fields": ["message"]}
    ]
    assert client.get(DEFAULT).json()["files"] == {"chains/default.yaml": edited}
    [listed] = client.get("/api/drafts").json()
    assert (listed["key"], listed["files"], listed["changes"]) == (
        "default",
        ["chains/default.yaml"],
        1,
    )
    # Nothing on disk until a publish.
    assert (templates_dir / "chains" / "default.yaml").read_text() == shipped


def test_publish_writes_the_files_reloads_and_drops_the_draft(client, templates_dir):
    assert client.get("/api/drafts/chains/scratch").status_code == 404
    put(client, "scratch", SCRATCH)

    r = client.post("/api/drafts/chains/scratch/publish")
    assert r.status_code == 200
    assert r.json()["published"] == ["chains/scratch.yaml"]
    assert (templates_dir / "chains" / "scratch.yaml").read_text() == SCRATCH
    assert "scratch" in {t["id"] for t in client.get("/api/templates/chains").json()}
    assert client.get("/api/drafts").json() == []


def test_publish_over_a_file_saved_since_answers_409_with_the_diff(client, templates_dir):
    path = templates_dir / "chains" / "default.yaml"
    shipped = path.read_text()
    put(client, "default", shipped.replace("the implementation plan.", "the plan."))
    saved = shipped.replace("Review and approve the specification.", "Saved elsewhere.")
    assert client.put("/api/templates/chains/default", json={"text": saved}).status_code == 200

    r = client.post(f"{DEFAULT}/publish")
    assert r.status_code == 409
    diff = r.json()["files"]["chains/default.yaml"]
    assert diff["published"] == saved
    assert "-    message: Saved elsewhere.\n" in diff["diff"]
    assert path.read_text() == saved
    assert client.get(DEFAULT).json()["draft"] is True


def test_publish_with_a_problem_answers_422_and_writes_nothing(client, templates_dir):
    put(client, "scratch", SCRATCH.replace('command: "true"}', 'command: "true", bogus: 1}'))

    r = client.post("/api/drafts/chains/scratch/publish")
    assert r.status_code == 422
    assert [p["field"] for p in r.json()["problems"]] == ["bogus"]
    assert not (templates_dir / "chains" / "scratch.yaml").exists()


def test_a_library_draft_reports_the_chains_it_breaks_and_publishes(client, templates_dir):
    path = templates_dir / "library.yaml"
    shipped = path.read_text()
    broken = shipped.replace("  spec_author:", "  renamed_author:")
    problems = client.put(
        "/api/drafts/library/library/files/library.yaml", json={"text": broken}
    ).json()["result"]["problems"]
    assert {p["file"] for p in problems} == {"chains/default.yaml"}

    edited = shipped.replace("steering:\n", "steering:\n  terse: {instructions: Be brief.}\n", 1)
    client.put("/api/drafts/library/library/files/library.yaml", json={"text": edited})
    assert client.post("/api/drafts/library/library/publish").status_code == 200
    assert path.read_text() == edited


def test_discard_is_final(client):
    put(client, "scratch", SCRATCH)
    assert client.delete("/api/drafts/chains/scratch").status_code == 204
    assert client.delete("/api/drafts/chains/scratch").status_code == 404
    assert client.post("/api/drafts/chains/scratch/undo").status_code == 409


@pytest.mark.parametrize(
    ("method", "url", "status"),
    [
        ("get", "/api/drafts/harnesses/x", 404),
        ("get", "/api/drafts/chains/Bad..id", 400),
        ("put", "/api/drafts/chains/default/files/library.yaml", 422),
        ("put", "/api/drafts/library/library/files/chains/default.yaml", 422),
    ],
    ids=["unknown-area", "bad-chain-id", "chain-outside-file", "library-outside-file"],
)
def test_a_request_outside_an_area_is_refused(client, method, url, status):
    body = {"json": {"text": "x: 1\n"}} if method == "put" else {}
    assert getattr(client, method)(url, **body).status_code == status
