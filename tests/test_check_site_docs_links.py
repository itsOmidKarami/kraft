"""`dev/check_site_docs_links.py`: a marketing page's docs links exist in the
release the site's root serves.

The docs workflow runs it against the real tags. These pin the checker itself,
so a loosened pattern or rule cannot turn that step into a pass that checks
nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_site_docs_links.py"
sys.path.insert(0, str(_SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("check_site_docs_links", _SCRIPT)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

_SITE = {"/guides/old-name": set(), "/project/security": {"agents"}, "/why": set()}


@pytest.mark.parametrize(
    "address, dead",
    [
        pytest.param("/project/security", False, id="page"),
        pytest.param("/project/security#agents", False, id="heading"),
        pytest.param("/why", False, id="marketing-page"),
        pytest.param("/guides/moved", False, id="forwarded-by-the-map"),
        pytest.param("/guides/split-work", True, id="page-newer-than-the-release"),
        pytest.param("/project/security#gates", True, id="heading-newer-than-the-release"),
    ],
)
def test_a_link_is_checked_against_the_release(address, dead):
    problems = checker.check(
        [("Page.vue:3", address)], _SITE, {"/guides/moved": "/guides/old-name"}, "v9.0.0"
    )
    assert bool(problems) is dead
    if dead:
        assert problems[0].startswith(f"Page.vue:3: {address} ") and "v9.0.0" in problems[0]


def test_docs_links_reads_paths_and_skips_files(tmp_path):
    page = tmp_path / "Page.vue"
    page.write_text(
        '<SiteArrowLink to="/guides/a">\n'
        "docs: { to: '/project/b#c' }\n"
        "const INSTALL = `/get-started/install/`\n"
        '<img src="/site/board.png"> <a href="/demo/"> <img src="/assets/x.png">\n'
        'class="s-wrap" d="M4 3h3"\n',
        encoding="utf-8",
    )
    assert checker.docs_links([page], tmp_path) == [
        ("Page.vue:1", "/guides/a"),
        ("Page.vue:2", "/project/b#c"),
        ("Page.vue:3", "/get-started/install"),
    ]
