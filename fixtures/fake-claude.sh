#!/usr/bin/env bash
# Stand-in for `claude` headless. Invoked as:
#   fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format stream-json --verbose
# CWD is the worktree. Modes via KRAFT_FAKE_CLAUDE: fix (default) | noop | slow | stall.
# `stall` is `noop` that, as a fix-loop repair, still commits a file. The
# default `fix` mode edits calc.py only once, so a second fix round edits
# nothing and a `just dev` fix loop stops after its first round: that is the
# product's behaviour (a round that changed nothing stops), not a fake bug.
# The reported status defaults to "done"; KRAFT_FAKE_CLAUDE_STATUS overrides it,
# and KRAFT_FAKE_CLAUDE_CONCERNS / KRAFT_FAKE_CLAUDE_QUESTION set the field that
# goes with done_with_concerns / needs_context.
# The planning artifact is written only when the reported status advances the
# chain; KRAFT_FAKE_CLAUDE_SKIP_ARTIFACT=1 suppresses it even then.
# Always writes $KRAFT_RESULT_PATH so an adopted session can resolve after a restart.
set -eu

mode="${KRAFT_FAKE_CLAUDE:-fix}"
status="${KRAFT_FAKE_CLAUDE_STATUS:-done}"

# Test seam: record the -p instruction so a test can assert what was actually asked.
if [ -n "${KRAFT_FAKE_CLAUDE_PROMPT_LOG:-}" ]; then
  prev=""
  for arg in "$@"; do
    if [ "$prev" = "-p" ]; then printf '%s\n\000\n' "$arg" >> "$KRAFT_FAKE_CLAUDE_PROMPT_LOG"; break; fi
    prev="$arg"
  done
fi

# Test seam: record the full argv, one invocation per record (a lone NUL line
# separates records, same convention as PROMPT_LOG above) -- so a test can
# assert which --model actually reached the agent, since the reported
# "model" in worker_sessions comes from the agent's own usage payload below,
# not from this argv (Kraft-df4tc).
if [ -n "${KRAFT_FAKE_CLAUDE_ARGV_LOG:-}" ]; then
  for arg in "$@"; do printf '%s\n' "$arg" >> "$KRAFT_FAKE_CLAUDE_ARGV_LOG"; done
  printf '\000\n' >> "$KRAFT_FAKE_CLAUDE_ARGV_LOG"
fi

# KRAFT_SUBAGENTS in the title: the run spawns two Claude Code sub-agents, one
# that completes and one that fails (shapes as claude 2.1.316 writes them), so
# the log's agent filter and labels have something to show.
subagents=""
case "$*" in *KRAFT_SUBAGENTS*) subagents=1 ;; esac
init_printed=""

# The adapter runs the real CLI with `--output-format stream-json --verbose`,
# which opens with an init line, before any work -- so a session paused
# mid-run has already named itself. KRAFT_FAKE_CLAUDE_SESSION_ID is the
# provider session id that line carries, the one `--resume` takes.
print_init() {
  printf '{"type":"system","subtype":"init","model":"fake-agent","tools":[]%s}\n' \
    "${KRAFT_FAKE_CLAUDE_SESSION_ID:+,\"session_id\":\"$KRAFT_FAKE_CLAUDE_SESSION_ID\"}"
  init_printed=1
}

# The two sub-agents' starts. Every sub-agent runs in the background headless,
# so a start is the first thing a log shows of one.
subagents_started=""
print_subagent_starts() {
  cat <<'LINES'
{"type":"system","subtype":"task_started","task_id":"fa0001","run_id":"fr01","tool_use_id":"toolu_fa0001","description":"Explore codebase","subagent_type":"Explore","is_backgrounded":true,"spawn_depth":1,"task_type":"local_agent","prompt":"List the modules under src/"}
{"type":"system","subtype":"task_started","task_id":"fa0002","run_id":"fr02","tool_use_id":"toolu_fa0002","description":"Review diff","subagent_type":"general-purpose","is_backgrounded":true,"spawn_depth":1,"task_type":"local_agent","prompt":"Review the diff for mistakes"}
LINES
  subagents_started=1
}

