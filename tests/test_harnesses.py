from pathlib import Path

import pytest

from kraft import harness
from kraft.templates import environment as template_environment


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
    hs = harness.load(None).valid
    claude, codex = hs["claude"], hs["codex"]
    assert claude.value_ok("effort", "low")
    assert not claude.value_ok("effort", "lower")  # fullmatch, not search
    # codex's model list is a real pattern, so it carries the fullmatch case.
    assert codex.value_ok("model", "gpt-5-codex")
    assert not codex.value_ok("model", "sonnet")
    # A capability with no `values:` accepts anything.
    assert claude.value_ok("prompt", "anything at all")


def test_claude_constrains_effort_but_not_model():
    """A model id is an open set Anthropic owns -- aliases, full names and the
    provider-prefixed ids Bedrock and Vertex use. `values:` on `model` is a
    hard gate (load_registry raises, api/startup.py does not catch), so a
    narrow list stops an existing install booting after an upgrade. effort is
    a genuinely closed vocabulary and keeps its list.
    """
    claude = harness.load(None).valid["claude"]
    for model in ("fable", "opus", "opusplan", "claude-opus-5", "sonnet[1m]"):
        assert claude.value_ok("model", model), model
    assert claude.value_ok("model", "us.anthropic.claude-sonnet-4-5-v1:0")
    assert claude.value_ok("effort", "max")
    assert not claude.value_ok("effort", "minimal")  # codex's value, not claude's


def test_codex_effort_values_include_xhigh():
    """Kraft-c1qfa: codex-cli 0.155.0 passes `-c model_reasoning_effort=` straight
    to the model API uninterpreted -- probed 2026-09-22, an invalid value's own
    error names the real vocabulary as 'none', 'minimal', 'low', 'medium',
    'high', 'xhigh', 'max'. codex.yaml's `values:` list (a closed vocabulary
    Kraft ships, not codex's own) had fallen behind that and rejected a legit
    xhigh binding at load with a RegistryError."""
    codex = harness.load(None).valid["codex"]
    assert codex.value_ok("effort", "xhigh")
    assert codex.value_ok("effort", "minimal")  # unaffected by the widening
    assert not codex.value_ok("effort", "bogus")


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


_CAPS = "  context: { channel: prompt }\n  usage: { source: result_file }\n"


@pytest.mark.parametrize(
    "body,expect",
    [
        ("- just\n- a list\n", "x.yaml: expected a top-level mapping"),
        ("id: 7\nkind: cli\ncommand: [x]\n", "missing a string 'id'"),
        ("id: x\nkind: [cli]\ncommand: [x]\n", "unknown kind ['cli']"),
        ("id: x\nkind: cli\ncommand: {a: b}\n", "'command' must be a string or a list"),
        (
            "id: x\nkind: cli\ncommand: [x]\ncommand_resume: [1]\n",
            "'command_resume' must be a string or a list",
        ),
        ("id: x\nkind: cli\ncommand: [x]\ncapabilities: [prompt]\n", "non-empty mapping"),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n" + _CAPS + "  prompt: [-p]\n",
            "capability 'prompt' must be a mapping",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n" + _CAPS + "  prompt: { cli: -p }\n",
            "capability 'prompt' 'cli' must be a list of strings",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            + _CAPS
            + "  prompt: { cli: ['-p', '{value}'] }\n"
            + "  effort: { cli: ['-e', '{value}'], values: [1] }\n",
            "capability 'effort' 'values' must be a list of strings",
        ),
        (
            "id: x\nkind: cli\ncommand: [x]\ncapabilities:\n"
            + _CAPS
            + "  effort: { cli: ['-e', '{value}'], always: 3 }\n"
            + "  prompt: { cli: ['-p', '{value}'] }\n",
            "capability 'effort' 'always' must be a string or a list of strings",
        ),
    ],
)
def test_a_misshapen_harness_is_refused_in_prose_naming_the_key(tmp_path, body, expect):
    """Kraft-5d510.2: the shape is `HarnessInput`'s, but the reason an operator
    reads is still the one the hand-rolled parser gave, never pydantic's
    "Input should be a valid list"."""
    _write(tmp_path, "x.yaml", body)
    assert expect in harness.load(tmp_path / "harnesses").invalid["x"]


