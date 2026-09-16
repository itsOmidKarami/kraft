"""Every `origin:` line in the intent tree names a file that exists.

The intent parser treats `origin` as free text and never checks it, so a
citation can rot silently. After the GitHub move the design record it used to
point at is private, and a reader following one of those lines finds nothing.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _origins():
    for path in (ROOT / "docs" / "intent").rglob("*.md"):
        for line in path.read_text().splitlines():
            if line.startswith("origin:"):
                # "path §7" and "path §7.1" both appear; the file is the first token.
                yield path, line.removeprefix("origin:").strip().split(" ")[0]


def test_there_are_origins_to_check():
    assert list(_origins())


def test_every_origin_resolves():
    missing = [
        f"{path.name}: {target}" for path, target in _origins() if not (ROOT / target).exists()
    ]
    assert not missing, "origin lines pointing at nothing: " + ", ".join(missing)
