"""`dev/check_docs_shell.py`: no comment in a shell block of the docs.

CI's lint job runs it over `docsite/content/`. These pin the rule itself, so a
loosened one cannot turn that step into a pass that checks nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from support.harness import write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_docs_shell.py"


@pytest.fixture(scope="module")
def cs():
    spec = importlib.util.spec_from_file_location("_dev_check_docs_shell", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _problems(cs, tmp_path: Path, body: str) -> list[str]:
    write(tmp_path, "1.page.md", f"Text.\n\n{body}\n\nMore text.\n")
    return cs.problems(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "```bash\nkraft view list\n```",
        "```bash\necho \"# not a comment\" 'and # this'\n```",
        "```bash\nopen https://example.dev/page#heading\n```",
        "```bash\necho ${#PATH} $#\n```",
        "```yaml\nkey: value  # a comment, in YAML\n```",
        "```\n# a fence with no language is not a shell fence\n```",
        "# a heading outside any fence",
        "- step\n\n  ```bash\n  kraft view list\n  ```",
        "```bash\nkraft view list\n```\n\nafter the fence: `# not in one`",
        "````md\n```bash\n# inside a longer fence of another language\n```\n````",
    ],
    ids=[
        "plain",
        "quoted",
        "url-fragment",
        "no-space-before-hash",
        "yaml",
        "no-language",
        "outside-a-fence",
        "indented-clean",
        "after-the-fence",
        "inside-a-longer-fence",
    ],
)
def test_a_block_without_a_comment_passes(cs, tmp_path, body):
    assert _problems(cs, tmp_path, body) == []


@pytest.mark.parametrize(
    "body",
    [
        '```bash\nbackup="$HOME/x" # keep a copy\n```',
        "```bash\n# keep a copy\ncp a b\n```",
        "```sh\ncp a b #note\n```",
        "```zsh\ncp a b # note\n```",
        "```shell\ncp a b # note\n```",
        "```console\n$ cp a b # note\n```",
        '```bash\necho "a" # quotes close before the comment\n```',
        "- step\n\n  ```bash\n  kraft view list  # indented in a list item\n  ```",
        "~~~bash\ncp a b # note\n~~~",
    ],
    ids=[
        "trailing",
        "whole-line",
        "no-space-after-hash",
        "zsh",
        "shell",
        "console",
        "after-quotes",
        "indented-fence-in-a-list-item",
        "tilde-fence",
    ],
)
def test_a_comment_in_a_shell_block_is_reported(cs, tmp_path, body):
    problems = _problems(cs, tmp_path, body)
    assert len(problems) == 1
    assert "1.page.md:" in problems[0]
    assert "move it to the sentence above the block" in problems[0]


def test_the_report_names_the_line(cs, tmp_path):
    problems = _problems(cs, tmp_path, "```bash\ncp a b\ncp c d # note\n```")
    assert problems[0].startswith("1.page.md:5: ")


def test_the_docs_pass(cs):
    assert cs.problems(cs.CONTENT) == []
