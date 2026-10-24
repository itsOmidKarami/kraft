"""Where a unix socket lives when its path is too long for `sun_path`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kraft.remote import sockets


@pytest.mark.parametrize(
    "path, moved",
    [
        (Path("/tmp/kraft/remote/machine.sock"), False),
        (Path("/tmp") / ("d" * 120) / "w.sock", True),
    ],
    ids=["fits", "too-long"],
)
def test_short_socket_path(path, moved):
    result = sockets.short_socket_path(path)
    assert result.name == path.name
    if not moved:
        assert result == path
    else:
        assert result.parent.parent == Path("/tmp")
        assert result.parent.name.startswith(f"kraft-{os.getuid()}-")
        assert len(os.fsencode(result)) < 100
