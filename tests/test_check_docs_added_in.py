"""`dev/check_docs_added_in.py`: what is new since the last release carries a note.

The lint job runs it over the repository. These pin the checker itself on made-up
pages and a made-up release, so a loosened rule cannot turn that step into a pass
that checks nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from support.harness import commit_all, git, make_repo, write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_docs_added_in.py"

_FRONT = "---\ntitle: T\ndescription: D\n---\n\n"
_OLD = "3.guides/1.thing.md"
_BASE = {_OLD: _FRONT + "Intro.\n\n## Old part\n\nText.\n\n### Old detail\n"}
NOTE = "_Added in next_"


@pytest.fixture(scope="module")
def ca():
    spec = importlib.util.spec_from_file_location("_dev_check_docs_added_in", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # a dataclass finds its module by name
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def problems(ca, tmp_path):
    """Every finding for `cur` against the 2.0.0 pages, with the given redirects."""

    def run(cur, *, base=_BASE, redirects="", tags=("v2.0.0", "v2.1.0")):
        (tmp_path / "redirects.yml").write_text(redirects)
        entries, _ = ca.read_map(tmp_path / "redirects.yml")
        pages = ca.load(cur)
        return ca.check_notes(pages, set(tags), None) + ca.check_new(
            ca.load(base), pages, entries, "v2.0.0"
        )

    return run


def _page(body, name=_OLD, front=_FRONT):
    return {name: front + body}


_KEPT = "Intro.\n\n## Old part\n\nText.\n\n### Old detail\n\n"


@pytest.mark.parametrize(
    "cur",
    [
        _page(_KEPT),
        _page(_KEPT + f"## New part\n\n{NOTE}\n\nBody.\n"),
        # A note may follow a comment, and a table cell may end in one.
        _page(_KEPT + f"## New part\n\n<!-- aside -->\n\n{NOTE}\n"),
        _page(_KEPT + f"| Verb | Does |\n|---|---|\n| `x` | Does it. {NOTE} |\n"),
        # A note covers the sections below it, not only its own heading.
        _page(_KEPT + f"## New part\n\n{NOTE}\n\n### Sub one\n\n#### Deeper\n"),
        # A fenced block is no heading and no note.
        _page(_KEPT + f"```md\n## Not a heading\nSee {NOTE} here\n```\n"),
        # An old heading under a new name says so.
        _page(_KEPT + "## New name\n\n<!-- renamed from: Old part -->\n"),
        _page(_KEPT + "## Reworded\n\n<!-- not new: prose only -->\n"),
        # A new page is covered by its title note, sections included.
        {**_page(_KEPT), "3.guides/2.fresh.md": _FRONT + f"{NOTE}\n\nIntro.\n\n## A\n\n### B\n"},
        {**_page(_KEPT), "3.guides/2.fresh.md": _FRONT + "# Fresh\n\n" + f"{NOTE}\n\n## A\n"},
        # A page split from an old one: its old headings are not new.
        {
            **_page(_KEPT),
            "3.guides/2.half.md": _FRONT
            + "<!-- renamed from: /guides/thing -->\n\n## Old part\n\n### Old detail\n",
        },
        {**_page(_KEPT), "3.guides/2.half.md": _FRONT + "<!-- not new: moved text -->\n\n## Z\n"},
        # A numbered note is a real minor.
        _page(_KEPT + "## New part\n\n_Added in 2.1_\n"),
    ],
    ids=[
        "unchanged",
        "noted-heading",
        "note-after-comment",
        "cell-note",
        "note-covers-sections",
        "fenced",
        "renamed",
        "not-new",
        "new-page",
        "new-page-with-title",
        "split-page",
        "not-new-page",
        "numbered",
    ],
)
def test_a_sound_page_passes(problems, cur):
    assert problems(cur) == []


@pytest.mark.parametrize(
    ("cur", "redirects", "expected"),
    [
        (_page(_KEPT + "## New part\n"), "", "`New part` is new since v2.0.0"),
        (_page(_KEPT + "## New part\n\nText.\n\n" + NOTE + "\n"), "", "directly under a heading"),
        (_page(_KEPT + f"## New part {NOTE}\n"), "", "a note in a heading's text"),
        (_page(_KEPT + f"Text with {NOTE} inside.\n"), "", "is not a note"),
        (_page(_KEPT + "## N\n\n_Added in 2.1.0_\n"), "", "is not a note"),
        (_page(_KEPT + "## N\n\n*Added in next*\n"), "", "is not a note"),
        (_page(_KEPT + "## N\n\n_Added in 2.2_\n"), "", "there is no release v2.2.0"),
        (_page(_KEPT + "## N\n\n_Added in 2.0_\n"), "", "a major release"),
        # A `next` note on what the release already had: the stamp was missed.
        (_page(_KEPT.replace("## Old part\n", f"## Old part\n\n{NOTE}\n")), "", "already has"),
        (_page(f"{NOTE}\n\n" + _KEPT), "", "on a page v2.0.0 already has"),
        # A hatch must name something the release had.
        (_page(_KEPT + "## N\n\n<!-- renamed from: Never was -->\n"), "", "names nothing"),
        (
            _page(_KEPT + "## N\n\n<!-- renamed from: /guides/gone#Old part -->\n"),
            "",
            "names nothing",
        ),
        ({**_page(_KEPT), "3.guides/2.fresh.md": _FRONT + "Intro.\n"}, "", "a page v2.0.0 did not"),
        (
            {
                **_page(_KEPT),
                "3.guides/2.half.md": _FRONT + "<!-- renamed from: /guides/gone -->\n",
            },
            "",
            "names nothing",
        ),
        # A hatch cannot be borrowed for a heading the old page never had.
        (
            {
                **_page(_KEPT),
                "3.guides/2.half.md": _FRONT
                + "<!-- renamed from: /guides/thing -->\n\n## Brand new\n",
            },
            "",
            "`Brand new` is new",
        ),
    ],
    ids=[
        "new-heading",
        "note-below-a-paragraph",
        "note-in-heading",
        "note-mid-sentence",
        "three-part-version",
        "star-emphasis",
        "no-such-release",
        "major",
        "missed-stamp-heading",
        "missed-stamp-page",
        "hatch-names-nothing",
        "hatch-names-no-page-heading",
        "new-page-without-note",
        "page-hatch-names-no-page",
        "page-hatch-covers-only-old-headings",
    ],
)
def test_a_missing_or_wrong_note_is_reported(problems, cur, redirects, expected):
    found = problems(cur, redirects=redirects)
    assert any(expected in p for p in found), found


@pytest.mark.parametrize(
    ("redirects", "cur", "base"),
    [
        # A page that moved keeps its headings, and is no new page.
        ("/guides/thing: /guides/moved\n", {"3.guides/2.moved.md": _BASE[_OLD]}, _BASE),
        # A section that moved to another page is no new heading there.
        (
            "/guides/thing#old-part: /guides/other#kept\n",
            {_OLD: _BASE[_OLD], "3.guides/2.other.md": _FRONT + "Intro.\n\n## Kept\n"},
            {**_BASE, "3.guides/2.other.md": _FRONT + "Intro.\n"},
        ),
    ],
    ids=["moved-page", "moved-section"],
)
def test_what_redirects_forwards_is_not_new(problems, redirects, cur, base):
    found = problems(cur, redirects=redirects, base=base)
    assert [p for p in found if "is new" in p or "did not have" in p] == []


def test_a_note_on_a_moved_page_is_a_missed_stamp(problems):
    # The stamp is how a `next` note stops being one; a moved page is not new.
    cur = {"3.guides/2.moved.md": _FRONT + f"{NOTE}\n\n" + _BASE[_OLD].split("\n\n", 1)[1]}
    found = problems(cur, redirects="/guides/thing: /guides/moved\n")
    assert any("already has" in p for p in found), found


def _release_repo(tmp_path, monkeypatch, ca, content):
    repo = make_repo(tmp_path)
    write(repo, "docsite/content/3.guides/1.thing.md", _BASE[_OLD])
    commit_all(repo, "release 2.0.0")
    git(repo, "tag", "v2.0.0")
    git(repo, "tag", "v2.1.0rc1")
    for name, text in content.items():
        write(repo, f"docsite/content/{name}", text)
    monkeypatch.setattr(ca, "ROOT", repo)
    monkeypatch.setattr(ca, "CONTENT", repo / "docsite" / "content")
    return repo


@pytest.mark.parametrize(
    ("content", "code", "printed"),
    [
        ({"3.guides/1.thing.md": _BASE[_OLD]}, 0, "ok:"),
        ({"3.guides/1.thing.md": _BASE[_OLD] + "\n## New\n"}, 1, "`New` is new since v2.0.0"),
        ({"3.guides/1.thing.md": _BASE[_OLD] + f"\n## New\n\n{NOTE}\n"}, 0, "ok:"),
    ],
    ids=["unchanged", "new-heading", "noted"],
)
def test_main_compares_this_checkout_with_the_latest_release_tag(
    ca, tmp_path, monkeypatch, capsys, content, code, printed
):
    # The rc tag is not a release: the base stays v2.0.0.
    _release_repo(tmp_path, monkeypatch, ca, content)
    assert ca.main([]) == code
    assert printed in capsys.readouterr().out


def test_main_refuses_a_clone_without_tags(ca, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(ca, "ROOT", repo)
    assert ca.main([]) == 1
    assert "git fetch --tags" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("note", "expected"),
    [("2.3", "no release v2.3.0"), ("2.2", "v2.2.0 is newer than this content's own v2.1.0")],
    ids=["no-such-release", "newer-than-its-own-release"],
)
def test_main_ref_checks_a_tags_own_notes(ca, tmp_path, monkeypatch, capsys, note, expected):
    repo = _release_repo(tmp_path, monkeypatch, ca, {})
    write(
        repo, "docsite/content/3.guides/1.thing.md", _BASE[_OLD] + f"\n## N\n\n_Added in {note}_\n"
    )
    commit_all(repo, "x")
    git(repo, "tag", "v2.1.0")
    commit_all(repo, "later")
    git(repo, "tag", "v2.2.0")
    assert ca.main(["--ref", "v2.1.0"]) == 1
    assert expected in capsys.readouterr().out
