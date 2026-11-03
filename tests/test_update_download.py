"""`update.download`: a release wheel streamed to a file with a real timeout."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from kraft import update


def test_download_streams_to_the_file(tmp_path: Path, monkeypatch) -> None:
    seen: dict = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def iter_bytes(self):
            yield b"ab"
            yield b"cd"

    @contextmanager
    def stream(method, url, **kw):
        seen.update(kw, url=url)
        yield Response()

    monkeypatch.setattr(httpx, "stream", stream)
    dest = update.download("https://x/k.whl", tmp_path / "k.whl")
    assert dest.read_bytes() == b"abcd"
    assert seen["timeout"] == 120.0
    assert seen["follow_redirects"] is True  # GitHub asset URLs redirect


def test_download_raises_on_a_4xx(tmp_path: Path, monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("404", request=None, response=None)

    @contextmanager
    def stream(method, url, **kw):
        yield Response()

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(httpx.HTTPError):
        update.download("https://x/k.whl", tmp_path / "k.whl")
