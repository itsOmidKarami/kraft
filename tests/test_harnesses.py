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
    assert (
        claude.capabilities["approval_channel"].always
        == "mcp__kraft__permission_request"
    )


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
    assert not claude.value_ok("effort", "lower")      # fullmatch, not search
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
