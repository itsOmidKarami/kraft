#!/usr/bin/env bash
# Stand-in for `claude` headless. Invoked as:
#   fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format json
# CWD is the worktree. Modes via KRAFT_FAKE_CLAUDE: fix (default) | noop | slow.
# Always writes $KRAFT_RESULT_PATH so an adopted session can resolve after a restart.
set -eu

mode="${KRAFT_FAKE_CLAUDE:-fix}"

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

if [ -n "${KRAFT_RESULT_PATH:-}" ]; then
  rtmp="$(mktemp)"
  if [ -n "$summary_ref" ]; then
    printf '{"status": "done", "session_summary_ref": "%s"}' "$summary_ref" > "$rtmp"
  else
    printf '{"status": "done"}' > "$rtmp"
  fi
  mv "$rtmp" "$KRAFT_RESULT_PATH"
fi

# A real agent's final envelope carries its token usage; usage capture parses
# this line, so the fake has to carry it too or nothing downstream is exercised.
printf '{"type": "result", "is_error": false, "model": "fake-agent", "total_cost_usd": 0.035, "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500}}\n'
