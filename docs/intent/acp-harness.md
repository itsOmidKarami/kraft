# ACP harness entries

A harness whose `kind` is `acp` runs an Agent Client Protocol agent through Kraft's own client, so a
new agent is one YAML file declaring only what ACP cannot tell Kraft. This capability covers the
entry, where an ACP launch may and may not go, and what the client and its log promise.

## REQ acp-entry-declares-only-what-acp-cannot-tell
The system SHALL load a `kind: acp` harness file from its command, `reports`, `asks_everything`, `tool_names`, `always_deny`, `options`, `network` and `credentials`, and SHALL refuse one that carries a `kind: cli`-only key.
enforced-by: tests/acp/test_entry.py::test_an_acp_entry_loads_with_the_capabilities_kraft_reads, tests/acp/test_entry.py::test_a_malformed_acp_entry_is_refused_naming_the_key[capabilities-given], tests/acp/test_entry.py::test_a_malformed_acp_entry_is_refused_naming_the_key[permission-hook], tests/acp/test_entry.py::test_a_command_given_as_one_string_is_one_word_not_split
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
