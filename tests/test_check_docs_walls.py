"""`dev/check_docs_walls.py`: no wall of text in a docsite page.

CI's lint job runs it over `docsite/content/`. These pin the counter itself, so
a parser that stops seeing a kind of block cannot turn that step into a pass
that checks nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_docs_walls.py"


@pytest.fixture(scope="module")
def cw():
    spec = importlib.util.spec_from_file_location("_dev_check_docs_walls", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _words(count: int) -> str:
    return " ".join(["word"] * count)


@pytest.mark.parametrize(
    ("kind", "template", "count", "is_wall"),
    [
        ("paragraph", "{}", 80, False),
        ("paragraph", "{}", 81, True),
        ("list item", "- {}", 80, False),
        ("list item", "3. {}", 81, True),
        ("table cell", "| key | {} |", 40, False),
        ("table cell", "| key | {} |", 41, True),
    ],
    ids=["paragraph-80", "paragraph-81", "item-80", "numbered-item-81", "cell-40", "cell-41"],
)
def test_a_block_is_a_wall_only_past_its_limit(cw, kind, template, count, is_wall):
    page = f"intro\n\n{template.format(_words(count))}\n"
    assert [(w.line, w.kind, w.words) for w in cw.walls(page)] == (
        [(3, kind, count)] if is_wall else []
    )


@pytest.mark.parametrize(
    ("page", "tail_line"),
    [
        (f"---\ndescription: {_words(100)}\n---\n\ntail", 5),
        (f"```text\n{_words(100)}\n```\n\ntail", 5),
        (f"## {_words(100)}\n\ntail", 3),
        (f"::u-page-hero\n---\ndescription: {_words(100)}\n---\ntail\n::", 5),
        (f":::tip{{title='{_words(100)}'}}\ntail\n:::", 2),
    ],
    ids=["front-matter", "code-fence", "heading", "component-yaml", "component-line"],
)
def test_what_is_not_prose_is_not_counted(cw, page, tail_line):
    assert cw.blocks(page) == [(tail_line, "paragraph", "tail")]


def test_a_rule_in_the_body_does_not_hide_the_prose_after_it(cw):
    # `---` opens YAML only on line 1 or under a component. Anywhere else it is
    # a horizontal rule, and reading it as an opener would skip the rest of the page.
    assert [w.line for w in cw.walls(f"intro\n\n---\n\n{_words(81)}\n")] == [5]


def test_a_list_item_is_counted_with_its_continuation_lines(cw):
    page = f"- {_words(50)}\n  {_words(31)}\n- short\n"
    assert [(w.line, w.kind, w.words) for w in cw.walls(page)] == [(1, "list item", 81)]


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("`no checkout`", []),
        (
            "`no such text`",
            [
                "{page}:1: table cell of 41 words (limit 40)",
                "page.md: ALLOWED entry '`no such text`' matches no wall;"
                " remove it from dev/check_docs_walls.py",
            ],
        ),
    ],
    ids=["entry-matches", "entry-matches-nothing"],
)
def test_an_allowed_wall_passes_and_an_entry_matching_no_wall_fails(
    cw, tmp_path, fragment, expected
):
    page = tmp_path / "page.md"
    page.write_text(f"| `no checkout` {_words(39)} | fix |\n")
    problems = cw.check([page], allowed=[("page.md", fragment)], docsite=tmp_path)
    assert problems == [line.format(page=page) for line in expected]


def test_main_checks_the_paths_it_is_given_and_says_what_to_do(cw, tmp_path, capsys):
    page = tmp_path / "page.md"
    page.write_text(_words(81))
    assert cw.main(["check", str(page)]) == 1
    captured = capsys.readouterr()
    assert captured.out == f"{page}:1: paragraph of 81 words (limit 80)\n"
    assert "Split a paragraph" in captured.err


def test_the_published_docsite_has_no_walls(cw, capsys):
    assert cw.main(["check"]) == 0, capsys.readouterr().out
