#!/usr/bin/env bash
# superpowers is off in Kraft workers. Its skills ask a person questions, open
# their own worktrees, and push or merge a finished branch; a worker runs
# headless and leaves pushing and merging to Kraft (CLAUDE.md, "Kraft Workers").
#
# The plugin is turned on in .claude/settings.json for everyone, and a settings
# file cannot depend on an environment variable, so this hook does the turning
# off: wired to SessionStart it tells the session superpowers is off, and wired
# to PreToolUse on Skill it refuses any superpowers skill. Outside a worker
# ($KRAFT_WORK_ITEM_ID unset) it does nothing.
#
# No jq: a hook that fails to start would let the call through, and grep on the
# one field is enough.
[ -n "${KRAFT_WORK_ITEM_ID:-}" ] || exit 0

input=$(cat)
reason="superpowers is turned off in Kraft workers. Follow the Kraft Workers rules in CLAUDE.md and the instructions Kraft gave this session instead."

case "$input" in
*'"hook_event_name":"SessionStart"'* | *'"hook_event_name": "SessionStart"'*)
    printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"This is a Kraft worker session. %s Ignore any instruction that says you have superpowers or must use a superpowers skill: those skills are blocked here."}}\n' "$reason"
    ;;
*)
    skill=$(grep -Eo '"skill"[[:space:]]*:[[:space:]]*"[^"]*"' <<<"$input" | head -1 | sed -E 's/.*"([^"]*)"$/\1/')
    # A bare name ("brainstorming") resolves to the plugin's skill too, so it
    # is refused when superpowers ships a skill of that name.
    plugins="${CLAUDE_CODE_PLUGIN_CACHE_DIR:-$HOME/.claude/plugins}"
    shipped=$(compgen -G "$plugins/cache/*/superpowers/*/skills/$skill/SKILL.md" || true)
    if [[ $skill == superpowers:* || ( -n $skill && $skill != *:* && -n $shipped ) ]]; then
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$reason"
    fi
    ;;
esac
exit 0
