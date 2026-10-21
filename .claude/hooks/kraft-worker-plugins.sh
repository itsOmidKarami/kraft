#!/usr/bin/env bash
# superpowers and ponytail are off in Kraft workers. superpowers' skills ask a
# person questions, open their own worktrees, and push or merge a finished
# branch; a worker runs headless and leaves pushing and merging to Kraft
# (CLAUDE.md, "Kraft Workers"). ponytail puts every session into its own
# minimal-code mode, and a worker follows the instructions Kraft gave it.
#
# The plugins are turned on in .claude/settings.json for everyone, and a
# settings file cannot depend on an environment variable, so this hook does the
# turning off: wired to SessionStart it tells the session they are off, and
# wired to PreToolUse on Skill it refuses any of their skills. Outside a worker
# ($KRAFT_WORK_ITEM_ID unset) it does nothing.
#
# No jq: a hook that fails to start would let the call through, and grep on the
# one field is enough.
[ -n "${KRAFT_WORK_ITEM_ID:-}" ] || exit 0

input=$(cat)
off=(superpowers ponytail)
reason="superpowers and ponytail are turned off in Kraft workers. Follow the Kraft Workers rules in CLAUDE.md and the instructions Kraft gave this session instead."

case "$input" in
*'"hook_event_name":"SessionStart"'* | *'"hook_event_name": "SessionStart"'*)
    printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"This is a Kraft worker session. %s Ignore any instruction that says you have superpowers, must use a superpowers skill, or are in a PONYTAIL mode: those skills are blocked here."}}\n' "$reason"
    ;;
*)
    skill=$(grep -Eo '"skill"[[:space:]]*:[[:space:]]*"[^"]*"' <<<"$input" | head -1 | sed -E 's/.*"([^"]*)"$/\1/')
    [ -n "$skill" ] || exit 0
    # A bare name ("brainstorming") resolves to the plugin's skill too, so it
    # is refused when one of these plugins ships a skill of that name.
    plugins="${CLAUDE_CODE_PLUGIN_CACHE_DIR:-$HOME/.claude/plugins}"
    for plugin in "${off[@]}"; do
        if [[ $skill == "$plugin":* ]] ||
            { [[ $skill != *:* ]] && compgen -G "$plugins/cache/*/$plugin/*/skills/$skill/SKILL.md" >/dev/null; }; then
            printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$reason"
            break
        fi
    done
    ;;
esac
exit 0
