from pathlib import Path

import pytest

from kraft import harness


def test_bundled_harnesses_all_load():
    hs = harness.load(None)
    assert hs.invalid == {}
    assert sorted(hs.valid) == ["claude", "codex", "gemini"]


def test_claude_declares_the_leaked_claude_isms():
    """Spec leaks 1 and 2: Monitor and the permission tool are claude's facts,
    not the chain's, so they must live in claude.yaml as `always:`."""
    claude = harness.load(None).valid["claude"]
    assert claude.capabilities["deny_tools"].always == ("Monitor",)
    assert claude.capabilities["approval_channel"].always == "mcp__kraft__permission_request"


def test_codex_declares_no_deny_tools_and_gemini_no_resume():
    hs = harness.load(None).valid
    assert not hs["codex"].supports("deny_tools")
    assert not hs["gemini"].supports("resume")
    assert not hs["gemini"].supports("effort")


def test_context_channel_is_explicit_per_harness():
    hs = harness.load(None).valid
    assert hs["claude"].capabilities["context"].channel == "system_prompt"
    assert hs["codex"].capabilities["context"].channel == "system_prompt"
    # Gemini has no out-of-band channel at all -- the spec's one honest
    # admission of a weaker mechanism, and it must be visible, not inferred.
    assert hs["gemini"].capabilities["context"].channel == "prompt"


def test_values_are_fullmatch_patterns():
    claude = harness.load(None).valid["claude"]
    assert claude.value_ok("effort", "low")
    assert not claude.value_ok("effort", "lower")  # fullmatch, not search
    assert claude.value_ok("model", "claude-opus-5")
    assert claude.value_ok("model", "sonnet")
    assert not claude.value_ok("model", "gpt-5")
    # A capability with no `values:` accepts anything.
    assert claude.value_ok("prompt", "anything at all")


def _write(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "harnesses"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


_MINIMAL = """
id: {id}
kind: cli
command: [{id}]
capabilities:
  context: {{ channel: prompt }}
  usage:   {{ source: result_file }}
  prompt:  {{ cli: ["-p", "{{value}}"] }}
"""


def test_minimal_harness_is_valid(tmp_path):
    _write(tmp_path, "mini.yaml", _MINIMAL.format(id="mini"))
    hs = harness.load(tmp_path / "harnesses")
    assert hs.invalid == {}
    assert "mini" in hs.valid


@pytest.mark.parametrize(
    "body,expect",
    [
        ("id: x\nkind: wat\ncommand: [x]\ncapabilities: {}\n", "unknown kind"),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n  usage: { source: result_file }\n",
            "missing required capability 'prompt'",
        ),
        (
            "id: x\nkind: cli\ncommand: []\ncapabilities:\n  prompt: { cli: ['-p'] }\n",
            "'command' must be a non-empty",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n  usage: { source: result_file }\n"
            "  prompt: { cli: ['-p', '{value}'] }\n  telepathy: { cli: ['-t'] }\n",
            "unknown capability 'telepathy'",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n  usage: { source: result_file }\n"
            "  effort: { cli: ['-e', '{toml}'] }\n  prompt: { cli: ['-p', '{value}'] }\n",
            "placeholder {toml}",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n  usage: { source: result_file }\n"
            "  effort: { cli: ['-e', '{value}'], values: [low], always: high }\n"
            "  prompt: { cli: ['-p', '{value}'] }\n",
            "'always' value 'high' is not accepted",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n"
            "  usage: { source: envelope, reader: claude-stream-json }\n"
            "  prompt: { cli: ['-p', '{value}'] }\n",
            "'structured_log' must be declared",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n  usage: { source: result_file }\n"
            "  prompt: { cli: ['{value}'] }\n  model: { cli: ['-m', '{value}'] }\n",
            "must be the last declared capability",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: system_prompt }\n  usage: { source: result_file }\n"
            "  prompt: { cli: ['-p', '{value}'] }\n",
            "channel 'system_prompt' needs a 'cli' binding",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            "  context: { channel: prompt }\n  usage: { source: result_file }\n"
            "  resume: { via: command_resume }\n  prompt: { cli: ['-p', '{value}'] }\n",
            "which this harness does not define",
        ),
    ],
)
def test_malformed_harness_is_quarantined_with_its_reason(tmp_path, body, expect):
    _write(tmp_path, "x.yaml", body)
    hs = harness.load(tmp_path / "harnesses")
    assert "x" not in hs.valid
    assert expect in hs.invalid["x"]


def test_one_bad_file_does_not_take_the_others_down(tmp_path):
    """The `load_templates` precedent: quarantine by name, keep serving."""
    _write(tmp_path, "good.yaml", _MINIMAL.format(id="good"))
    _write(tmp_path, "bad.yaml", "id: bad\nkind: nope\ncommand: [b]\ncapabilities: {}\n")
    hs = harness.load(tmp_path / "harnesses")
    assert "good" in hs.valid
    assert "bad" in hs.invalid
    # The bundled three are still there too.
    assert {"claude", "codex", "gemini"} <= set(hs.valid)


