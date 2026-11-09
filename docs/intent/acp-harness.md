# ACP harness entries

A harness whose `kind` is `acp` runs an Agent Client Protocol agent through Kraft's own client, so a
new agent is one YAML file declaring only what ACP cannot tell Kraft. This capability covers the
entry, where an ACP launch may and may not go, and what the client and its log promise.

## REQ acp-entry-declares-only-what-acp-cannot-tell
The system SHALL load a `kind: acp` harness file from its command, `reports`, `asks_everything`, `tool_names`, `always_deny`, `settles_itself`, `options`, `network` and `credentials`, and SHALL refuse one that carries a `kind: cli`-only key.
enforced-by: tests/acp/test_entry.py::test_an_acp_entry_loads_with_the_capabilities_kraft_reads, tests/acp/test_entry.py::test_a_malformed_acp_entry_is_refused_naming_the_key[capabilities-given], tests/acp/test_entry.py::test_a_malformed_acp_entry_is_refused_naming_the_key[permission-hook], tests/acp/test_entry.py::test_a_command_given_as_one_string_is_one_word_not_split, tests/acp/test_entry.py::test_a_malformed_acp_entry_is_refused_naming_the_key[settles-itself-not-a-list]
origin: src/kraft/acp/entry.py

## REQ acp-entry-reports-are-tokens-or-cost
IF an ACP entry's `reports` names anything but `tokens` or `cost`, THEN the system SHALL refuse the file at load.
enforced-by: tests/acp/test_entry.py::test_a_malformed_acp_entry_is_refused_naming_the_key[unknown-report]
origin: src/kraft/acp/entry.py

## REQ acp-harness-is-never-a-fallback
IF a `fallback:` entry resolves to a `kind: acp` harness, THEN the system SHALL refuse a profile's list at load and SHALL skip a task's list entry as unavailable at launch.
enforced-by: tests/test_fallback_profiles.py::test_a_profile_fallback_naming_an_acp_harness_is_refused_at_load, tests/test_fallback_profiles.py::test_a_task_fallback_candidate_on_an_acp_harness_is_unavailable, tests/executor/test_launch_fallback.py::test_a_task_fallback_naming_an_acp_harness_is_skipped_as_unavailable
origin: src/kraft/executor/fallback.py

## REQ acp-client-advertises-no-host-access
The system SHALL advertise no `fs` and no `terminal` capability to an ACP agent.
enforced-by: tests/acp/test_wire.py::test_initialize_advertises_no_fs_and_no_terminal
origin: src/kraft/acp/wire.py

## REQ acp-line-reading-is-bounded-and-tolerant
WHEN an ACP agent prints a line that is not JSON-RPC, or one longer than the line limit, the system SHALL skip it and log it, and SHALL NOT fail the session.
enforced-by: tests/acp/test_wire.py::test_read_line_never_raises_on_what_an_agent_prints[help-text], tests/acp/test_wire.py::test_a_line_past_the_limit_is_skipped_not_read_whole
origin: src/kraft/acp/wire.py

## REQ acp-launch-refuses-tool-limits-it-cannot-hold
IF a launch on a `kind: acp` harness sets `permission_mode`, or holds a named grant alongside a limit that denies `Bash`, or holds a tool policy on an entry that declares no `asks_everything`, or one its `tool_names` cannot tell apart, or one that denies a tool its `settles_itself` names, THEN the system SHALL refuse it before it starts, saying why.
enforced-by: tests/acp/test_launch.py::test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason[permission-mode], tests/acp/test_launch.py::test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason[grant-with-bash-denied], tests/acp/test_launch.py::test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason[grant-with-allowlist-omitting-bash], tests/acp/test_launch.py::test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason[deny-list-no-asks], tests/acp/test_launch.py::test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason[allowlist-splits-a-kind], tests/acp/test_launch.py::test_a_tool_limit_the_entry_cannot_hold_is_refused_with_its_reason[deny-of-a-settled-tool], tests/adapters/test_agent_acp.py::test_the_launch_door_refuses_what_it_cannot_hold
origin: src/kraft/acp/launch.py

## REQ acp-launch-accepts-a-tool-policy-the-entry-can-hold
WHERE the entry declares `asks_everything`, the system SHALL launch a task with an allowlist (even an empty one), a deny list or `always_deny`.
enforced-by: tests/acp/test_launch.py::test_a_tool_limit_the_entry_can_hold_is_not_refused[allowlist], tests/acp/test_launch.py::test_a_tool_limit_the_entry_can_hold_is_not_refused[empty-allowlist], tests/acp/test_launch.py::test_a_tool_limit_the_entry_can_hold_is_not_refused[deny-list], tests/acp/test_launch.py::test_a_tool_limit_the_entry_can_hold_is_not_refused[name-covering-two-tools-is-the-gates-also-rule]
origin: src/kraft/acp/launch.py

## REQ acp-launch-accepts-a-grant-that-changes-nothing
WHERE a launch on a `kind: acp` harness holds a named grant and no limit that denies `Bash` (an escalation turn's default grants, with no tool policy or with a policy that leaves `Bash` allowed), the system SHALL launch it.
enforced-by: tests/acp/test_launch.py::test_a_tool_limit_the_entry_can_hold_is_not_refused[grants-with-no-policy], tests/acp/test_launch.py::test_a_tool_limit_the_entry_can_hold_is_not_refused[grants-with-a-deny-that-leaves-bash]
origin: src/kraft/acp/launch.py

## REQ acp-launch-hands-the-client-the-tool-policy
WHEN Kraft launches a `kind: acp` harness, the system SHALL put the entry's asking switch, tool-name mapping, `always_deny` and `settles_itself`, and whether the task holds a limit and an allowlist, in the client's spec file, with or without a policy.
enforced-by: tests/adapters/test_agent_acp.py::test_an_acp_launch_hands_its_tool_policy_to_the_client[allowlist], tests/adapters/test_agent_acp.py::test_an_acp_launch_hands_its_tool_policy_to_the_client[deny-only], tests/adapters/test_agent_acp.py::test_an_acp_launch_hands_its_tool_policy_to_the_client[always-deny-alone], tests/adapters/test_agent_acp.py::test_an_acp_launch_hands_its_tool_policy_to_the_client[no-policy-still-asks]
origin: src/kraft/acp/launch.py

## REQ acp-launch-door-leaves-placement-and-sandbox-to-their-own-rules
The system SHALL NOT refuse a launch on a `kind: acp` harness for being sandboxed or placed on a remote machine; a sandbox without `network:` stays refused for every harness.
enforced-by: tests/adapters/test_agent_acp.py::test_an_acp_launch_in_a_sandbox_or_on_a_machine_runs_or_is_refused_by_the_general_rule[sandbox-with-network-runs], tests/adapters/test_agent_acp.py::test_an_acp_launch_in_a_sandbox_or_on_a_machine_runs_or_is_refused_by_the_general_rule[sandbox-without-network-keeps-the-general-refusal], tests/adapters/test_agent_acp.py::test_an_acp_launch_in_a_sandbox_or_on_a_machine_runs_or_is_refused_by_the_general_rule[machine-runs]
origin: src/kraft/acp/launch.py

## REQ acp-session-command-is-the-client-not-the-agent
WHEN Kraft launches a `kind: acp` harness locally, the system SHALL run Kraft's client as the session command and hand it the agent's argv in a spec file, and SHALL NOT run the agent as the session command.
enforced-by: tests/adapters/test_agent_acp.py::test_an_acp_launch_runs_the_client_with_a_spec_and_the_agent_argv_in_it
origin: src/kraft/acp/launch.py