def test_a_harness_is_built_from_its_input_model():
    """The pattern `HarnessProfileInput`/`HarnessProfile` use: the model holds
    the shape, `from_input` the relationships between capabilities."""
    parsed = harness.HarnessInput.model_validate(
        {
            "id": "mini",
            "kind": "cli",
            "command": "mini",
            "capabilities": {
                "context": {"channel": "prompt"},
                "usage": {"source": "result_file"},
                "prompt": {"cli": ["-p", "{value}"]},
            },
        }
    )
    h = harness.Harness.from_input(parsed, where="test")
    assert (h.id, h.command, list(h.capabilities)) == (
        "mini",
        ("mini",),
        ["context", "usage", "prompt"],
    )
    with pytest.raises(harness.HarnessError, match="test: missing required capability 'prompt'"):
        harness.Harness.from_input(
            parsed.model_copy(
                update={
                    "capabilities": {k: v for k, v in parsed.capabilities.items() if k != "prompt"}
                }
            ),
            where="test",
        )


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


# ── HarnessProfile: a configured instance, validated against its provider's
# own declared capability surface (provider-declares-harness-capabilities,
# harness-profile-has-safe-instance-configuration,
# agent-task-selects-capability-compatible-runtime-options) ──


def test_harness_profile_selects_only_provider_declared_options():
    claude = harness.load(None).valid["claude"]
    parsed = template_environment.HarnessProfileInput(
        provider="claude", executable="claude", defaults={"effort": "low"}
    )
    profile = template_environment.HarnessProfile.from_input("claude", parsed, harness=claude)
    assert profile.defaults == {"effort": "low"}
    assert profile.provider == "claude"


def test_harness_profile_rejects_an_option_the_provider_does_not_declare():
    """A profile selects only from what `provider-declares-harness-
    capabilities` -- it does not invent its own runtime option."""
    claude = harness.load(None).valid["claude"]
    parsed = template_environment.HarnessProfileInput(provider="claude", defaults={"network": "on"})
    with pytest.raises(template_environment.TemplateEnvironmentError, match="network"):
        template_environment.HarnessProfile.from_input("bad", parsed, harness=claude)


def test_harness_profile_rejects_a_value_the_provider_rejects():
    claude = harness.load(None).valid["claude"]
    parsed = template_environment.HarnessProfileInput(
        provider="claude", defaults={"effort": "minimal"}
    )
    with pytest.raises(template_environment.TemplateEnvironmentError, match="effort"):
        template_environment.HarnessProfile.from_input("bad", parsed, harness=claude)


def test_harness_profile_reports_unavailable_when_disabled():
    """`unavailable-selected-harness-needs-human` needs to know this fact;
    it does not itself decide what happens next -- that is Phase 6's."""
    claude = harness.load(None).valid["claude"]
    parsed = template_environment.HarnessProfileInput(provider="claude", enabled=False)
    profile = template_environment.HarnessProfile.from_input("claude", parsed, harness=claude)
    assert profile.is_available() is False


def test_harness_profile_provider_must_be_the_harness_it_configures():
    """A profile is one configured instance of a provider, and the provider id
    IS the `Harness.id` -- there is no second registry a mapping could point
    at (`provider-profile-and-agent-task-are-distinct`)."""
    claude = harness.load(None).valid["claude"]
    parsed = template_environment.HarnessProfileInput(provider="codex")
    with pytest.raises(template_environment.TemplateEnvironmentError, match="codex"):
        template_environment.HarnessProfile.from_input("mismatched", parsed, harness=claude)


def test_harness_profile_id_must_be_nameable_by_a_task():
    """`AgentTask.harness` is an `Identifier`; a profile id that pattern rejects
    could be defined and never referenced."""
    claude = harness.load(None).valid["claude"]
    parsed = template_environment.HarnessProfileInput(provider="claude")
    with pytest.raises(template_environment.TemplateEnvironmentError, match="Claude-Review"):
        template_environment.HarnessProfile.from_input("Claude-Review", parsed, harness=claude)


def test_codex_reads_usage_and_rate_limits_off_its_json_log():
    """Kraft-w3kot: codex.yaml used to say `usage: result_file`, so a codex
    run recorded no tokens, no session id and no rate-limit signal."""
    codex = harness.load(None).valid["codex"]
    assert codex.capabilities["usage"].source == "envelope"
    assert codex.capabilities["usage"].reader == "codex-json"
    assert codex.capabilities["rate_limit_signal"].reader == "codex-json"
