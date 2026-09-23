"""`kraft.permission_rules`: a task's tool policy rendered into a CLI's own
permission config at launch (Kraft-4in7z.4 opencode, .2 amp)."""

import json

import pytest

from kraft import harness
from kraft import permission_rules as pr


def _names(h):
    return harness.load(None).valid[h].tool_names


def _opencode(allowed, deny, tmp_path):
    env, argv = pr.render(
        "opencode", _names("opencode"), allowed, deny, directory=tmp_path, session_id="s"
    )
    assert argv == ("--standalone",)  # the only run that reads the env config
    return json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]


@pytest.mark.parametrize("renderer", sorted(pr.RENDERERS))
def test_nothing_to_enforce_renders_nothing(renderer, tmp_path):
    assert pr.render(renderer, {"x": ("Bash",)}, None, (), directory=tmp_path, session_id="s") == (
        {},
        (),
    )
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_opencode_deny_bash_also_denies_code_mode(tmp_path):
    perm = _opencode(None, ("Bash",), tmp_path)
    # The shell stays listed (OpenCode's free tier refuses a request without
    # it) with every call denied; code mode is dropped outright.
    assert perm == {"bash": {"?*": "deny"}, "execute": "deny"}


def test_opencode_deny_write_denies_edit_which_also_writes(tmp_path):
    assert _opencode(None, ("Write",), tmp_path) == {"edit": "deny"}


def test_opencode_allowlist_denies_everything_else_last_rule_wins(tmp_path):
    perm = _opencode(("Read", "Edit", "Write"), (), tmp_path)
    assert list(perm)[0] == "*" and perm["*"] == "deny"  # first, so later keys win
    assert perm["read"] == perm["edit"] == "allow"
    assert perm["bash"] == {"?*": "deny"} and perm["execute"] == "deny"
    assert perm["glob"] == "deny"
    # Guards, not tools: left to --auto, so the result file can be written.
    assert perm["external_directory"] == perm["doom_loop"] == "ask"


def test_opencode_allowlist_naming_half_a_tool_does_not_allow_it(tmp_path):
    """`edit` also writes: listing Edit alone must not let the agent write."""
    assert _opencode(("Edit",), (), tmp_path)["edit"] == "deny"


def test_a_deny_outranks_the_allowlist(tmp_path):
    assert _opencode(("Read",), ("Read",), tmp_path)["read"] == "deny"


def test_unmapped_names_are_reported_once_and_mapped_ones_not():
    names = {"bash": ("Bash",), "edit": ("Edit", "Write")}
    assert pr.unmapped(names, ("Write", "Read", "Read"), ("Bash", "Monitor")) == [
        "Monitor",
        "Read",
    ]
    assert pr.unmapped(names, None, ()) == []
