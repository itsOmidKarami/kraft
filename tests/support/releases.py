"""A releases-feed entry, as the GitHub list of releases carries one."""

from __future__ import annotations


def release_entry(tag: str, *, prerelease: bool = False) -> dict:
    """`tag` published with a wheel, so `kraft.update` counts it as installable."""
    wheel = {"name": f"kraft-{tag}.whl", "browser_download_url": f"https://x/{tag}.whl"}
    return {"tag_name": tag, "draft": False, "prerelease": prerelease, "assets": [wheel]}
