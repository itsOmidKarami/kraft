"""A fake `kraft` and a fake `herdr` on PATH, and the record of what they were asked.

The same trick as the repo's `fixtures/bin`, kept stdlib-only because this
directory's tests run with nothing installed but pytest. Each fake logs its argv
as one JSON line and answers from a script: the longest argv prefix that
matches picks the reply, and a list of replies is handed out in order, the last
one repeating.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

_TOOL = """#!{python}
import json, os, sys
root = {root!r}
name = {name!r}
argv = sys.argv[1:]
with open(os.path.join(root, "calls.jsonl"), "a") as log:
    log.write(json.dumps([name, argv]) + "\\n")
script = json.load(open(os.path.join(root, "script.json"))).get(name, {{}})
counts_path = os.path.join(root, "counts.json")
counts = json.load(open(counts_path)) if os.path.exists(counts_path) else {{}}
def matches(k):
    return argv[: len(k.split())] == k.split()
key = next((k for k in sorted(script, key=lambda k: -len(k.split())) if matches(k)), None)
reply = script.get(key, {{}})
if isinstance(reply, list):
    n = counts.get(name + " " + str(key), 0)
    counts[name + " " + str(key)] = n + 1
    json.dump(counts, open(counts_path, "w"))
    reply = reply[min(n, len(reply) - 1)]
out = reply.get("stdout", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("exit", 0))
"""


def result(**body) -> dict:
    """What `herdr` prints: the answer under `result`."""
    return {"stdout": {"id": "x", "result": body}}


class Fakes:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir(parents=True)
        self.script: dict[str, dict] = {"kraft": {}, "herdr": {}}
        for name in ("kraft", "herdr"):
            tool = self.bin / name
            tool.write_text(_TOOL.format(python=sys.executable, root=str(root), name=name))
            tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        self.write()

    def install(self, monkeypatch) -> Fakes:
        monkeypatch.setenv("PATH", f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
        monkeypatch.delenv("HERDR_PLUGIN_ID", raising=False)
        return self

    def reply(self, tool: str, argv: str, answer) -> None:
        """`answer` is a reply dict, or a list of them to hand out in order."""
        self.script[tool][argv] = answer
        self.write()

    def write(self) -> None:
        (self.root / "script.json").write_text(json.dumps(self.script))

    def calls(self, tool: str | None = None, prefix: str = "") -> list[list[str]]:
        path = self.root / "calls.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        want = prefix.split()
        return [a for t, a in rows if (tool is None or t == tool) and a[: len(want)] == want]
