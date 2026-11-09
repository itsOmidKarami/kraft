# ACP checks

## REQ doctor-shows-what-an-acp-agent-offers
WHEN `kraft admin doctor` runs, the system SHALL show, for each ACP harness a chain selects, the agent's `initialize` answer (resume, load, system prompt, config options) and whether its login works, without sending a prompt.
enforced-by: tests/acp/test_probe.py::test_the_handshake_reports_what_the_agent_offers[full], tests/acp/test_probe.py::test_the_handshake_reports_what_the_agent_offers[needs-login], tests/test_doctor_acp.py::test_doctor_shows_what_an_acp_agent_offers_and_flags_unreported_cost
origin: src/kraft/acp/probe.py

## REQ doctor-flags-an-acp-entry-that-reports-no-cost
WHERE an ACP entry's `reports` omits `cost`, `kraft admin doctor` SHALL warn that its spend may be unknown under a dollar cap.
enforced-by: tests/test_doctor_acp.py::test_doctor_shows_what_an_acp_agent_offers_and_flags_unreported_cost
origin: src/kraft/doctor.py

## REQ claude-acp-entry-asks-in-manual-mode-and-denies-monitor
The Claude-over-ACP example entry SHALL make the bridge ask by setting its mode to `default`, report tokens and cost, and always deny `Monitor`.
enforced-by: tests/acp/test_example_entries.py::test_the_claude_entry_asks_in_manual_mode_and_always_denies_monitor
origin: fixtures/claude-acp.yaml

## REQ claude-acp-entry-refuses-a-limit-that-denies-a-tool-the-bridge-settles
IF a task's tool limit would deny, or leave out of an allowlist, a tool the Claude-over-ACP entry lists in `settles_itself`, THEN the system SHALL refuse the launch before it starts.
enforced-by: tests/acp/test_example_entries.py::test_the_claude_entry_refuses_a_limit_that_denies_a_tool_the_bridge_settles[allowlist-omits-a-settled-tool]
origin: fixtures/claude-acp.yaml
