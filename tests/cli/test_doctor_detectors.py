"""`kraft admin doctor`'s detectors.yaml row: an operator's detector table,
read as a probe reads it. Split from test_doctor.py for its line budget."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from kraft import client, doctor


def _by_name(rows, name):
    return next(row for row in rows if row["name"] == name)


@pytest.mark.parametrize(
    ("text", "ok", "said"),
    [
        (None, True, "not present"),
        ("detectors:\n  - {id: earthly, tier: runner, files: [Earthfile]}\n", True, "1 detector"),
        ("detectorz: []\n", False, "detectors.yaml"),
    ],
    ids=["absent", "valid", "broken"],
)
def test_doctor_reads_the_operators_detectors_file(app, tmp_path, text, ok, said):
    asyncio.run(client.health())  # the app's lifespan makes the run dir
    templates = Path(os.environ["KRAFT_CONFIG_DIR"])
    if text is not None:
        (templates / "detectors.yaml").write_text(text)
    row = _by_name(asyncio.run(doctor.run_checks()), "detectors.yaml")
    assert (row["ok"], said in row["detail"]) == (ok, True), row