# What the two sub-agents then do: a tool call and an answer each, their
# progress, and how each ended.
print_subagent_work() {
  cat <<'LINES'
{"type":"assistant","parent_tool_use_id":"toolu_fa0001","agent_id":"fa0001","subagent_type":"Explore","task_description":"Explore codebase","timestamp":"2026-11-02T23:38:52.727Z","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls src/"}}]}}
{"type":"assistant","parent_tool_use_id":"toolu_fa0002","agent_id":"fa0002","subagent_type":"general-purpose","task_description":"Review diff","timestamp":"2026-11-02T23:38:52.801Z","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"calc.py"}}]}}
{"type":"system","subtype":"task_progress","task_id":"fa0001","run_id":"fr01","tool_use_id":"toolu_fa0001","description":"Running Explore codebase","subagent_type":"Explore","usage":{"total_tokens":31067,"tool_uses":1,"duration_ms":3452},"last_tool_name":"Bash"}
{"type":"user","parent_tool_use_id":"toolu_fa0001","agent_id":"fa0001","subagent_type":"Explore","task_description":"Explore codebase","timestamp":"2026-11-02T23:38:52.910Z","message":{"content":[{"type":"tool_result","content":"kraft\n"}]}}
{"type":"assistant","parent_tool_use_id":"toolu_fa0001","agent_id":"fa0001","subagent_type":"Explore","task_description":"Explore codebase","timestamp":"2026-11-02T23:38:54.194Z","message":{"content":[{"type":"text","text":"One package: kraft."}]}}
{"type":"system","subtype":"task_updated","task_id":"fa0001","run_id":"fr01","patch":{"status":"completed","end_time":1793662734269}}
{"type":"system","subtype":"task_notification","task_id":"fa0001","run_id":"fr01","tool_use_id":"toolu_fa0001","status":"completed","output_file":"tasks/fa0001.output","summary":"One package: kraft.","usage":{"total_tokens":31902,"tool_uses":1,"duration_ms":4992}}
{"type":"assistant","parent_tool_use_id":"toolu_fa0002","agent_id":"fa0002","subagent_type":"general-purpose","task_description":"Review diff","timestamp":"2026-11-02T23:38:55.002Z","message":{"content":[{"type":"text","text":"I could not read the diff."}]}}
{"type":"system","subtype":"task_updated","task_id":"fa0002","run_id":"fr02","patch":{"status":"failed","end_time":1793662735100}}
{"type":"system","subtype":"task_notification","task_id":"fa0002","run_id":"fr02","tool_use_id":"toolu_fa0002","status":"failed","output_file":"tasks/fa0002.output","summary":"I could not read the diff.","usage":{"total_tokens":12000,"tool_uses":1,"duration_ms":2300}}
LINES
}

# `stall` mode: a fix-loop repair commits a file even when it fails, so the loop
# sees a round that changed something. A file, not an empty commit: a commit
# touching none reads as nothing changed.
stall_commit() {
  case "$1" in
    *.fix_loop.judge) ;;
    *.fix_loop.*)
      echo x > "stall-$$.txt"
      git add "stall-$$.txt" >/dev/null 2>&1 || true
      git -c user.name=t -c user.email=t@t commit -qm stall >/dev/null 2>&1 || true ;;
  esac
}

# Per-invocation failure. KRAFT_FAKE_CLAUDE is per-server, so it cannot make one
# work item fail while its neighbours succeed; the instruction can. The seed
# script puts KRAFT_FAIL in a work item title to drive the needs_human path.
for arg in "$@"; do
  case "$arg" in
    *KRAFT_FAIL*)
      if [ "$mode" = "stall" ]; then
        stall_commit "$(printf '%s\n' "$@" | sed -n 's/^Hook point: //p' | head -1)"
      fi
      printf 'fake-claude: asked to fail\n' >&2; exit 3 ;;
    # break: the title reaches this script twice (-p and --append-system-prompt),
    # and sleeping once per copy doubles the delay the seed script waits on.
    *KRAFT_SLOW*) sleep "${KRAFT_FAKE_CLAUDE_DELAY:-15}"; break ;;
    # Long enough that the item is still running when someone looks at it.
    *KRAFT_HANG*)
      # With sub-agents the log must already show them running while it hangs.
      if [ -n "$subagents" ]; then print_init; print_subagent_starts; fi
      sleep "${KRAFT_FAKE_CLAUDE_HANG:-1800}"; break ;;
  esac
done

# Keywords that shape what a run produces rather than whether it runs. Read off
# every argument at once (a title may carry several), unlike the arm above.
cost="0.035"
case "$*" in
  *KRAFT_COST_[0-9]*) cost="$(printf '%s' "$*" | sed -n 's/.*KRAFT_COST_\([0-9][0-9.]*[0-9]\).*/\1/p' | head -1)" ;;
