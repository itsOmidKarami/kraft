"""Stand-in for `claude` headless. Invoked as:
   python fake_agent.py -p <instr> --append-system-prompt <ctx> \
       --output-format stream-json --verbose
CWD is the worktree. Mode via KRAFT_FAKE_AGENT: fix (default) | noop | error |
rate_limit (rejects with KRAFT_FAKE_AGENT_RESETS_AT, default 1788968400).

Obeys the session-summary instructions in the injected context (03 §3, 04 §6):
reads the linkage fields back out of the prompt, writes
.engineering/sessions/<session>.md, and reports session_summary_ref.

The reported `status` defaults to "done". KRAFT_FAKE_AGENT_STATUS overrides it
for every invocation; KRAFT_FAKE_AGENT_CONCERNS / KRAFT_FAKE_AGENT_QUESTION /
KRAFT_FAKE_AGENT_VERDICT set the field that goes with `done_with_concerns` /
`needs_context` / the fix-loop judge's verdict. For per-cycle scripting,
KRAFT_FAKE_AGENT_PLAN points at a JSON file the same shape as
tests/support/fake_reviewer.py's plan: a list of per-invocation
`{"status": ..., "concerns": ..., "question": ..., "verdict": ...}` entries (a
plan entry wins over the single-shot env vars when both are set).
"""

import json
import os
import pathlib
import re
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


def _write_artifact(argv: list[str]) -> None:
    """Honour the `artifact:` contract when the injected context states one.

    A binding that declares an artifact now fails its node unless the file
    exists (Kraft-7lu), so a fake that reported success without writing one
    would stand in for a *broken* worker in every test that drives spec or
    plan. The path is read back out of the prompt, exactly as the session
    summary already is, rather than duplicating `agent.artifact_path()` here.

    KRAFT_FAKE_AGENT_SKIP_ARTIFACT=1 suppresses it, for a test that wants the
    empty-gate failure on purpose.
    """
    if os.environ.get("KRAFT_FAKE_AGENT_SKIP_ARTIFACT"):
        return
    if "--append-system-prompt" not in argv:
        return
    ctx = argv[argv.index("--append-system-prompt") + 1]
    match = re.search(r"Write your (\w+) to (\S+?), relative to the repo root", ctx)
    if not match:
        return
    kind, rel = match.group(1), match.group(2)
    fields = _ctx_fields(argv)
    path = pathlib.Path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"work_item_ids: [{fields.get('Work item', '')}]\n"
        f"node_id: {fields.get('Node', '')}\n"
        f"hook_point: {fields.get('Hook point', '')}\n"
        f"kind: {kind}s\n"
        f"title: fake {kind}\n"
        f"---\n\nfake agent {kind}\n"
    )


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


def _plan_entry() -> dict:
    """Per-invocation `{"status": ..., "concerns": ..., "question": ...}` from
    KRAFT_FAKE_AGENT_PLAN, following tests/support/fake_reviewer.py's mechanism:
    a JSON list of responses plus a sidecar invocation counter, because
    run_task passes no round number into the child. Empty dict if unset."""
    plan_env = os.environ.get("KRAFT_FAKE_AGENT_PLAN")
    if not plan_env:
        return {}
    plan_path = pathlib.Path(plan_env)
    plan = json.loads(plan_path.read_text())
    counter = plan_path.with_suffix(".count")
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    return plan[min(n, len(plan) - 1)]


def _record_argv(argv: list[str]) -> None:
    """Append the full argv (JSON, one line) to KRAFT_FAKE_AGENT_ARGV_LOG, so a
    test can assert on flags -p doesn't cover, like --model."""
    dest = os.environ.get("KRAFT_FAKE_AGENT_ARGV_LOG")
    if not dest:
        return
    with open(dest, "a") as fh:
        fh.write(json.dumps(argv[1:]) + "\n")


def main() -> int:
    _record_prompt(sys.argv)
    _record_argv(sys.argv)
    mode = os.environ.get("KRAFT_FAKE_AGENT", "fix")
    if mode == "fix":
        calc = pathlib.Path("calc.py")
        calc.write_text(calc.read_text().replace("a - b", "a + b"))
    result_path = os.environ.get("KRAFT_RESULT_PATH")
    if mode not in ("error", "rate_limit") and result_path:
        _write_artifact(sys.argv)
        fields = _ctx_fields(sys.argv)
        ref = _write_summary(fields)
        entry = _plan_entry()
        # KRAFT_FAKE_AGENT_STATUS/_CONCERNS/_QUESTION are the single-shot knobs;
        # a plan entry (per invocation) overrides them when present. Default
        # stays "done" so every test that sets neither is unaffected.
        status = entry.get("status") or os.environ.get("KRAFT_FAKE_AGENT_STATUS", "done")
        # chain_review's own decision lives inside its artifact envelope, not
        # this outer per-task result -- a global KRAFT_FAKE_AGENT_STATUS aimed
        # at the node under test must not also starve chain_finalized of the
        # gate it needs (same reasoning as fixtures/fake-claude.sh).
        if fields.get("Hook point") == "on.chain.review_ready":
            status = "done"
        result = {"status": status}
        concerns = entry.get("concerns") or os.environ.get("KRAFT_FAKE_AGENT_CONCERNS")
        if concerns:
            result["concerns"] = concerns
        question = entry.get("question") or os.environ.get("KRAFT_FAKE_AGENT_QUESTION")
        if question:
            result["question"] = question
        verdict = entry.get("verdict") or os.environ.get("KRAFT_FAKE_AGENT_VERDICT")
        if verdict:
            result["verdict"] = verdict
        if ref:
            result["session_summary_ref"] = ref
        # Lets a test pin that the fix task's own result never leaks into the
        # next cycle's findings (KRAFT_FAKE_AGENT_FINDING sets the message).
        finding = os.environ.get("KRAFT_FAKE_AGENT_FINDING")
        if finding:
            result["findings"] = [
                {
                    "severity": "critical",
                    "message": finding,
                    "file": "fixagent.py",
                    "line": 1,
                    "source_plugin": "fake-agent",
                }
            ]
        pathlib.Path(result_path).write_text(json.dumps(result))
    # The adapter runs the real CLI in stream-json mode, so the fake streams the
    # shapes the adapter now parses: an init line with the model, one assistant
    # line with a request_id and per-request usage, then the envelope last.
    print(json.dumps({"type": "system", "subtype": "init", "model": "fake-agent"}), flush=True)
    print(
        json.dumps(
            {
                "type": "assistant",
                "request_id": "req_1",
                "message": {
                    "model": "fake-agent",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 500,
                    },
                },
            }
        ),
        flush=True,
    )
    if mode == "rate_limit":
        print(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "rejected",
                        "resetsAt": int(os.environ.get("KRAFT_FAKE_AGENT_RESETS_AT", "1788968400")),
                        "rateLimitType": "five_hour",
                    },
                }
            ),
            flush=True,
        )
    envelope = {
        "type": "result",
        "is_error": mode in ("error", "rate_limit"),
        # no top-level `model`: the real envelope carries `modelUsage`, keyed by
        # model name, which is why every worker_sessions row had model NULL
        "modelUsage": {"fake-agent": {"inputTokens": 1500, "outputTokens": 200}},
        # a real agent CLI reports what it was billed; Kraft never computes it
        "total_cost_usd": 0.035,
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500},
    }
    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
