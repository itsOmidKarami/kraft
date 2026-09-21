from kraft.executor import prompts
from kraft.findings import Finding, JobRef
from kraft.templates.models import AgentTask


def _task(skill: str | None = None) -> AgentTask:
    """An agent task; `skill` set means it states its own method."""
    return AgentTask.model_validate(
        {"id": "t", "kind": "agent", "harness": "claude", "prompt": "p", "skill": skill}
    )


def test_attachment_note_keeps_the_imperative_for_the_implementer():
    out = prompts.attachment_note([{"kind": "plan", "path": "p.md"}])
    assert "Follow the documents above" in out
    assert "Do not re-plan." in out


def test_attachment_note_drops_the_imperative_when_the_hook_has_its_own_method():
    out = prompts.attachment_note([{"kind": "plan", "path": "p.md"}], method_is_own=True)
    assert "Plan: p.md" in out  # the paths still reach the hook
    assert "Follow the documents above" not in out
    assert "Do not re-plan" not in out


def test_attachment_note_is_empty_either_way_with_no_attachments():
    assert prompts.attachment_note([]) == ""
    assert prompts.attachment_note([], method_is_own=True) == ""


def test_method_note_names_the_section_not_a_position():
    # The instruction is sent twice -- as the `-p` argv and inside the system
    # prompt -- and `## Method` is appended only to the second. "below" would
    # point at nothing in the argv copy.
    assert "## Method" in prompts.METHOD_NOTE
    assert "below" not in prompts.METHOD_NOTE.lower()


def test_method_note_does_not_forbid_verifying():
    # Review hooks are asked by carried_findings_note to decide whether a past
    # finding is still present, which is verification.
    lowered = prompts.METHOD_NOTE.lower()
    assert "verify" not in lowered and "judge" not in lowered


_SCOPES = {
    "test_scopes": [
        {"paths": ["frontend/**"], "command": "just test-ui"},
        {"paths": ["frontend/**"], "command": "just e2e-ci"},
        {"paths": ["src/**", "tests/**"], "command": "just ci-test"},
    ]
}


def test_scope_note_lists_every_scope_command_for_the_implementer():
    out = prompts.scope_note(_task(), _SCOPES)
    for cmd in ("just test-ui", "just e2e-ci", "just ci-test"):
        assert cmd in out
    assert "frontend/**" in out


def test_scope_note_is_empty_for_any_other_hook():
    """V1 keys this on the task stating a method of its own (a `skill:`), not
    on a hook name: every task with a skill is "any other hook" now."""
    for skill in ("kraft:mr-description", "kraft:code-review", "kraft:spec"):
        assert prompts.scope_note(_task(skill), _SCOPES) == ""


def test_scope_note_is_empty_with_no_scopes_configured():
    assert prompts.scope_note(_task(), {}) == ""
    assert prompts.scope_note(_task(), None) == ""


def test_scope_note_handles_a_legacy_bare_test_command():
    # config.load_repos wraps a bare test_command into a ["**"] scope, but a
    # LaunchContext built by hand may not have been through that.
    out = prompts.scope_note(_task(), {"test_command": "just test"})
    assert "just test" in out


def test_format_findings_renders_job_refs():
    found = [
        Finding(
            severity="critical",
            message="on.test.run failed: 2 job(s)",
            file=None,
            line=None,
            source_plugin="on.test.run",
            jobs=(
                JobRef(label="just test-ui", log_ref="kraft view logs w1 --session s1"),
                JobRef(label="just e2e-ci", log_ref="kraft view logs w1 --session s2"),
            ),
        )
    ]
    text = prompts.format_findings(found, repeats=set())
    assert "just test-ui → kraft view logs w1 --session s1" in text
    assert "just e2e-ci → kraft view logs w1 --session s2" in text
