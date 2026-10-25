"""`machine.yaml`: the limit a machine sets for itself."""

from __future__ import annotations

import pytest

from kraft.config import ConfigError
from kraft.remote import machine_config


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", None),
        ("other: 1\n", None),
        ("capacity: 3\n", 3),
    ],
    ids=["no-file", "empty", "no-key", "set"],
)
def test_the_limit_is_what_the_file_says_or_none(tmp_path, text, expected):
    path = tmp_path / "machine.yaml"
    if text is not None:
        path.write_text(text)
    assert machine_config.capacity(path) == expected


@pytest.mark.parametrize("value", ["0", "x", "true", "1.5"], ids=["zero", "word", "bool", "float"])
def test_a_limit_that_is_not_a_whole_number_is_refused_naming_the_file(tmp_path, value):
    path = tmp_path / "machine.yaml"
    path.write_text(f"capacity: {value}\n")
    with pytest.raises(ConfigError, match="machine.yaml"):
        machine_config.capacity(path)


def test_a_broken_file_reads_as_no_limit(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "machine.yaml").write_text("capacity: 0\n")
    assert machine_config.limit() is None
    assert "machine.yaml" in caplog.text
    (tmp_path / "machine.yaml").write_text("capacity: 2\n")
    assert machine_config.limit() == 2
