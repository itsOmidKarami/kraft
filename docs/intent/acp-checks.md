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

## REQ acp-probe-judges-each-call-by-what-the-entry-declares
IF the entry declares `asks_everything` and a probe tool call ran without an ask while its Kraft tool is not in `settles_itself`, THEN the probe SHALL report a mismatch naming the tool.
enforced-by: tests/acp/test_conformance.py::test_the_probe_judges_each_call_by_what_the_entry_declares[unasked-and-undeclared], tests/acp/test_conformance.py::test_the_probe_judges_each_call_by_what_the_entry_declares[unasked-and-declared-settled]
origin: src/kraft/acp/conformance.py

## REQ acp-probe-judges-reported-usage-by-the-entry
IF the entry's `reports` declares tokens or cost and the probe session sent none, THEN the probe SHALL report a mismatch naming which.
enforced-by: tests/acp/test_conformance.py::test_the_probe_judges_reported_usage_by_the_entry[tokens-missing], tests/acp/test_conformance.py::test_the_probe_judges_reported_usage_by_the_entry[cost-missing]
origin: src/kraft/acp/conformance.py

## REQ acp-probe-notes-usage-the-entry-does-not-declare
WHEN the agent reports tokens or cost that the entry's `reports` does not declare, the probe SHALL note it and SHALL NOT call it a mismatch.
enforced-by: tests/acp/test_conformance.py::test_the_probe_judges_reported_usage_by_the_entry[undeclared-cost], tests/acp/test_conformance.py::test_the_probe_judges_reported_usage_by_the_entry[undeclared-tokens]
origin: src/kraft/acp/conformance.py

## REQ acp-probe-runs-kraft-client-in-a-scratch-folder-and-enforces-nothing
WHEN an operator runs the conformance probe, the system SHALL run Kraft's own ACP client against the agent in a scratch folder with the entry's asking switch applied, ask the agent to read a file, write a file, run a command, glob and grep, answer every ask `allow_once` without the permission gate even when the entry always denies a tool, report the agent's version, and kill the client's process group when the probe is cancelled.
enforced-by: tests/acp/test_conformance.py::test_the_probe_runs_the_client_against_the_agent_and_judges_the_log, tests/acp/test_conformance.py::test_the_probe_sends_no_host_access_enforces_no_policy_and_sets_no_model, tests/acp/test_conformance.py::test_the_probe_enforces_nothing_even_when_the_entry_always_denies_a_tool, tests/acp/test_conformance.py::test_cancelling_the_probe_kills_the_client_and_the_agent
origin: src/kraft/acp/conformance.py

## REQ acp-probe-verb-exits-one-on-a-mismatch
WHEN `kraft admin acp-probe` finds a problem or a mismatch between the session and the entry, the system SHALL exit 1 after printing the report, and SHALL exit 0 when the session conforms.
enforced-by: tests/cli/test_admin_acp_probe.py::test_the_verb_exits_one_on_a_mismatch[conforms], tests/cli/test_admin_acp_probe.py::test_the_verb_exits_one_on_a_mismatch[a-call-ran-unasked]
origin: src/kraft/cli/admin.py
