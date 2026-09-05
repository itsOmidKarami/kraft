"""A measuring task whose findings a test dictates, one response per invocation.

Bound to `on.review.local.run` via a registry override. Nothing in the real
codebase can emit a finding yet — `on.test.run` is pytest (no result file) and
`on.review.local.run` is `builtins.noop` — so the fix loop's findings behaviour
has nothing to exercise it without this.

`KRAFT_FAKE_REVIEW_PLAN` points at a JSON file: a list of per-invocation
responses, each `{"status": "done"|"failed", "findings": [...]}`. The Nth
invocation writes the Nth entry; past the end, the last entry repeats. The
invocation count lives in a sidecar next to the plan, because `run_task` passes
no round number into the child.
"""

import json
import os
import pathlib
import sys


def main() -> int:
    plan_path = pathlib.Path(os.environ["KRAFT_FAKE_REVIEW_PLAN"])
    plan = json.loads(plan_path.read_text())

    counter = plan_path.with_suffix(".count")
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))

    entry = plan[min(n, len(plan) - 1)]

    result = pathlib.Path(os.environ["KRAFT_RESULT_PATH"])
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(entry))
    print(f"fake reviewer invocation {n}: {entry.get('status')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
