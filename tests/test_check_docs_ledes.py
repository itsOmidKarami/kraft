"""`dev/check_docs_ledes.py`: a page's first paragraph does not repeat its description.

CI's lint job runs it over `docsite/content/`. These pin the scorer, so a
change that stops it seeing the description or the paragraph cannot turn that
step into a pass that checks nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_docs_ledes.py"

_DESC = "Install Kraft with uv or Homebrew, start the server, and check that it works."


@pytest.fixture(scope="module")
def cl():
    spec = importlib.util.spec_from_file_location("_dev_check_docs_ledes", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _page(body: str, desc: str = _DESC) -> str:
    return f"---\ntitle: T\ndescription: {desc}\n---\n\n{body}\n"


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("Install Kraft, start its server, and check that it works on your machine.", True),
        (
            "At the end the board loads at the local address and the doctor has nothing to report.",
            False,
        ),
        # Only the first sentence is judged: the rest of a paragraph may say more.
        (
            "The board loads locally."
            " Install Kraft, start the server, and check that it works with uv or Homebrew.",
            False,
        ),
        # A pointer this short is not a restatement, whatever words it shares.
        ("Install Kraft first.", False),
        # Under a heading it is no longer the opening paragraph.
        (
            "## Steps\n\nInstall Kraft, start its server, and check that it works on your machine.",
            False,
        ),
    ],
    ids=["restates", "adds", "first-sentence-only", "too-short", "after-heading"],
)
def test_an_opening_paragraph_is_flagged_only_when_it_repeats_the_description(cl, body, flagged):
    score = cl.overlap(_page(body))
    assert (score is not None and score >= cl.THRESHOLD) is flagged


def test_a_page_without_a_description_is_not_judged(cl):
    assert (
        cl.overlap("Install Kraft, start its server, and check that it works on your machine.\n")
        is None
    )


def test_main_names_the_page_and_says_what_to_do(cl, tmp_path, capsys):
    page = tmp_path / "page.md"
    page.write_text(
        _page("Install Kraft, start its server, and check that it works on your machine.")
    )
    assert cl.main(["check", str(page)]) == 1
    captured = capsys.readouterr()
    assert f"{page}: first paragraph repeats the description" in captured.out
    assert "Delete the paragraph" in captured.err


def test_the_published_docsite_has_no_repeated_ledes(cl, capsys):
    assert cl.main(["check"]) == 0, capsys.readouterr().out
