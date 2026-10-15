"""`dev/stamp_docs_added_in.py`: a release turns the docs' `next` notes into its number.

The release workflow runs it over `docsite/content` when it tags a release, and
`dev/build_docs_site.sh` runs it over a tag's copy. These pin what it writes for
a minor, a major and a patch, and that it leaves code alone.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "stamp_docs_added_in.py"
_spec = importlib.util.spec_from_file_location("_dev_stamp_docs_added_in", _SCRIPT)
stamp_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stamp_mod)

_PAGE = """\
---
title: T
---

_Added in next_

Intro.

## Verb
<!-- renamed from: Old verb -->

_Added in next_

Text.

| Verb | Does |
|---|---|
| `x` | Does it. _Added in next_ |
| `y` | Already there. |

## Old
_Added in 2.1_

Prose with `_Added in next_` in code.

```md
_Added in next_
```
"""

_AS_2_1 = (
    _PAGE.replace("_Added in next_\n\nIntro", "_Added in 2.1_\n\nIntro")
    .replace("<!-- renamed from: Old verb -->\n\n_Added in next_", "\n_Added in 2.1_")
    .replace("Does it. _Added in next_", "Does it. _Added in 2.1_")
)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        # A minor: `next` becomes the minor, a number stays, code is left alone, and the
        # comment that excused a rename goes.
        ("2.1.0", _AS_2_1.replace("## Verb\n\n\n", "## Verb\n\n")),
        # A major starts with no notes at all, numbered ones too.
        (
            "3.0.0",
            "---\ntitle: T\n---\n\nIntro.\n\n## Verb\n\nText.\n\n| Verb | Does |\n|---|---|\n"
            "| `x` | Does it. |\n| `y` | Already there. |\n\n## Old\n\n"
            "Prose with `_Added in next_` in code.\n\n```md\n_Added in next_\n```\n",
        ),
    ],
    ids=["minor", "major"],
)
def test_a_release_rewrites_the_notes(tmp_path, version, expected):
    page = write(tmp_path, "5.reference/a.md", _PAGE)
    assert stamp_mod.stamp(version, tmp_path) == []
    assert page.read_text() == expected


@pytest.mark.parametrize(
    ("text", "stuck"),
    [
        (_PAGE, True),
        # Nothing for a patch to say: only the rename comment goes.
        ("## A\n<!-- not new: prose -->\n\n_Added in 2.1_\n", False),
    ],
    ids=["feature-under-a-patch", "nothing-to-stamp"],
)
def test_a_patch_adds_no_features(tmp_path, text, stuck):
    page = write(tmp_path, "a.md", text)
    assert stamp_mod.stamp("2.0.1", tmp_path) == (["a.md"] if stuck else [])
    # A refused stamp writes nothing; an accepted one only drops the comment.
    assert page.read_text() == (text if stuck else "## A\n\n_Added in 2.1_\n")


def test_a_bad_version_is_refused(tmp_path):
    with pytest.raises(ValueError, match="bare X.Y.Z"):
        stamp_mod.stamp("v2.1.0", tmp_path)


def test_the_command_exits_1_on_a_feature_under_a_patch(tmp_path):
    write(tmp_path, "a.md", "_Added in next_\n")
    run = subprocess.run(
        [sys.executable, str(_SCRIPT), "2.0.1", str(tmp_path)], capture_output=True, text=True
    )
    assert run.returncode == 1
    assert "a.md" in run.stderr
