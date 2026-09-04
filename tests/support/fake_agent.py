"""Stand-in for `claude` headless. Invoked as:
   python fake_agent.py -p <instr> --append-system-prompt <ctx> --output-format json
CWD is the worktree. Mode via KRAFT_FAKE_AGENT: fix (default) | noop | error.

Obeys the session-summary instructions in the injected context (03 §3, 04 §6):
reads the linkage fields back out of the prompt, writes
.engineering/sessions/<session>.md, and reports session_summary_ref.
"""

import json
import os
import pathlib
import sys


def _ctx_fields(argv: list[str]) -> dict[str, str]:
    if "--append-system-prompt" not in argv:
        return {}
    ctx = argv[argv.index("--append-system-prompt") + 1]
    fields = {}
    for line in ctx.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            fields.setdefault(key, value)
    return fields


def _write_summary(fields: dict[str, str]) -> str | None:
    session = fields.get("Worker session")
    if not session:
        return None
    ref = f".engineering/sessions/{session}.md"
    path = pathlib.Path(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"work_item_ids: [{fields.get('Work item', '')}]\n"
        f"node_id: {fields.get('Node', '')}\n"
        f"hook_point: {fields.get('Hook point', '')}\n"
        f"worker_session_id: {session}\n"
        "---\n\nfake agent session\n"
    )
    return ref


def main() -> int:
    mode = os.environ.get("KRAFT_FAKE_AGENT", "fix")
    if mode == "fix":
        calc = pathlib.Path("calc.py")
        calc.write_text(calc.read_text().replace("a - b", "a + b"))
    result_path = os.environ.get("KRAFT_RESULT_PATH")
    if mode != "error" and result_path:
        ref = _write_summary(_ctx_fields(sys.argv))
        result = {"status": "done"}
        if ref:
            result["session_summary_ref"] = ref
        pathlib.Path(result_path).write_text(json.dumps(result))
    envelope = {"type": "result", "is_error": mode == "error"}
    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
