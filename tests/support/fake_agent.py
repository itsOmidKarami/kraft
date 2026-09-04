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


def _record_prompt(argv: list[str]) -> None:
    """Append the -p instruction to KRAFT_FAKE_AGENT_PROMPT_LOG, so a test can
    assert on what the executor actually asked the agent to do."""
    dest = os.environ.get("KRAFT_FAKE_AGENT_PROMPT_LOG")
    if not dest:
        return
    for i, a in enumerate(argv):
        if a == "-p" and i + 1 < len(argv):
            with open(dest, "a") as fh:
                fh.write(argv[i + 1] + "\n\x00\n")
            return


def main() -> int:
    _record_prompt(sys.argv)
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
    # A real agent's final envelope carries its token usage; usage capture reads
    # this line, so the fake carries it too.
    envelope = {
        "type": "result",
        "is_error": mode == "error",
        "model": "fake-agent",
        # a real agent CLI reports what it was billed; Kraft never computes it
        "total_cost_usd": 0.035,
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500},
    }
    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
