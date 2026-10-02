"""`/docs` and `/redoc` load their scripts from a CDN onto Kraft's own origin,
with no login, so each one is pinned to an exact release and checked by hash."""

from __future__ import annotations

import re

import pytest

from kraft.api import apidocs


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_every_cdn_asset_is_pinned_and_checked_by_hash(client, path):
    """A tag the browser loads from another origin with no integrity runs
    whatever that origin serves today: `swagger-ui-dist@5` is the newest 5.x,
    and a bad publish of it would hold the whole API. Each `src` or `href` on
    the page is one exact release, with the hash written in `apidocs`."""
    r = client.get(path)
    assert r.status_code == 200, r.text
    tags = re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)="https://cdn[^>]*>', r.text)
    assert tags, r.text
    for tag in tags:
        url = re.search(r'(?:src|href)="([^"]+)"', tag).group(1)
        assert re.search(r"@\d+\.\d+\.\d+/", url), url
        assert f'integrity="{apidocs.INTEGRITY[url]}"' in tag, tag
        assert 'crossorigin="anonymous"' in tag, tag
    assert "fonts.googleapis.com" not in r.text
    assert "fastapi.tiangolo.com" not in r.text
    assert '<link rel="shortcut icon" href="/icon.svg">' in r.text
