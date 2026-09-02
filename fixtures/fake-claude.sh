#!/usr/bin/env bash
# Stand-in for `claude` headless. Invoked as:
#   fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format json
# CWD is the worktree. Modes via KRAFT_FAKE_CLAUDE: fix (default) | noop | slow.
# Always writes $KRAFT_RESULT_PATH so an adopted session can resolve after a restart.
set -eu

mode="${KRAFT_FAKE_CLAUDE:-fix}"

if [ "$mode" = "slow" ]; then
  sleep "${KRAFT_FAKE_CLAUDE_DELAY:-10}"
  mode="fix"
fi

if [ "$mode" = "fix" ] && [ -f calc.py ]; then
  # portable in-place edit: rewrite the file
  tmp="$(mktemp)"
  sed 's/a - b/a + b/' calc.py > "$tmp" && mv "$tmp" calc.py
fi

if [ -n "${KRAFT_RESULT_PATH:-}" ]; then
  rtmp="$(mktemp)"
  printf '{"status": "done"}' > "$rtmp" && mv "$rtmp" "$KRAFT_RESULT_PATH"
fi

printf '{"type": "result", "is_error": false}\n'
