import pytest


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A built SPA (`index.html` and one asset), pinned as `KRAFT_FRONTEND_DIST`.
    List it before `client`: the app reads it at startup."""
    dist = tmp_path / "fe-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>kraft</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    return dist
