"""All three LICENSE files carry the same Apache-2.0 text.

A relicense that misses a copy is how a repository ends up claiming two
licenses at once, and nothing else in the suite would notice.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSES = (
    ROOT / "LICENSE",
    ROOT / "plugins" / "kraft" / "LICENSE",
    ROOT / "plugins" / "kraft-lite" / "LICENSE",
)


def test_every_license_is_apache_2():
    for path in LICENSES:
        assert path.is_file(), f"{path} is missing"
        text = path.read_text()
        assert "Apache License" in text
        assert "Version 2.0" in text
        assert "Copyright 2026 Omid Karami" in text


def test_the_copies_are_identical():
    assert len({path.read_text() for path in LICENSES}) == 1
