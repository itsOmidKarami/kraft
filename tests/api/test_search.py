from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from support.api import _client
from support.harness import isolated_bd, make_repo_with_engineering

from kraft.adapters import beads

#: No repo indexed at startup unless a test names one (`_indexing`).
pytestmark = pytest.mark.api_client(env={"KRAFT_INDEX_REPOS": ""})


def _indexing(tmp_path, monkeypatch, repo):
    """A client whose startup index covers `repo` (`KRAFT_INDEX_REPOS`)."""
    return _client(tmp_path, monkeypatch, env={"KRAFT_INDEX_REPOS": str(repo)})


def _commit(repo: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", msg], check=True, capture_output=True)


def test_search_documents_and_rescan(tmp_path, monkeypatch):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/ws.md": (
                "---\ntitle: WS transport\nowner: omid\n---\n"
                "reconnect backoff schedule caps at ten seconds\n"
            ),
            ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
        },
    )
    with _indexing(tmp_path, monkeypatch, repo) as client:
        # pin the mode: this case is about FTS ranking, and whether the vector
        # extra is installed differs between a dev machine and CI.
        body = client.get("/api/search", params={"q": "reconnect backoff", "mode": "fts"}).json()
        assert body["mode"] == "fts"
        assert [h["path"] for h in body["results"]] == [".engineering/specs/ws.md"]
        hit = body["results"][0]
        assert hit["title"] == "WS transport"
        assert hit["kind"] == "specs"
        assert "[reconnect]" in hit["snippet"]
        assert hit["links"] == []

        # Filters, under exact matching. Pinned to fts on purpose: the vector leg
        # is fuzzy, so "no results" is not a meaningful assertion under hybrid —
        # a semantically near document is a correct hit there.
        fts = {"mode": "fts"}
        assert client.get("/api/search", params={"q": "board", "kind": "plans", **fts}).json()[
            "results"
        ]
        assert not client.get("/api/search", params={"q": "board", "kind": "specs", **fts}).json()[
            "results"
        ]
        assert not client.get("/api/search", params={"q": "board", "repo": "/nope", **fts}).json()[
            "results"
        ]

        doc = client.get(f"/api/documents/{hit['id']}").json()
        assert doc["content"] == "reconnect backoff schedule caps at ten seconds\n"
        assert doc["metadata"] == {"owner": "omid"}
        assert client.get("/api/documents/nope").status_code == 404

        (repo / ".engineering/specs/new.md").write_text("# New\nfresh material here\n")
        _commit(repo, "new spec")
        rr = client.post("/api/index/rescan", params={"repo": str(repo)})
        assert rr.status_code == 200
        assert rr.json()["stats"]["inserted"] == 1
        assert client.get("/api/search", params={"q": "fresh material"}).json()["results"]


def test_search_validation(client):
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search", params={"q": "  "}).status_code == 422
    # mode=vector is refused only where embeddings are unavailable; with the
    # 'vector' extra installed it is a legitimate request.
    available = client.get("/api/health").json()["index"]["embeddings"]["available"]
    vector_status = client.get("/api/search", params={"q": "x", "mode": "vector"}).status_code
    assert vector_status == (200 if available else 422)
    assert client.get("/api/search", params={"q": "x", "mode": "nope"}).status_code == 422
    # Kraft-bj9.4: an unparseable query is retried as a literal phrase, not a 422
    r = client.get("/api/search", params={"q": '"unterminated'})
    assert r.status_code == 200
    assert r.json()["results"] == []
    r = client.get("/api/search", params={"q": "effort-4a"})
    assert r.status_code == 200


def test_rescan_unknown_repo_404(client):
    assert client.post("/api/index/rescan", params={"repo": "/not/known"}).status_code == 404
    # no repo arg -> rescan all known (none) -> zeroed stats
    r = client.post("/api/index/rescan")
    assert r.status_code == 200
    assert r.json() == {
        "repo": None,
        "stats": {"inserted": 0, "updated": 0, "renamed": 0, "deleted": 0},
    }


def test_health_has_index_block(client):
    h = client.get("/api/health").json()
    assert set(h["index"]) == {
        "last_scan_at",
        "repos_scanned",
        "documents",
        "errors",
        "embeddings",
    }
    emb = h["index"]["embeddings"]
    assert set(emb) == {"available", "model", "chunks", "reason"}
    assert isinstance(emb["available"], bool)


