"""The server's paths as the machine keeps them: its run directory and an item's
worktree live somewhere else there. Applied to every string a job carries (argv,
env values, cwd), because a prompt names the paths of the files it points at."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathMap:
    pairs: tuple[tuple[str, str], ...]

    def _pattern(self) -> re.Pattern[str]:
        sources = sorted((s for s, _ in self.pairs), key=len, reverse=True)
        alt = "|".join(re.escape(s) for s in sources)
        # not the middle of a longer name: /srv/run must not match /srv/runner
        return re.compile(rf"(?<![\w.-])(?:{alt})(?![\w-])") if alt else re.compile(r"(?!x)x")

    def text(self, value: str) -> str:
        table = dict(self.pairs)
        return self._pattern().sub(lambda m: table[m.group(0)], value)

    def path(self, value: str | Path) -> Path:
        return Path(self.text(str(value)))

    def covers(self, value: str | Path) -> bool:
        text = str(value)
        return any(text == s or text.startswith(s.rstrip("/") + "/") for s, _ in self.pairs)
