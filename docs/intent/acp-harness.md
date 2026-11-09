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
