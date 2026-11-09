# ACP checks

## REQ doctor-shows-what-an-acp-agent-offers
WHEN `kraft admin doctor` runs, the system SHALL show, for each ACP harness a chain selects, the agent's `initialize` answer (resume, load, system prompt, config options) and whether its login works, without sending a prompt.
enforced-by: tests/acp/test_probe.py::test_the_handshake_reports_what_the_agent_offers[full], tests/acp/test_probe.py::test_the_handshake_reports_what_the_agent_offers[needs-login], tests/test_doctor_acp.py::test_doctor_shows_what_an_acp_agent_offers_and_flags_unreported_cost
origin: src/kraft/acp/probe.py

## REQ doctor-flags-an-acp-entry-that-reports-no-cost
WHERE an ACP entry's `reports` omits `cost`, `kraft admin doctor` SHALL warn that its spend may be unknown under a dollar cap.
enforced-by: tests/test_doctor_acp.py::test_doctor_shows_what_an_acp_agent_offers_and_flags_unreported_cost
origin: src/kraft/doctor.py