def test_overlay_wins_and_takes_ownership(tmp_path):
    _write(tmp_path, "claude.yaml", _MINIMAL.format(id="claude"))
    hs = harness.load(tmp_path / "harnesses")
    # The operator's file replaces the shipped one entirely -- it does not
    # merge -- so the shipped `always: [Monitor]` is gone.
    assert not hs.valid["claude"].supports("deny_tools")
    assert hs.valid["claude"].capabilities["context"].channel == "prompt"


def test_id_must_match_file_name(tmp_path):
    _write(tmp_path, "wrong.yaml", _MINIMAL.format(id="right"))
    hs = harness.load(tmp_path / "harnesses")
    assert "does not match its file name" in hs.invalid["wrong"]


def _argv(hid, **kw):
    kw.setdefault("prompt", "do the thing")
    kw.setdefault("context", "CTX")
    return harness.build_argv(harness.load(None).valid[hid], **kw)


def test_claude_argv_matches_todays_invocation():
    argv = _argv("claude", options={"model": "sonnet", "effort": "medium"})
    assert argv[:2] == ["claude", "-p"]
    assert argv[2] == "do the thing"
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "CTX"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "medium"
    # `always:` reaches argv with no binding asking -- spec leaks 1 and 2.
    assert argv[argv.index("--disallowed-tools") + 1] == "Monitor"
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"


def test_codex_argv_puts_the_bare_prompt_last():
    argv = _argv("codex", options={"effort": "high"})
    assert argv[:2] == ["codex", "exec"]
    assert argv[-1] == "do the thing"
    assert "-c" in argv
    assert "developer_instructions=CTX" in argv
    assert "model_reasoning_effort=high" in argv
    # `always: workspace-write`, unasked.
    assert argv[argv.index("-s") + 1] == "workspace-write"


def test_gemini_folds_context_into_the_prompt():
    """channel: prompt -- the weaker channel, and the whole contract must
    still arrive. `prompt` is declared last in gemini.yaml (not a
    bare-positional, so no ordering requirement forces it earlier), so this
    checks content rather than position."""
    argv = _argv("gemini")
    assert argv[0] == "gemini"
    assert argv[argv.index("-p") + 1] == "CTX\n\ndo the thing"
    assert "--append-system-prompt" not in argv


def test_monitor_does_not_appear_for_a_harness_without_deny_tools():
    """The spec's clearest leak: `Monitor` was appended unconditionally in
    `agent.py:388`, so it would have reached every harness."""
    assert "Monitor" not in _argv("codex")
    assert "Monitor" not in _argv("gemini")


def test_deny_tools_unions_always_with_the_bindings_own():
    argv = _argv("claude", options={"deny_tools": ("WebFetch", "Monitor")})
    # Deduplicated, `always` first, one comma-joined value.
    assert argv[argv.index("--disallowed-tools") + 1] == "Monitor,WebFetch"


def test_resume_uses_command_resume_as_the_whole_prefix():
    argv = _argv("codex", resume="abc-123")
    assert argv[:4] == ["codex", "exec", "resume", "abc-123"]
    assert argv[-1] == "do the thing"


def test_resume_as_a_flag_when_that_is_how_the_harness_spells_it():
    argv = _argv("claude", resume="abc-123")
    assert argv[0] == "claude"
    assert argv[argv.index("--resume") + 1] == "abc-123"


def test_resume_is_ignored_by_a_harness_that_cannot_do_it():
    """Gemini's --resume takes an index, not an id, so it declares no resume
    capability. Escalation falls back to a fresh thread (spec: the gaps
    fail-at-load cannot reach); argv must simply not carry a foreign id."""
    argv = _argv("gemini", resume="abc-123")
    assert "abc-123" not in argv
    assert "--resume" not in argv


def test_command_override_replaces_the_executable_slot():
    argv = _argv("codex", command="/opt/fixtures/codex", resume="x1")
    assert argv[:4] == ["/opt/fixtures/codex", "exec", "resume", "x1"]


def test_a_multiword_override_is_shlex_split():
    """tests/support/harness.py builds `command` as "<python> <script>" and
    agent.py shlex.splits it today. Breaking that breaks every dispatch test."""
    argv = _argv("codex", command="/usr/bin/python3 /t/fake_agent.py")
    assert argv[:3] == ["/usr/bin/python3", "/t/fake_agent.py", "exec"]


def test_an_option_a_harness_does_not_support_is_never_emitted():
    """Defence in depth. Task 3 rejects such a binding at load; if one ever
    reaches here it must not become a malformed command line -- the latent
    bug at agent.py:473."""
    argv = _argv("codex", options={"deny_tools": ("Write",)})
    assert "Write" not in argv
    assert "--disallowed-tools" not in argv
