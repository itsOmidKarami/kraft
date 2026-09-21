import pytest


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A built SPA (`index.html` and one asset), pinned as `KRAFT_FRONTEND_DIST`.
    `client` pulls this fixture itself (via `request.getfixturevalue`) before its
    lifespan starts if a test lists both, so argument order between `client` and
    `dist` no longer matters."""
    dist = tmp_path / "fe-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>kraft</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    return dist