esac
many_files=""
case "$*" in *KRAFT_MANY_FILES*) many_files=1 ;; esac

if [ -z "$init_printed" ]; then print_init; fi

if [ "$mode" = "slow" ]; then
  sleep "${KRAFT_FAKE_CLAUDE_DELAY:-10}"
  mode="fix"
fi

if [ "$mode" = "fix" ] && [ -f calc.py ]; then
  # portable in-place edit: rewrite the file
  tmp="$(mktemp)"
  sed 's/a - b/a + b/' calc.py > "$tmp" && mv "$tmp" calc.py
fi

# A change wide enough to stress a review page: dozens of files, one on a long
# path, one renamed, one deleted, one binary, one long enough to scroll.
if [ -n "$many_files" ]; then
  long_dir="src/kraft_polish/internationalization/translations/right_to_left_languages/pluralization_rules"
  mkdir -p "$long_dir" src/pkg docs/guide assets
  for n in $(seq -w 1 36); do
    printf 'def handler_%s(value):\n    """Handle case %s."""\n    return value + %s\n' "$n" "$n" "$((10#$n))" > "src/pkg/module_$n.py"
  done
  awk 'BEGIN { for (i = 1; i <= 220; i++) printf "RULE_%03d = \"rule %d of the pluralization table\"\n", i, i }' \
    > "$long_dir/very_long_module_name_for_checking_truncation_in_the_file_tree_sidebar.py"
  if [ -f legacy.txt ]; then rm legacy.txt; fi
  if [ -f docs/old-notes.md ]; then mv docs/old-notes.md docs/guide/notes.md; fi
  printf '\211PNG\r\n\032\n\000\000\000\rIHDRfake-claude\000\377\000\377' > assets/logo.png
fi

# The agent adapter injects the work-item/node/session linkage through
# whichever context channel the harness declares: claude's
# --append-system-prompt, or codex's `-c developer_instructions=<text>`
# (src/kraft/harnesses/*.yaml `context.channel`). Obey either, so integration
# tests exercise the real ingestion inputs regardless of which fake this
# script is playing (`fixtures/bin/codex` symlinks here too).
ctx=""
prev=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--append-system-prompt" ] && [ "$#" -gt 1 ]; then ctx="$2"; fi
  case "$prev" in
    -c) case "$1" in developer_instructions=*) ctx="${1#developer_instructions=}" ;; esac ;;
  esac
  prev="$1"
  shift
done
field() { printf '%s\n' "$ctx" | sed -n "s/^$1: //p" | head -1; }

if [ "$mode" = "stall" ]; then stall_commit "$(field 'Hook point')"; fi

# The summary goes where the context says; Kraft records it itself, so the
# result file reports no session_summary_ref.
session="$(field 'Worker session')"
summary_path="$(printf '%s\n' "$ctx" | sed -n 's/.*session summary to \([^ ]*\), starting.*/\1/p' | head -1)"
if [ -n "$session" ] && [ -n "$summary_path" ]; then
  mkdir -p "$(dirname "$summary_path")"
  cat > "$summary_path" <<EOF
---
work_item_ids: [$(field 'Work item')]
node_id: $(field 'Node')
hook_point: $(field 'Hook point')
worker_session_id: ${session}
---

fake-claude session
EOF
fi

# A task that `produces:` a document is contractually required to write it,
# where the contract says: its item's docs/, outside the repository, so there
# is nothing to commit. Honour that, so the gate, the artifact endpoint and the
# indexer all see the real inputs.
hook="$(field 'Hook point')"
item="$(field 'Work item')"

# A task's hook point is its canonical path (`spec.main.author`), and what it
# must write is declared by `produces:` and spelled out in the instruction the
# adapter built. So read the artifact contract line itself -- "Write your <kind> to
# <path>" -- which is the same thing a real agent does, and the only signal
# that survives a chain author renaming the node. Not a path substring: an
# attached spec puts `.engineering/specs/` into the *plan* author's context
# too, and that read wrote the plan author a spec.
kind="$(printf '%s\n' "$ctx" | sed -n 's/.*Write your \([a-z_]*\) to .*/\1/p' | head -1)"
doc_path="$(printf '%s\n' "$ctx" | sed -n 's/.*Write your [a-z_]* to \([^ ]*\), creating that directory.*/\1/p' | head -1)"
# Only on a status that advances the chain (executor._ADVANCING), mirroring
# tests/support/fake_agent.py. A real worker that reports needs_context or a
# failure has written nothing, and `agent._resolve_status` holds only a claim of
# success to the artifact -- a fake that writes it regardless is the reason the
# needs_context downgrade bug in MR !58 passed every needs_context test.
# `$status` is already resolved here, so this is a guard and not a reordering.
write_artifact=""
case "$status" in
  done|done_with_concerns) write_artifact=1 ;;
