#!/usr/bin/env bash
# Stand-in for `claude` headless. Invoked as:
#   fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format stream-json --verbose
# CWD is the worktree. Modes via KRAFT_FAKE_CLAUDE: fix (default) | noop | slow.
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

# Per-invocation failure. KRAFT_FAKE_CLAUDE is per-server, so it cannot make one
# work item fail while its neighbours succeed; the instruction can. The seed
# script puts KRAFT_FAIL in a work item title to drive the needs_human path.
for arg in "$@"; do
  case "$arg" in
    *KRAFT_FAIL*) printf 'fake-claude: asked to fail\n' >&2; exit 3 ;;
    # break: the title reaches this script twice (-p and --append-system-prompt),
    # and sleeping once per copy doubles the delay the seed script waits on.
    *KRAFT_SLOW*) sleep "${KRAFT_FAKE_CLAUDE_DELAY:-15}"; break ;;
  esac
done

# The adapter runs the real CLI with `--output-format stream-json --verbose`,
# which opens with an init line, before any work -- so a session paused
# mid-run has already named itself. KRAFT_FAKE_CLAUDE_SESSION_ID is the
# provider session id that line carries, the one `--resume` takes.
printf '{"type":"system","subtype":"init","model":"fake-agent","tools":[]%s}\n' \
  "${KRAFT_FAKE_CLAUDE_SESSION_ID:+,\"session_id\":\"$KRAFT_FAKE_CLAUDE_SESSION_ID\"}"

if [ "$mode" = "slow" ]; then
  sleep "${KRAFT_FAKE_CLAUDE_DELAY:-10}"
  mode="fix"
fi

if [ "$mode" = "fix" ] && [ -f calc.py ]; then
  # portable in-place edit: rewrite the file
  tmp="$(mktemp)"
  sed 's/a - b/a + b/' calc.py > "$tmp" && mv "$tmp" calc.py
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

summary_ref=""
session="$(field 'Worker session')"
if [ -n "$session" ]; then
  summary_ref=".engineering/sessions/${session}.md"
  mkdir -p .engineering/sessions
  cat > "$summary_ref" <<EOF
---
work_item_ids: [$(field 'Work item')]
node_id: $(field 'Node')
hook_point: $(field 'Hook point')
worker_session_id: ${session}
---

fake-claude session
EOF
fi

# A task that `produces:` a document is contractually required to write and
# commit it. Honour that, so the gate, the artifact endpoint and the indexer all
# see the real inputs.
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
if [ -n "$kind" ] && [ -n "$item" ] && [ -n "$write_artifact" ]; then
  mkdir -p ".engineering/${kind}s"
  cat > ".engineering/${kind}s/${item}.md" <<EOF
---
work_item_ids: [${item}]
node_id: $(field 'Node')
hook_point: ${hook}
kind: ${kind}s
${labels}title: fake ${kind}
---

${body}
EOF
  git add ".engineering/${kind}s/${item}.md" >/dev/null 2>&1 || true
  git -c user.name=fake -c user.email=fake@kraft \
      commit -q -m "fake ${kind}" -- ".engineering/${kind}s/${item}.md" >/dev/null 2>&1 || true
fi

if [ -n "${KRAFT_RESULT_PATH:-}" ]; then
  rtmp="$(mktemp)"
  # ponytail: no JSON-string escaping on these values, same as the pre-existing
  # summary_ref line below -- test-controlled inputs only. Add escaping (or a
  # `python -c` json.dumps helper) if a caller ever needs a quote/backslash in
  # KRAFT_FAKE_CLAUDE_CONCERNS/_QUESTION.
  json="{\"status\": \"$status\""
  if [ -n "${KRAFT_FAKE_CLAUDE_CONCERNS:-}" ]; then
    json="$json, \"concerns\": \"${KRAFT_FAKE_CLAUDE_CONCERNS}\""
  fi
  if [ -n "${KRAFT_FAKE_CLAUDE_QUESTION:-}" ]; then
    json="$json, \"question\": \"${KRAFT_FAKE_CLAUDE_QUESTION}\""
  fi
  if [ -n "$summary_ref" ]; then
    json="$json, \"session_summary_ref\": \"${summary_ref}\""
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
printf '{"type":"assistant","request_id":"req_1","message":{"model":"fake-agent","usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":500}}}\n'
printf '{"type":"result","is_error":false,"total_cost_usd":0.035,"modelUsage":{"fake-agent":{"inputTokens":1500,"outputTokens":200}},"usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":500}}\n'
