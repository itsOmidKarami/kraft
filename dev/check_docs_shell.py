"""Fail if a shell block in the docs holds a comment.

An interactive zsh, the macOS default, does not treat `#` as a comment:
`backup="..." # note` leaves `backup` unset and prints `command not found: #`.
A reader who pastes a block line by line gets that. Say it in the sentence
above the block instead.

Checks every line inside a `bash`, `sh`, `shell`, `zsh` or `console` fence of
docsite/content/ and of the READMEs a reader pastes from (`README.md`,
`vscode/README.md` and the three plugin and add-on ones): it fails if the line
starts with `#` or has a `#` after whitespace outside single and double quotes.
A `#` inside quotes or in a URL fragment (`/page#heading`) passes; a YAML, JSON or
other fence is not read.
A `#` in a heredoc's body is read as a comment too: no docs page has a
heredoc, and one that needs it belongs in a file block, not a pasted one.

Run directly: `uv run python dev/check_docs_shell.py`. Exits 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONTENT = ROOT / "docsite" / "content"
READMES = [
    ROOT / name
    for name in (
        "README.md",
        "vscode/README.md",
        "plugins/kraft/README.md",
        "plugins/kraft-lite/README.md",
        "addons/kraft-superpowers/README.md",
    )
]

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\w*)")
_SHELL = {"bash", "sh", "shell", "zsh", "console"}
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_COMMENT = re.compile(r"(^|\s)#")


def _label(page: Path, content: Path) -> Path:
    for base in (content, ROOT):
        if page.is_relative_to(base):
            return page.relative_to(base)
    return page


def problems(content: Path, files: Sequence[Path] = ()) -> list[str]:
    found = []
    for page in [*sorted(content.rglob("*.md")), *files]:
        fence = ""  # the marker that opened the block we are in
        shell = False
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            marker = _FENCE.match(line)
            if marker and not fence:
                fence, shell = marker.group(1), marker.group(2) in _SHELL
            elif marker and marker.group(1).startswith(fence) and not marker.group(2):
                fence, shell = "", False
            elif shell and _COMMENT.search(_QUOTED.sub('""', line.strip())):
                found.append(
                    f"{_label(page, content)}:{number}: a comment in a shell block "
                    f"(zsh does not read `#` as one): move it to the sentence above the "
                    f"block: {line.strip()[:80]}"
                )
    return found


def main() -> int:
    found = problems(CONTENT, READMES)
    for problem in found:
        print(problem)
    if found:
        print(f"{len(found)} shell comment(s) in the docs", file=sys.stderr)
        return 1
    print("ok: no shell block in the docs holds a comment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
