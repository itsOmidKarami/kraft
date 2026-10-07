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

# Served at /guides/vscode (anchors: vs-code, install, _2-connect,
# without-json, yes, install-1), /get-started/install (upgrading) and
# /get-started.
_PAGES = {
    "3.guides/07.vscode.md": (
        "---\ntitle: VS Code\n# not a heading: front matter\n---\n\n"
        "# VS Code\n\n## Install\n\n## 2. Connect\n\n## Without `--json`\n\n## `--yes`\n\n"
        "```bash\n# Not a heading: fenced\n```\n\n"
        "~~~\n# Not a heading: tilde fence\n~~~\n\n"
        "````md\n```\n# Not a heading: still inside four backticks\n```\n````\n\n"
        "### Install\n"
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
    # collapses or is trimmed, and a `#` line in front matter or inside a code
    # fence (backticks, tildes, or a longer fence holding a shorter one) is not
    # a heading.
    assert cr.pages(content)["/guides/vscode"] == {
        "vs-code",
        "install",
        "_2-connect",
        "without-json",
        "yes",
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
        # A heading that starts with a digit has the underscore the site gives it.
        "/guides/vscode#_5-check-it: /guides/vscode#install\n",
    ],
    ids=[
        "empty",
        "comment",
        "page",
        "page-to-section",
        "section",
        "section-of-moved-page",
        "source-anchor-of-a-digit-heading",
    ],
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
        ("/robots.txt: /guides/vscode\n", "redirects.yml:1: not an entry"),
        ("/guides/old: /guides/vscode.md\n", "redirects.yml:1: not an entry"),
        (
            "/guides/vscode#5-check-it: /guides/vscode#install\n",
            "/guides/vscode#5-check-it has an anchor the site never generates",
        ),
        (
            "/guides/vscode#a--b: /guides/vscode#install\n",
            "/guides/vscode#a--b has an anchor the site never generates",
        ),
        (
            "/guides/vscode#a-: /guides/vscode#install\n",
            "/guides/vscode#a- has an anchor the site never generates",
        ),
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
        "source-is-a-file",
        "target-is-a-file",
        "source-anchor-without-underscore",
        "source-anchor-double-hyphen",
        "source-anchor-trailing-hyphen",
    ],
)
def test_a_bad_entry_is_reported(cr, content, map_text, expected):
    problems = _problems(cr, content, map_text)
    assert len(problems) == 1
    assert expected in problems[0]


def _page_problems(cr, content: Path, map_text: str, text: str) -> list[str]:
    (content / cr.MAP_NAME).write_text(map_text)
    write(content, "9.links/1.page.md", f"## Here\n\n{text}\n")
    site = cr.pages(content)
    return cr.check_page_links(content, cr.read_map(content / cr.MAP_NAME)[0], site)


@pytest.mark.parametrize(
    ("map_text", "text"),
    [
        ("", "[a](/guides/vscode#install)"),
        ("", "[a](/guides/vscode#_2-connect) and [b](/get-started/)"),
        ("", "[a](#here)"),
        ("", "![a](/assets/shot.png) and [b](/diagrams/x.svg) and [c](https://x.dev/y#z)"),
        # The map forwards only an address that is no longer a page.
        ("/guides/old: /guides/vscode\n", "[a](/guides/vscode#install)"),
    ],
    ids=["heading", "digit-heading", "in-page", "not-a-page", "map-leaves-a-live-page"],
)
def test_a_link_between_pages_that_lands_passes(cr, content, map_text, text):
    assert _page_problems(cr, content, map_text, text) == []


@pytest.mark.parametrize(
    ("map_text", "text", "expected"),
    [
        ("", "[a](/guides/vscode#nope)", "9.links/1.page.md:3: /guides/vscode#nope is not a page"),
        ("", "[a](/guides/gone)", "9.links/1.page.md:3: /guides/gone is not a page"),
        ("", "[a](/guides/vscode#2-connect)", "/guides/vscode#2-connect is not a page"),
        ("", "[a](#nope)", "#nope is not a page and heading"),
        (
            "/guides/old: /guides/vscode\n",
            "[a](/guides/old#install)",
            "/guides/old#install is forwarded by redirects.yml: write ](/guides/vscode#install)",
        ),
        (
            "/get-started/install#moved: /guides/vscode#install\n",
            "[a](/get-started/install#moved)",
            "write ](/guides/vscode#install)",
        ),
    ],
    ids=["no-heading", "no-page", "digit-without-underscore", "in-page", "forwarded", "section"],
)
def test_a_link_between_pages_that_lands_nowhere_or_is_forwarded_is_reported(
    cr, content, map_text, text, expected
):
    problems = _page_problems(cr, content, map_text, text)
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
        # The entry names the section; the anchor the reader came with is dropped.
        ("/guides/old: /guides/vscode#install\n", "/guides/old#gone"),
        ("/get-started/install#moved: /guides/vscode#install\n", "/get-started/install#moved"),
        (
            "/guides/old: /guides/vscode\n/guides/old#moved: /get-started/install#upgrading\n",
            "/guides/old#moved",
        ),
    ],
    ids=["page", "page-keeps-anchor", "page-to-section", "section", "section-of-moved-page"],
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
        ("itsOmidKarami.github.io/Kraft/guides/vscode", "/guides/vscode"),
    ],
    ids=["home", "page", "trailing-slash", "full-stop", "next", "next-home", "not-next", "case"],
)
def test_site_path_drops_the_base_and_what_is_not_address(cr, url, expected):
    assert cr.site_path(url) == expected


def test_kraft_links_finds_the_links_in_the_repository(cr):
    # README.md, install.sh and src/kraft/cli/admin.py carry these, the last
    # with its anchor; ARCHITECTURE.md, outside any source folder, the fourth.
    assert {
        "/",
        "/get-started/install",
        "/get-started/install#connect-your-agent",
        "/concepts/how-a-work-item-runs",
    } <= set(cr.kraft_links(cr.ROOT))


def test_check_reads_the_map_and_the_links_of_one_repository(cr, content, tmp_path):
    write(content, cr.MAP_NAME, "/guides/old: /guides/gone\n")
    # A dead anchor on a live page, in a file outside any source folder and
    # with the host in another case; the tests' and the docs' own URLs are not
    # Kraft's links.
    write(
        tmp_path, "SECURITY.md", "https://itsOmidKarami.github.io/kraft/next/guides/vscode#nope\n"
    )
    write(tmp_path, "src/kraft/cli.py", "# https://itsomidkarami.github.io/kraft/guides/vscode\n")
    write(tmp_path, "tests/test_x.py", "# https://itsomidkarami.github.io/kraft/a/fixture\n")
    write(content, "index.md", "https://itsomidkarami.github.io/kraft/lychee/checks/this\n")
    git(tmp_path, "init", "-q")
    commit_all(tmp_path)
    map_problem, link_problem = cr.check(tmp_path)
    assert "/guides/old forwards to /guides/gone" in map_problem
    assert "Kraft links to /guides/vscode#nope" in link_problem


def test_finding_no_links_at_all_is_a_failure(cr, content, tmp_path):
    # Outside a repository the grep finds nothing, which must not read as
    # "Kraft links to nothing broken".
    assert cr.check(tmp_path) == ["found no docs links anywhere in the repository"]


def test_the_repository_passes(cr):
    assert cr.check(cr.ROOT) == []