esac
if [ -n "${KRAFT_FAKE_CLAUDE_SKIP_ARTIFACT:-}" ]; then write_artifact=""; fi

# The labels an `mr_meta` names, so a test can see them reach the opened MR.
labels=""
if [ "$kind" = mr_meta ] && [ -n "${KRAFT_FAKE_CLAUDE_LABELS:-}" ]; then
  labels="labels: [${KRAFT_FAKE_CLAUDE_LABELS}]
"
fi

# A chain revision is a change set Kraft parses, not prose: a fake one proposes
# no change, the common real answer, so its gate passes without a human.
body="fake ${kind} body"
if [ "$kind" = chain_revision ]; then
  body="$(printf '%s\n%s\n%s' '```json' '{"rationale": "fake: the chain fits the plan"}' '```')"
fi
if [ -n "$kind" ] && [ -n "$doc_path" ] && [ -n "$item" ] && [ -n "$write_artifact" ]; then
  mkdir -p "$(dirname "$doc_path")"
  cat > "$doc_path" <<EOF
---
work_item_ids: [${item}]
node_id: $(field 'Node')
hook_point: ${hook}
kind: ${kind}s
${labels}title: fake ${kind}
---

${body}
EOF
fi

if [ -n "${KRAFT_RESULT_PATH:-}" ]; then
  # Beside the result and renamed onto it, as a harness writing atomically
  # does: a sandbox mounts the session's whole out/ folder (Kraft-l9ive).
  rtmp="$(mktemp "$KRAFT_RESULT_PATH.XXXXXX")"
  # ponytail: no JSON-string escaping on these values -- test-controlled
  # inputs only. Add escaping (or a
  # `python -c` json.dumps helper) if a caller ever needs a quote/backslash in
  # KRAFT_FAKE_CLAUDE_CONCERNS/_QUESTION.
  json="{\"status\": \"$status\""
  if [ -n "${KRAFT_FAKE_CLAUDE_CONCERNS:-}" ]; then
    json="$json, \"concerns\": \"${KRAFT_FAKE_CLAUDE_CONCERNS}\""
  fi
  if [ -n "${KRAFT_FAKE_CLAUDE_QUESTION:-}" ]; then
    json="$json, \"question\": \"${KRAFT_FAKE_CLAUDE_QUESTION}\""
  fi
  json="$json}"
  printf '%s' "$json" > "$rtmp"
  mv "$rtmp" "$KRAFT_RESULT_PATH"
fi

# The rest of the stream, in the real CLI's shapes: after the init line above,
# one assistant line carrying a request_id and per-request usage, then the
# result envelope last (usage capture and `_envelope_is_error` both read the
# last line). KRAFT_FAKE_CLAUDE_STREAM_DELAY holds the stream open between the
# first line and the rest, so a test can read the log while the child still runs.
sleep "${KRAFT_FAKE_CLAUDE_STREAM_DELAY:-0}"
# Tokens follow the cost, so a dearer run reads as a bigger one (0.035 is 1000 in, 200 out).
tin="$(awk -v c="$cost" 'BEGIN { printf "%.0f", 1000 * c / 0.035 }')"
tout="$(awk -v c="$cost" 'BEGIN { printf "%.0f", 200 * c / 0.035 }')"
if [ -n "$subagents" ]; then
  if [ -z "$subagents_started" ]; then print_subagent_starts; fi
  print_subagent_work
fi
printf '{"type":"assistant","request_id":"req_1","message":{"model":"fake-agent","usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":500}}}\n'
printf '{"type":"result","is_error":false,"total_cost_usd":%s,"modelUsage":{"fake-agent":{"inputTokens":%s,"outputTokens":%s,"cacheReadInputTokens":500}},"usage":{"input_tokens":%s,"output_tokens":%s,"cache_read_input_tokens":500}}\n' "$cost" "$tin" "$tout" "$tin" "$tout"
