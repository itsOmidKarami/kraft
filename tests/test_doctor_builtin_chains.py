"""doctor judges the built-in's chains only when a local chain extends them."""

from __future__ import annotations

import pytest
import yaml

from kraft import doctor


def _codex_only(tmp_path, monkeypatch, local_default: dict | None):
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text(
        yaml.safe_dump({"tasks": {"t": {"kind": "agent", "harness": "b", "prompt": "p"}}})
    )
    node = {"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "t"}]}
    (live / "chains" / "mine.yaml").write_text(yaml.safe_dump({"id": "mine", "nodes": [node]}))
    if local_default is not None:
        (live / "chains" / "default.yaml").write_text(yaml.safe_dump(local_default))
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": {"b": {"provider": "codex"}}})
    )
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(live))


@pytest.mark.parametrize(
    ("local_default", "checked"),
    [
        (None, False),
        ({"id": "default", "nodes": [{"id": "n", "kind": "exec", "tasks": []}]}, False),
        ({"id": "default", "extends": "kraft-default:default"}, True),
    ],
    ids=["no-local-default", "default-that-does-not-extend", "default-that-extends"],
)
def test_a_codex_only_home_is_not_judged_on_chains_it_never_runs(
    tmp_path, monkeypatch, local_default, checked
):
    _codex_only(tmp_path, monkeypatch, local_default)
    names = {r["name"] for r in doctor._agent_checks()}
    assert ("agent: claude" in names) is checked


def test_a_repo_naming_a_builtin_chain_as_its_default_has_it_checked(tmp_path, monkeypatch):
    _codex_only(tmp_path, monkeypatch, None)
    live = tmp_path / "templates"
    entry = {"id": "r", "path": "/x", "default_chain": "kraft-default:default"}
    (live / "repos.yaml").write_text(yaml.safe_dump({"repos": [entry]}))
    assert "agent: claude" in {r["name"] for r in doctor._agent_checks()}
