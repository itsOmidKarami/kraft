"""The launch context carries `storage.outputs.max_size` to a dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kraft.api import deps


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (SimpleNamespace(outputs_max_bytes=10 << 20), 10 << 20),
        (None, 64 << 20),
    ],
    ids=["the-policy-s-limit", "no-policy-loaded"],
)
def test_launch_carries_the_outputs_limit(tmp_path, policy, expected):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    st = SimpleNamespace(templates_dir=templates_dir, skills_dir=None, policy=policy)

    assert deps.launch(st, "/work/repo").outputs_max_bytes == expected
