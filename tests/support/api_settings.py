"""Shared test seam for the split `test_api_settings_*.py` and
`test_api_repos.py` files: a TestClient wired to a hermetic env.

Each of those files defines its own `templates_dir`/`client` fixtures on top
of `_client` — a plain function is safe to import, but a fixture is only
visible to pytest when it lives in the test module itself or a conftest.py,
so re-exporting one by import is fragile (and reads as unused to a linter).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from support.harness import isolated_bd


def _client(tmp_path, monkeypatch, templates_dir, *, host: str | None = None):
    """`host` is what the process binds — auth follows that, not access.yaml, so a
    test about the locked-down posture has to set it before the app starts."""
    if host:
        monkeypatch.setenv("KRAFT_HOST", host)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))
