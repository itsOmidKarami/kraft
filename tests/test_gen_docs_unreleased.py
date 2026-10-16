"""`dev/gen_docs_unreleased.py`: the Unreleased page is built from the "Added in next" notes.

The docs build runs it for main's site. A released site has no `next` note left
(dev/stamp_docs_added_in.py), so it must write no page and leave no stale one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "gen_docs_unreleased.py"

_FRONT = "---\ntitle: {title}\ndescription: D\n---\n\n"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("_dev_gen_docs_unreleased", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # a dataclass in the parser finds its module by name
    spec.loader.exec_module(mod)
    return mod


def _content(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


_PAGES = {
    "3.guides/.navigation.yml": "title: Guides\n",
    "3.guides/1.old.md": _FRONT.format(title="Old page")
    + "Intro.\n\n## Old part\n\nText.\n\n## New part\n_Added in next_\n\nMore.\n\n"
    "| Verb | What |\n|---|---|\n| `x` | does it. _Added in next_ |\n",
    "3.guides/2.fresh.md": _FRONT.format(title="Fresh page") + "_Added in next_\n\nBody.\n",
    "3.guides/3.stamped.md": _FRONT.format(title="Stamped") + "## Thing\n_Added in 2.1_\n\nText.\n",
}


def test_lists_every_next_note_and_nothing_else(gen, tmp_path):
    content = _content(tmp_path, _PAGES)
    text, routes = gen.build(content)
    assert routes == {"/guides/old": "updated", "/guides/fresh": "new"}
    assert "## Guides" in text
    assert "- [Fresh page](/guides/fresh): a new page." in text
    # A section is listed once, however many notes sit in it, under its own anchor.
    assert text.count("[New part](/guides/old#new-part)") == 1
    assert "Stamped" not in text and "Old part" not in text


def test_a_page_with_no_note_gets_no_page_and_a_stale_one_is_removed(gen, tmp_path, capsys):
    content = _content(tmp_path, {"1.a.md": _FRONT.format(title="A") + "## H\n_Added in 2.1_\n"})
    stale = content / gen.PAGE
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("old", encoding="utf-8")

    assert gen.main(["gen", str(content)]) == 0

    assert not stale.exists()
    assert json.loads(capsys.readouterr().out) == {"page": None, "routes": {}}


def test_the_page_is_written_and_does_not_list_itself(gen, tmp_path, capsys):
    content = _content(tmp_path, _PAGES)
    assert gen.main(["gen", str(content)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["page"] == gen.ROUTE
    assert gen.ROUTE not in printed["routes"]
    # Run again with the page now in the tree: still not listed.
    assert gen.main(["gen", str(content)]) == 0
    assert gen.ROUTE not in json.loads(capsys.readouterr().out)["routes"]
