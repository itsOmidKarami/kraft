from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo_with_engineering

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, index_repos: str | None = None):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setenv("KRAFT_INDEX_REPOS", index_repos or "")
    import kraft.api as api

    return TestClient(api.app)


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
    with _client(tmp_path, monkeypatch, index_repos=str(repo)) as client:
        body = client.get("/search", params={"q": "reconnect backoff"}).json()
        assert body["mode"] == "fts"
        assert [h["path"] for h in body["results"]] == [".engineering/specs/ws.md"]
        hit = body["results"][0]
        assert hit["title"] == "WS transport"
        assert hit["kind"] == "specs"
        assert "[reconnect]" in hit["snippet"]
        assert hit["links"] == []

        assert client.get("/search", params={"q": "board", "kind": "plans"}).json()["results"]
        assert not client.get("/search", params={"q": "board", "kind": "specs"}).json()["results"]
        assert not client.get("/search", params={"q": "board", "repo": "/nope"}).json()["results"]

        doc = client.get(f"/documents/{hit['id']}").json()
        assert doc["content"] == "reconnect backoff schedule caps at ten seconds\n"
        assert doc["metadata"] == {"owner": "omid"}
        assert client.get("/documents/nope").status_code == 404

        (repo / ".engineering/specs/new.md").write_text("# New\nfresh material here\n")
        _commit(repo, "new spec")
        rr = client.post("/index/rescan", params={"repo": str(repo)})
        assert rr.status_code == 200
        assert rr.json()["stats"]["inserted"] == 1
        assert client.get("/search", params={"q": "fresh material"}).json()["results"]


def test_search_validation(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/search", params={"q": ""}).status_code == 422
        assert client.get("/search").status_code == 422
        assert client.get("/search", params={"q": "  "}).status_code == 422
        assert client.get("/search", params={"q": "x", "mode": "vector"}).status_code == 422
        assert client.get("/search", params={"q": '"unterminated'}).status_code == 422


def test_rescan_unknown_repo_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/index/rescan", params={"repo": "/not/known"}).status_code == 404
        # no repo arg -> rescan all known (none) -> zeroed stats
        r = client.post("/index/rescan")
        assert r.status_code == 200
        assert r.json() == {
            "repo": None,
            "stats": {"inserted": 0, "updated": 0, "renamed": 0, "deleted": 0},
        }


def test_health_has_index_block(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        h = client.get("/health").json()
        assert set(h["index"]) == {"last_scan_at", "repos_scanned", "documents", "errors"}
