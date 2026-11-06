"""The launch context carries `storage.outputs.max_size` to a dispatch."""

from __future__ import annotations

from types import SimpleNamespace

from kraft.api import deps


def test_launch_carries_the_outputs_limit(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    policy = SimpleNamespace(outputs_max_bytes=10 << 20)
    st = SimpleNamespace(templates_dir=templates_dir, skills_dir=None, policy=policy)

    assert deps.launch(st, "/work/repo").outputs_max_bytes == 10 << 20
