from pathlib import Path

import pytest

from kraft.worker import mounts


@pytest.mark.parametrize(
    ("rw_dirs", "want"),
    [(["/d/", "/d"], 1), (["/d", "/e"], 2)],
    ids=["trailing-slash-dedupes", "distinct-kept"],
)
def test_rw_dirs_are_keyed_on_path(tmp_path, rw_dirs, want):
    ms = mounts.session_mounts(tmp_path, None, rw_dirs=rw_dirs)
    assert [m for m in ms[1:] if m.src in (Path("/d"), Path("/e"))].__len__() == want
