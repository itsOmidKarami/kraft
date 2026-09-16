from kraft.executor import prompts


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
    out = prompts.scope_note("on.implementation.start", _SCOPES)
    for cmd in ("just test-ui", "just e2e-ci", "just ci-test"):
        assert cmd in out
    assert "frontend/**" in out


def test_scope_note_is_empty_for_any_other_hook():
    for hook in ("on.mr.describe", "on.review.local.run", "on.test.run"):
        assert prompts.scope_note(hook, _SCOPES) == ""


def test_scope_note_is_empty_with_no_scopes_configured():
    assert prompts.scope_note("on.implementation.start", {}) == ""
    assert prompts.scope_note("on.implementation.start", None) == ""


def test_scope_note_handles_a_legacy_bare_test_command():
    # config.load_repos wraps a bare test_command into a ["**"] scope, but a
    # LaunchContext built by hand may not have been through that.
    out = prompts.scope_note("on.implementation.start", {"test_command": "just test"})
    assert "just test" in out
