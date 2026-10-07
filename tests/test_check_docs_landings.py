"""`dev/check_docs_landings.py`: a section landing page lists its sidebar group.

CI's lint job runs it over `docsite/content/`. These pin what it compares, so
a check that stops reading a list or a folder cannot pass by seeing nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_docs_landings.py"


@pytest.fixture(scope="module")
def cl():
    spec = importlib.util.spec_from_file_location("_dev_check_docs_landings", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _site(root: Path, index: str, pages: tuple[str, ...] = ("1.install.md", "2.first.md")) -> Path:
    """A content folder with one section, `1.start/`, whose landing page is `index`."""
    section = root / "1.start"
    section.mkdir(parents=True)
    for page in pages:
        (section / page).parent.mkdir(parents=True, exist_ok=True)
        (section / page).write_text("text\n")
    (section / "index.md").write_text(index)
    return root


_BOTH = "- [Install](/start/install): a\n- [First](/start/first): b\n"


@pytest.mark.parametrize(
    ("index", "pages", "problem"),
    [
        (f"## In this section\n\n{_BOTH}", (), None),
        (f"## In this section\n\n{_BOTH}\n## Related\n\n- [Other](/guides/x): c\n", (), None),
        (f"intro\n\n{_BOTH}", (), 'no "## In this section" list'),
        ("## In this section\n\n- [Install](/start/install): a\n", (), "/start/first is in this"),
        (f"## In this section\n\n{_BOTH}- [Other](/guides/x): c\n", (), "/guides/x is listed"),
        (
            "## In this section\n\n- [First](/start/first): b\n- [Install](/start/install): a\n",
            (),
            "not in the sidebar's order",
        ),
        (
            f"## In this section\n\n{_BOTH}### Run\n\n- [Ops](/start/run/ops): c\n",
            ("3.run/1.ops.md",),
            None,
        ),
        (f"## In this section\n\n{_BOTH}", ("3.run/1.ops.md",), "/start/run/ops is in this"),
        (
            # `10.` sorts before `2.` by name, so this list is in the sidebar's order.
            "## In this section\n\n- [Install](/start/install): a\n- [Ops](/start/ops): c\n"
            "- [First](/start/first): b\n",
            ("10.ops.md",),
            "number prefixes differ in length",
        ),
    ],
    ids=[
        "lists-its-pages",
        "other-sections-under-another-heading",
        "no-list",
        "page-not-listed",
        "page-of-another-section-listed",
        "out-of-order",
        "group-without-a-landing-lists-its-pages",
        "group-without-a-landing-left-out",
        "prefixes-of-two-widths",
    ],
)
def test_a_landing_page_lists_its_own_folder_in_order(cl, tmp_path, index, pages, problem):
    found = cl.check(_site(tmp_path, index, ("1.install.md", "2.first.md", *pages)))
    assert [problem in line for line in found] == ([True] if problem else [])


def test_the_published_docsite_landings_match_their_groups(cl, capsys):
    assert cl.main() == 0, capsys.readouterr().out