def test_work_item_documents_endpoint(tmp_path, monkeypatch):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    with _indexing(tmp_path, monkeypatch, repo) as client:
        assert client.get("/api/work-items/nope/documents").status_code == 404
        wid = client.post("/api/work-items", json={"title": "t", "repo": str(repo)}).json()["id"]
        r = client.get(f"/api/work-items/{wid}/documents")
        assert r.status_code == 200
        assert r.json() == {"work_item_id": wid, "documents": []}


def test_search_modes_without_embeddings(tmp_path, monkeypatch):
    """4C: hybrid is the default and degrades to fts; explicit vector 422s.

    Forces `Embedder.available()` False rather than relying on `fastembed`
    being absent from the venv: the old version branched on that at runtime,
    so it asserted a different contract (and skipped the vector-422 checks
    entirely) on a machine with the `vector` extra installed (Kraft-k6sm).
    """
    monkeypatch.setattr("kraft.index.embed.Embedder.available", lambda self: False)
    repo = make_repo_with_engineering(
        tmp_path, {".engineering/specs/ws.md": "# WS\nreconnect backoff schedule\n"}
    )
    with _indexing(tmp_path, monkeypatch, repo) as client:
        assert client.get("/api/health").json()["index"]["embeddings"]["available"] is False

        # no mode -> hybrid requested, degrades to fts without an embedder
        body = client.get("/api/search", params={"q": "reconnect"}).json()
        assert body["mode"] == "fts"
        assert [h["path"] for h in body["results"]] == [".engineering/specs/ws.md"]

        r = client.get("/api/search", params={"q": "reconnect", "mode": "hybrid"})
        assert r.status_code == 200

        r = client.get("/api/search", params={"q": "reconnect", "mode": "nonsense"})
        assert r.status_code == 422

        r = client.get("/api/search", params={"q": "reconnect", "mode": "vector"})
        assert r.status_code == 422
        assert "vector" in r.json()["detail"]


def test_connecting_a_repo_indexes_it_immediately(client, tmp_path):
    """Kraft-38w: connect a repo through Settings and its documents are
    searchable at once — no work item for it, no restart."""
    repo = make_repo_with_engineering(
        tmp_path, {".engineering/specs/keel.md": "# Keel\nlaminated oak keel\n"}, "keel"
    )
    assert not client.get("/api/search", params={"q": "laminated oak", "mode": "fts"}).json()[
        "results"
    ]
    assert client.post("/api/index/rescan", params={"repo": str(repo)}).status_code == 404

    assert client.post("/api/repos", json={"path": str(repo)}).status_code == 201

    assert client.post("/api/index/rescan", params={"repo": str(repo)}).status_code == 200
    hits = client.get("/api/search", params={"q": "laminated oak", "mode": "fts"}).json()["results"]
    assert [h["path"] for h in hits] == [".engineering/specs/keel.md"]

    # Disconnecting takes its documents back out of search.
    assert client.delete("/api/repos", params={"path": str(repo)}).status_code == 204
    assert not client.get("/api/search", params={"q": "laminated oak", "mode": "fts"}).json()[
        "results"
    ]


def test_the_bead_strip_searches_connected_repos_when_no_override(client, tmp_path, monkeypatch):
    """Kraft-ibwj: with no KRAFT_BD_CWD the strip searched the daemon's cwd and
    was permanently empty. It degrades quietly — `beads.search` answers [] on
    any failure — so nobody filed it; it just never worked."""
    one = isolated_bd(tmp_path, name="alpha")
    two = isolated_bd(tmp_path, name="beta")
    for repo, title in ((one, "caulk the alpha transom"), (two, "caulk the beta transom")):
        asyncio.run(beads.intake(title, description="x", cwd=str(repo)))
    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)
    for repo in (one, two):
        assert client.post("/api/repos", json={"path": str(repo)}).status_code == 201
    hits = client.get("/api/beads/search", params={"q": "caulk the", "limit": 10}).json()["beads"]
    assert {h["title"] for h in hits} == {"caulk the alpha transom", "caulk the beta transom"}
