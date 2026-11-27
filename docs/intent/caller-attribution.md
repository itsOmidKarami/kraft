# Caller attribution

Who a call is recorded as: a person, an assistant (an MCP session or a coding
agent's shell) or a Kraft worker.

## REQ shell-agent-gate-decision-is-the-assistants

WHEN a gate is decided by a `kraft` call made from a coding agent's shell, the system SHALL record the decision as made by an assistant.
enforced-by: tests/api/test_gates.py::test_a_gate_decision_records_its_caller[agent-shell-approve], tests/api/test_gates.py::test_a_gate_decision_records_its_caller[agent-shell-reject], tests/client/test_transport.py::test_agent_shell_environments_tag_the_call[claude-code-child-session]
origin: src/kraft/api/routes/gates.py §_decided_by

## REQ explicit-client-outranks-shell-detection

WHERE `KRAFT_CLIENT` is set, the `kraft` client SHALL send its value as the caller's client whether or not an agent's shell is detected.
enforced-by: tests/client/test_transport.py::test_an_explicit_kraft_client_outranks_shell_detection[cli], tests/client/test_transport.py::test_an_explicit_kraft_client_outranks_shell_detection[mcp]
origin: src/kraft/client/transport.py §http

## REQ refused-person-only-call-does-not-name-the-override

IF a person-only call is refused because its caller is an assistant, THEN the refusal message SHALL NOT name `KRAFT_CLIENT` or any way to be recorded as a person.
enforced-by: tests/api/test_lifecycle.py::test_a_refused_person_only_call_does_not_name_the_override[retry-sandbox-from-config], tests/api/test_lifecycle.py::test_a_refused_person_only_call_does_not_name_the_override[chain-draft], tests/api/test_lifecycle.py::test_a_refused_person_only_call_does_not_name_the_override[repo-forge-account], tests/api/test_work_items_node_overrides.py::test_a_node_extra_prompt_is_a_persons_to_set[create-shell-agent], tests/api/test_work_items_node_overrides.py::test_a_node_extra_prompt_is_a_persons_to_set[patch-shell-agent], tests/api/test_retry_paths.py::test_a_retry_that_changes_more_than_a_tasks_model_is_a_persons[shell-agent-prompt], tests/api/test_repos_held.py::test_turning_a_repo_on_is_a_persons_call[shell-agent-enables]
origin: src/kraft/api/routes/lifecycle.py §_reread_before_retry

## REQ plain-terminal-sends-no-client

IF no agent's shell is detected and `KRAFT_CLIENT` is unset, THEN the `kraft` client SHALL NOT send a client header.
enforced-by: tests/client/test_transport.py::test_a_plain_terminal_sends_no_client_header
origin: src/kraft/client/transport.py §http
