"""`cli.admin.carry_moved_keys`: every start moves the two keys 2.0 reads from
another file, and moves nothing it would break."""

from __future__ import annotations

import pytest
import yaml

from kraft import cli

SCHEDULE = {"cron": "0 9 * * 1", "repo": "/r", "chain": "default", "title": "weekly"}
OTHER = {**SCHEDULE, "title": "monthly"}


@pytest.mark.parametrize(
    "case",
    [
        "already-carried",
        "a-trigger-intake-would-refuse",
        "an-intake-that-does-not-load",
        "symlinked-files",
        "nothing-to-move",
    ],
)
def test_carry_moved_keys_moves_only_what_intake_yaml_then_loads(tmp_path, case):
    """A schedule already carried is not added twice (a start interrupted
    between the two writes, or a home with both keys). A trigger 1.4 fired
    but 2.0's schedule refuses (an `enabled: false`), or an `intake.yaml`
    that would not load, leaves the triggers where they are, still read: an
    `intake.yaml` that fails turns every schedule in it off. A symlinked file
    is written where it points. A home with nothing to move is not written."""
    home = tmp_path / "config"
    home.mkdir()
    intake = {"enabled": False}
    triggers = [SCHEDULE, OTHER]
    if case == "already-carried":
        intake["schedules"] = [SCHEDULE]
    elif case == "a-trigger-intake-would-refuse":
        triggers = [{**SCHEDULE, "enabled": False}]
    elif case == "an-intake-that-does-not-load":
        intake["interval_s"] = "often"
    elif case == "nothing-to-move":
        intake["schedules"], triggers = [SCHEDULE], None
    policy = {"default": {"attempts": 1, "wall_clock_s": 1}}
    if triggers is not None:
        policy["triggers"] = triggers
    files = home
    if case == "symlinked-files":
        files = tmp_path / "dotfiles"
        files.mkdir()
        for name in ("intake.yaml", "policy.yaml"):
            (home / name).symlink_to(files / name)
    (files / "intake.yaml").write_text("# mine\n" + yaml.safe_dump(intake))
    (files / "policy.yaml").write_text("# caps\n" + yaml.safe_dump(policy))
    before = {n: (files / n).stat().st_ino for n in ("intake.yaml", "policy.yaml")}

    lines = cli.admin.carry_moved_keys(home)

    after = {n: yaml.safe_load((files / n).read_text()) for n in ("intake.yaml", "policy.yaml")}
    if case in ("a-trigger-intake-would-refuse", "an-intake-that-does-not-load"):
        assert after["policy.yaml"]["triggers"] == triggers
        assert "schedules" not in after["intake.yaml"]
        assert lines and "left where they are" in lines[0]
    elif case == "nothing-to-move":
        assert lines == []
        assert {n: (files / n).stat().st_ino for n in before} == before  # not replaced
    else:
        assert "triggers" not in after["policy.yaml"]
        assert after["intake.yaml"]["schedules"] == [SCHEDULE, OTHER]
        assert (files / "intake.yaml").read_text().startswith("# mine\n")
    if case == "symlinked-files":
        assert (home / "intake.yaml").is_symlink() and (home / "policy.yaml").is_symlink()
