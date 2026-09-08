#!/usr/bin/env bash
# Stand-in for `claude` headless. Invoked as:
#   fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format stream-json --verbose
# CWD is the worktree. Modes via KRAFT_FAKE_CLAUDE: fix (default) | noop | slow.
# The reported status defaults to "done"; KRAFT_FAKE_CLAUDE_STATUS overrides it,
# and KRAFT_FAKE_CLAUDE_CONCERNS / KRAFT_FAKE_CLAUDE_QUESTION set the field that
# goes with done_with_concerns / needs_context.
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

if [ "$mode" = "slow" ]; then
  sleep "${KRAFT_FAKE_CLAUDE_DELAY:-10}"
  mode="fix"
fi

if [ "$mode" = "fix" ] && [ -f calc.py ]; then
  # portable in-place edit: rewrite the file
  tmp="$(mktemp)"
  sed 's/a - b/a + b/' calc.py > "$tmp" && mv "$tmp" calc.py
fi

# The agent adapter injects the work-item/node/session linkage into the system
# prompt and asks for a session summary (04 §6). Obey it when those fields are
# present, so integration tests exercise the real ingestion inputs.
ctx=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--append-system-prompt" ] && [ "$#" -gt 1 ]; then ctx="$2"; fi
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

# A hook with `artifact:` in the registry is contractually required to write and
# commit a document. Honour it for the two planning hooks, so the gate, the
# artifact endpoint and the indexer all see the real inputs.
hook="$(field 'Hook point')"
item="$(field 'Work item')"
case "$hook" in
  on.spec.requested) kind="spec" ;;
  on.plan.requested) kind="plan" ;;
  *) kind="" ;;
esac
if [ -n "$kind" ] && [ -n "$item" ]; then
  mkdir -p ".engineering/${kind}s"
  cat > ".engineering/${kind}s/${item}.md" <<EOF
---
work_item_ids: [${item}]
node_id: $(field 'Node')
hook_point: ${hook}
kind: ${kind}s
title: fake ${kind}
---

fake ${kind} body
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

# The adapter runs the real CLI with `--output-format stream-json --verbose`, so
# the fake streams the same shapes: an init line carrying the model, one
# assistant line carrying a request_id and per-request usage, then the result
# envelope last (usage capture and `_envelope_is_error` both read the last
# line). KRAFT_FAKE_CLAUDE_STREAM_DELAY holds the stream open between the first
# line and the rest, so a test can read the log while the child still runs.
printf '{"type":"system","subtype":"init","model":"fake-agent","tools":[]}\n'
sleep "${KRAFT_FAKE_CLAUDE_STREAM_DELAY:-0}"
printf '{"type":"assistant","request_id":"req_1","message":{"model":"fake-agent","usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":500}}}\n'
printf '{"type":"result","is_error":false,"total_cost_usd":0.035,"modelUsage":{"fake-agent":{"inputTokens":1500,"outputTokens":200}},"usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":500}}\n'
