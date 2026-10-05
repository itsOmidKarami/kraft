"""`dev/check_docs_redirects.py`: a moved docs address keeps working.

The lint job runs it over the repository. These pin the checker itself on a
small made-up content tree, so a loosened rule cannot turn that step into a
pass that checks nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from support.harness import commit_all, git, write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_docs_redirects.py"

# Served at /guides/vscode (anchors: install, _2-connect, without-json,
# install-1), /get-started/install (upgrading) and /get-started.
_PAGES = {
    "3.guides/07.vscode.md": (
        "# VS Code\n\n## Install\n\n## 2. Connect\n\n## Without `--json`\n\n"
        "```bash\n# Not a heading\n```\n\n### Install\n"
    ),
    "1.get-started/1.install.md": "## Upgrading\n",
    "1.get-started/index.md": "## Start here\n",
}


@pytest.fixture(scope="module")
def cr():
    spec = importlib.util.spec_from_file_location("_dev_check_docs_redirects", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def content(tmp_path: Path) -> Path:
    root = tmp_path / "docsite" / "content"
    for name, text in _PAGES.items():
        page = root / name
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text)
    return root


def _problems(cr, content: Path, map_text: str, links: list[str] = ()) -> list[str]:
    (content / cr.MAP_NAME).write_text(map_text)
    site = cr.pages(content)
    entries, problems = cr.read_map(content / cr.MAP_NAME)
    return problems + cr.check_map(entries, site) + cr.check_links(list(links), entries, site, {})


def test_pages_are_served_at_their_path_without_number_prefixes(cr, content):
    assert set(cr.pages(content)) == {"/guides/vscode", "/get-started/install", "/get-started"}


def test_anchors_are_the_ids_the_site_gives_headings(cr, content):
    # A repeat is numbered, a leading digit gets `_`, `--` in a code span
    # collapses, and a `#` line inside a code fence is not a heading.
    assert cr.pages(content)["/guides/vscode"] == {
        "vs-code",
        "install",
        "_2-connect",
        "without-json",
        "install-1",
    }


@pytest.mark.parametrize(
    "map_text",
    [
        "",
        "# only a comment\n",
        "/guides/old: /guides/vscode\n",
        "/guides/old: /guides/vscode#install\n",
        "/get-started/install#moved: /guides/vscode#_2-connect\n",
        # A section of a page that itself moved.
        "/guides/old: /guides/vscode\n/guides/old#moved: /get-started/install#upgrading\n",
    ],
    ids=["empty", "comment", "page", "page-to-section", "section", "section-of-moved-page"],
)
def test_a_sound_map_passes(cr, content, map_text):
    links = ["/guides/vscode#install", "/get-started", "/get-started/install#upgrading"]
    assert _problems(cr, content, map_text, links) == []


def test_a_missing_map_is_an_empty_one(cr, content):
    # A release tag's content from before the map existed.
    assert cr.read_map(content / cr.MAP_NAME) == ({}, [])


@pytest.mark.parametrize(
    ("map_text", "expected"),
    [
        ("/guides/old: /guides/gone\n", "/guides/gone, which is not a page"),
        ("/guides/old: /guides/vscode#nope\n", "/guides/vscode#nope, which is not a page"),
        ("/get-started/install: /guides/vscode\n", "/get-started/install is still a real page"),
        ("/guides/old: /guides/old\n", "/guides/old forwards to itself"),
        (
            "/guides/a: /guides/b\n/guides/b: /guides/vscode\n",
            "/guides/a forwards to /guides/b, which another entry forwards again",
        ),
        (
            "/guides/a: /get-started/install#moved\n"
            "/get-started/install#moved: /guides/vscode#install\n",
            "/guides/a forwards to /get-started/install#moved, which another entry",
        ),
        (
            "/guides/gone#x: /guides/vscode#install\n",
            "/guides/gone#x is on a page that neither exists nor has an entry",
        ),
        (
            "/get-started/install#upgrading: /guides/vscode#install\n",
            "/get-started/install#upgrading is still a heading",
        ),
        ("/guides/old: /guides/vscode\n/guides/old: /get-started\n", "has two entries"),
        ("/Guides/Old: /guides/vscode\n", "redirects.yml:1: not an entry"),
        ("/guides/old/: /guides/vscode\n", "redirects.yml:1: not an entry"),
        ("/guides/old: /guides/vscode  # moved\n", "redirects.yml:1: not an entry"),
    ],
    ids=[
        "target-page-missing",
        "target-anchor-missing",
        "source-still-a-page",
        "self",
        "chain",
        "chain-through-a-section",
        "section-of-no-page",
        "section-still-a-heading",
        "duplicate",
        "upper-case",
        "trailing-slash",
        "trailing-comment",
    ],
)
def test_a_bad_entry_is_reported(cr, content, map_text, expected):
    problems = _problems(cr, content, map_text)
    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.parametrize(
    ("map_text", "link"),
    [
        ("", "/guides/gone"),
        ("", "/get-started/install#moved"),
        # The page moved, but the heading the link names did not come with it.
        ("/guides/old: /guides/vscode\n", "/guides/old#upgrading"),
    ],
    ids=["page", "anchor", "anchor-lost-in-a-move"],
)
def test_a_link_from_kraft_that_resolves_to_nothing_is_reported(cr, content, map_text, link):
    problems = _problems(cr, content, map_text, [link])
    assert len(problems) == 1
    assert f"Kraft links to {link}" in problems[0]


@pytest.mark.parametrize(
    ("map_text", "link"),
    [
        ("/guides/old: /guides/vscode\n", "/guides/old"),
        ("/guides/old: /guides/vscode\n", "/guides/old#install"),
        ("/get-started/install#moved: /guides/vscode#install\n", "/get-started/install#moved"),
        (
            "/guides/old: /guides/vscode\n/guides/old#moved: /get-started/install#upgrading\n",
            "/guides/old#moved",
        ),
    ],
    ids=["page", "page-keeps-anchor", "section", "section-of-moved-page"],
)
def test_a_link_from_kraft_that_the_map_forwards_passes(cr, content, map_text, link):
    assert _problems(cr, content, map_text, [link]) == []


def test_known_broken_excuses_a_dead_link_until_it_resolves(cr, content):
    site = cr.pages(content)
    known = {"/guides/gone": "why", "/guides/vscode": "fixed since"}
    problems = cr.check_links(["/guides/gone", "/guides/vscode"], {}, site, known)
    assert problems == ["/guides/vscode resolves now: delete it from KNOWN_BROKEN"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("itsomidkarami.github.io/kraft/", "/"),
        ("itsomidkarami.github.io/kraft/guides/vscode", "/guides/vscode"),
        ("itsomidkarami.github.io/kraft/guides/vscode/#install", "/guides/vscode#install"),
        ("itsomidkarami.github.io/kraft/guides/vscode.", "/guides/vscode"),
        ("itsomidkarami.github.io/kraft/next/guides/vscode#install", "/guides/vscode#install"),
        ("itsomidkarami.github.io/kraft/next/", "/"),
        ("itsomidkarami.github.io/kraft/nextsteps", "/nextsteps"),
    ],
    ids=["home", "page", "trailing-slash", "full-stop", "next", "next-home", "not-next"],
)
def test_site_path_drops_the_base_and_what_is_not_address(cr, url, expected):
    assert cr.site_path(url) == expected


def test_kraft_links_finds_the_links_in_the_repository(cr):
    # README.md and install.sh carry these two.
    assert {"/", "/get-started/install"} <= set(cr.kraft_links(cr.ROOT))


def test_check_reads_the_map_and_the_links_of_one_repository(cr, content, tmp_path):
    write(content, cr.MAP_NAME, "/guides/old: /guides/gone\n")
    write(tmp_path, "README.md", "https://itsomidkarami.github.io/kraft/next/guides/nope and\n")
    write(tmp_path, "src/kraft/cli.py", "# https://itsomidkarami.github.io/kraft/guides/vscode\n")
    git(tmp_path, "init", "-q")
    commit_all(tmp_path)
    map_problem, link_problem = cr.check(tmp_path)
    assert "/guides/old forwards to /guides/gone" in map_problem
    assert "Kraft links to /guides/nope" in link_problem


def test_finding_no_links_at_all_is_a_failure(cr, content, tmp_path):
    # Outside a repository the grep finds nothing, which must not read as
    # "Kraft links to nothing broken".
    assert cr.check(tmp_path) == ["found no docs links under " + ", ".join(cr.LINK_SOURCES)]


def test_the_repository_passes(cr):
    assert cr.check(cr.ROOT) == []
