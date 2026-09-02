"""Stand-in for `claude` headless. Invoked as:
   python fake_agent.py -p <instr> --append-system-prompt <ctx> --output-format json
CWD is the worktree. Mode via KRAFT_FAKE_AGENT: fix (default) | noop | error.
"""

import json
import os
import pathlib
import sys


def main() -> int:
    mode = os.environ.get("KRAFT_FAKE_AGENT", "fix")
    if mode == "fix":
        calc = pathlib.Path("calc.py")
        calc.write_text(calc.read_text().replace("a - b", "a + b"))
    envelope = {"type": "result", "is_error": mode == "error"}
    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
