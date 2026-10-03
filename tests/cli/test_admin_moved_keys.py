"""The first 2.0 start's moves (`cli.admin.prepare_home`): a 1.x home adopted
under its new name (`adopt_pre_2_home`), and the two keys 2.0 reads from
another file carried on every start (`carry_moved_keys`), moving nothing it
would break."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kraft import cli, paths

SCHEDULE = {"cron": "0 9 * * 1", "repo": "/r", "chain": "default", "title": "weekly"}
OTHER = {**SCHEDULE, "title": "monthly"}


@pytest.mark.parametrize(
    "case",
    [
        "already-carried",
        "already-carried-by-settings",
        "a-trigger-intake-would-refuse",
        "one-refused-the-rest-move",
        "a-1.4-cron-out-of-range-stays",
        "a-1.4-cron-in-other-digits-stays",
        "an-intake-that-does-not-load",
        "symlinked-files",
        "nothing-to-move",
    ],
)
def test_carry_moved_keys_moves_only_what_intake_yaml_then_loads(tmp_path, case):
    """A schedule already carried is not added twice (a start interrupted
    between the two writes, or a home with both keys). A trigger 1.4 fired
    but 2.0's schedule refuses (an `enabled: false`), or an `intake.yaml`
    that would not load, leaves the triggers where they are, and the line
    says which are still read and which skipped (a 1.4 cron): an
    `intake.yaml` that fails turns every schedule in it off. A symlinked file
    is written where it points. A home with nothing to move is not written.
    A schedule is compared as one (Settings writes `description: ''`), and
    one refused trigger keeps only itself back (R12c-02)."""
    home = tmp_path / "config"
    home.mkdir()
    intake = {"enabled": False}
    triggers = [SCHEDULE, OTHER]
    if case == "already-carried":
        intake["schedules"] = [SCHEDULE]
    elif case == "already-carried-by-settings":
        intake["schedules"] = [{**SCHEDULE, "description": ""}]
    elif case == "a-trigger-intake-would-refuse":
        triggers = [{**SCHEDULE, "enabled": False}]
    elif case == "one-refused-the-rest-move":
        triggers = [{**SCHEDULE, "enabled": False}, OTHER]
    elif case == "a-1.4-cron-out-of-range-stays":
        triggers = [{**SCHEDULE, "cron": "0 24 * * *"}, OTHER]
    elif case == "a-1.4-cron-in-other-digits-stays":
        triggers = [{**SCHEDULE, "cron": "\u00b2 9 * * 1"}, OTHER]
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
        assert lines and "left where they are" in lines[0] and "still read" in lines[0]
    elif case == "nothing-to-move":
        assert lines == []
        assert {n: (files / n).stat().st_ino for n in before} == before  # not replaced
    elif case.startswith(("one-refused", "a-1.4-cron")):
        assert after["policy.yaml"]["triggers"] == triggers[:1]
        assert after["intake.yaml"]["schedules"] == [OTHER]
        why, read = {
            "one-refused-the-rest-move": ("triggers.0.enabled", "triggers.0 still read"),
            "a-1.4-cron-out-of-range-stays": (
                "hour 24 is outside 0-23",
                "triggers.0 skipped, as 1.4 never ran it",
            ),
            "a-1.4-cron-in-other-digits-stays": (
                "is not the digits 0-9",
                "triggers.0 skipped, as a cron number is the digits 0-9",
            ),
        }[case]
        assert why in lines[1] and read in lines[1] and "next start moves them" in lines[1]
        assert "still read" not in lines[1] or case.startswith("one")
    else:
        assert "triggers" not in after["policy.yaml"]
        schedules = [{"description": ""} if case.endswith("settings") else {}, {}]
        assert after["intake.yaml"]["schedules"] == [
            {**s, **extra} for s, extra in zip([SCHEDULE, OTHER], schedules, strict=True)
        ]
        assert (files / "intake.yaml").read_text().startswith("# mine\n")
    if case == "symlinked-files":
        assert (home / "intake.yaml").is_symlink() and (home / "policy.yaml").is_symlink()


@pytest.mark.parametrize("where", ["renamed", "pointed-by-hand"])
def test_the_start_carries_the_keys_2_0_moved_between_files(monkeypatch, tmp_path, where):
    """A 1.x `intake.yaml` `max_concurrent` and `policy.yaml` `triggers:` move
    to the file 2.0 reads them from, once, at the first start, each file
    keeping its comments, and a moved trigger its own (R12D-03): in the home
    the start renames, and just the same in one an operator points
    `KRAFT_CONFIG_DIR` at, which is never renamed."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    old = tmp_path / ("elsewhere" if where == "pointed-by-hand" else "home/templates")
    if where == "pointed-by-hand":
        monkeypatch.setenv("KRAFT_CONFIG_DIR", str(old))
    old.mkdir(parents=True)
    (old / "library.yaml").write_text("tasks: {}\n")
    (old / "intake.yaml").write_text("# pickup\nenabled: false\nmax_concurrent: 1  # one\n")
    (old / "policy.yaml").write_text(
        "# caps\ndefault: {attempts: 1, wall_clock_s: 1}\n"
        "triggers:\n  # the weekly one\n"
        "  - {cron: '0 9 * * 1', repo: /r, chain: default, title: t}  # weekly\n"
        "# the end\n"
    )

    home = cli.admin.prepare_home()

    assert home == (old if where == "pointed-by-hand" else paths.default_config_dir())
    assert old.is_symlink() == (where == "renamed")
    intake = yaml.safe_load((home / "intake.yaml").read_text())
    policy = yaml.safe_load((home / "policy.yaml").read_text())
    assert "max_concurrent" not in intake and policy["max_concurrent"] == 1
    assert "triggers" not in policy
    assert intake["schedules"] == [
        {"cron": "0 9 * * 1", "repo": "/r", "chain": "default", "title": "t"}
    ]
    assert (home / "intake.yaml").read_text().startswith("# pickup\n")
    assert (home / "policy.yaml").read_text().startswith("# caps\n")
    lines = (home / "intake.yaml").read_text().splitlines()
    assert any("title: t}" in line and line.endswith("# weekly") for line in lines)
    # The line above it too, and the list's indent as policy.yaml had it.
    assert lines[lines.index("  # the weekly one") + 1].startswith("  - {cron:")
    assert "# the end" not in (home / "intake.yaml").read_text()
    assert "weekly" not in (home / "policy.yaml").read_text()


@pytest.mark.parametrize(
    "case, in_use, templates_is",
    [
        ("default", "config", "link"),
        ("templates-variable-at-the-default", "templates", "link"),
        ("config-variable-at-the-default", "config", "link"),
        ("config-made-by-hand", "config", "link"),
        ("config-made-by-hand-with-a-clash", "config", "dir"),
        ("config-made-by-hand-and-an-empty-leftover", "config", "link"),
        ("0x-update-interrupted", "config", "link"),
        ("rename-refused", "templates", "dir"),
        ("pointed-by-hand", "elsewhere", "dir"),
        ("a-2-0-home-beside-it", "config", "dir"),
        ("templates-variable-with-a-config-made-by-hand", "refused", "dir"),
        ("config-variable-at-templates-beside-config", "templates", "dir"),
    ],
)
def test_the_first_start_adopts_a_pre_2_home_only_at_the_default_location(
    monkeypatch, tmp_path, capsys, case, in_use, templates_is
):
    """The rename follows the directory the start reads, 1.4's service-unit
    `KRAFT_TEMPLATES_DIR` at the default included, and never seeds over the
    operator's repos. `templates` is left as a link, so a 1.4 process still
    running finds its `access.yaml`. A `config/` made by hand first gets what
    it lacks; a clash stays put for the operator, an empty directory is no
    clash (R13c-05). A directory pointed at
    elsewhere, or a 2.0 home already there, is not touched; a refused rename
    is read where it is. A `config/` beside a `templates/` the variable names
    stops the start: a merge could strand a clash where it reads. A
    `KRAFT_CONFIG_DIR` is a 2.0 operator's own choice, never moved or refused,
    even naming `templates/` beside a `config/` (the e2e server's layout);
    one naming the default `config/` is the default (R12c-04)."""
    bundled = tmp_path / "_bundled" / "config"
    bundled.mkdir(parents=True)
    (bundled / "library.yaml").write_text("tasks: {}\n")
    monkeypatch.setattr(cli.admin, "BUNDLED", tmp_path / "_bundled")
    h = tmp_path / "home"
    monkeypatch.setenv("KRAFT_HOME", str(h))
    elsewhere = tmp_path / "elsewhere"
    named_2 = {
        "pointed-by-hand": elsewhere,
        "config-variable-at-the-default": h / "config",
        "config-variable-at-templates-beside-config": h / "templates",
    }
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(named_2.get(case, "")))
    named = str(h / "templates") if case.startswith("templates-variable") else ""
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", named)
    old = h / "templates"
    (old / "harnesses").mkdir(parents=True)
    (old / "harnesses" / "mine.yaml").write_text("x: 1\n")
    for name, text in {
        "library.yaml": "tasks: {}\n",
        "repos.yaml": "repos: [mine]\n",
        "access.yaml": "port: 9999\n",
    }.items():
        (old / name).write_text(text)
    if "config-made-by-hand" in case or case.endswith("beside-config"):
        (h / "config" / "harnesses").mkdir(parents=True)
        if case.endswith(("clash", "leftover")):
            (h / "config" / "harnesses" / "theirs.yaml").write_text("y: 2\n")
        if case.endswith("leftover"):
            (old / "harnesses" / "mine.yaml").unlink()
    elif case == "a-2-0-home-beside-it":
        (h / "config").mkdir()
        (h / "config" / "library.yaml").write_text("tasks: {}\n")
    elif case == "0x-update-interrupted":
        old.rename(h / "templates.seeding")
        (h / "templates.seeding" / cli.admin.UPDATE_STAGED).write_text("templates.pre-v1-x\n")
    elif case == "rename-refused":
        monkeypatch.setattr(Path, "rename", lambda *a: (_ for _ in ()).throw(OSError(16, "busy")))

    if in_use == "refused":
        # The variable reads `templates/`: a merge could strand a clash there
        # and seed over it, so the start names both and stops.
        with pytest.raises(SystemExit, match="both exist"):
            cli.admin.prepare_home()
        assert sorted(p.name for p in old.iterdir()) == sorted(
            ["access.yaml", "harnesses", "library.yaml", "repos.yaml"]
        )
        return
    got = cli.admin.prepare_home()

    assert got == {"config": h / "config", "templates": old, "elsewhere": elsewhere}[in_use]
    assert old.is_symlink() == (templates_is == "link")
    if case not in ("pointed-by-hand", "a-2-0-home-beside-it"):
        # Never seeded over: the operator's repos are where every reader looks.
        assert yaml.safe_load((got / "repos.yaml").read_text()) == {"repos": ["mine"]}
    if case.endswith("clash"):
        assert sorted(p.name for p in (h / "config" / "harnesses").iterdir()) == ["theirs.yaml"]
        assert (old / "harnesses" / "mine.yaml").is_file()
        assert "merge them by hand" in capsys.readouterr().err
    else:
        assert (old / "access.yaml").read_text() == "port: 9999\n"
    if case.endswith("leftover"):
        assert sorted(p.name for p in (h / "config" / "harnesses").iterdir()) == ["theirs.yaml"]


def test_the_first_start_binds_the_address_of_the_home_it_finishes_moving(monkeypatch, tmp_path):
    """The bind is read before the home moves, to probe the port; a 0.x
    update stopped between its renames leaves no home under either name until
    the start finishes it, so the start reads `access.yaml` again after the
    move and binds that address, not the default (R12c-01)."""
    bundled = tmp_path / "_bundled" / "config"
    bundled.mkdir(parents=True)
    (bundled / "library.yaml").write_text("tasks: {}\n")
    monkeypatch.setattr(cli.admin, "BUNDLED", tmp_path / "_bundled")
    home = tmp_path / "home"
    for name in ("KRAFT_CONFIG_DIR", "KRAFT_TEMPLATES_DIR", "KRAFT_HOST", "KRAFT_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KRAFT_HOME", str(home))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_LOG_REDIRECTED", "1")
    staged = home / "templates.seeding"
    staged.mkdir(parents=True)
    (staged / "library.yaml").write_text("tasks: {}\n")
    (staged / "access.yaml").write_text("port: 8927\n")
    (staged / cli.admin.UPDATE_STAGED).write_text("templates.pre-v1-x\n")
    bound = []
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: bound.append(self.config.port)
    )

    cli.admin._serve()

    assert bound == [8927]
    assert (paths.default_config_dir() / "access.yaml").read_text() == "port: 8927\n"


def _triggers(comments: str, stays: str) -> tuple[str, list, list]:
    """A 1.4 `policy.yaml` with three triggers commented as `comments` says,
    and which of them `stays` keeps there: (text, moved, stayed)."""
    entries = [{**SCHEDULE, "title": t} for t in ("one", "two", "three")]
    where = {"all-move": None}.get(stays, 0 if stays.endswith("first") else 2)
    if where is not None:
        bad = {"enabled": False} if "enabled" in stays else {"cron": "0 24 * * *"}
        entries[where] = {**entries[where], **bad}
    lines = ["# caps", "max_concurrent: 3"]
    lines += ["# above the key"] if comments == "above-key" else []
    lines += ["triggers:   # on the key" if comments == "on-key-line" else "triggers:"]
    for i, entry in enumerate(entries):
        if (comments == "above-first" and i == 0) or (comments == "between" and i == 1):
            lines.append(f"  # before {entry['title']}")
        for j, (key, value) in enumerate(entry.items()):
            lines.append(f"  {'- ' if j == 0 else '  '}{key}: {json.dumps(value)}")
    text = "\n".join([*lines, "# after", "forge_poll_s: 60\n"])
    stayed = [] if where is None else [entries[where]]
    return text, [e for e in entries if e not in stayed], stayed


@pytest.mark.parametrize(
    ("comments", "stays"),
    [
        pytest.param(c, s, id=f"{c}-{s}")
        for c in ("none", "above-first", "above-key", "on-key-line", "between")
        for s in ("all-move", "enabled-false-last", "cron-24-last", "cron-24-first")
    ],
)
def test_carried_files_read_back_in_every_layout(tmp_path, comments, stays):
    """A comment above the first trigger with one trigger left behind wrote a
    `policy.yaml` that did not parse, and Kraft refused all work (R13d-01):
    in every layout both files read back as what moved and what stayed,
    comments kept. One above `triggers:` stays in `policy.yaml`."""
    text, moved, stayed = _triggers(comments, stays)
    (tmp_path / "policy.yaml").write_text(text)
    (tmp_path / "intake.yaml").write_text("enabled: false\n")

    lines = cli.admin.carry_moved_keys(tmp_path)

    policy = yaml.safe_load((tmp_path / "policy.yaml").read_text())
    intake = yaml.safe_load((tmp_path / "intake.yaml").read_text())
    assert intake["schedules"] == moved
    assert policy.get("triggers", []) == stayed
    assert (policy["max_concurrent"], policy["forge_poll_s"]) == (3, 60)
    assert not [line for line in lines if "without its comments" in line]
    kept = (tmp_path / "policy.yaml").read_text()
    assert ("# above the key" in kept) == (comments == "above-key")
    assert "# after" in kept


def test_a_carry_that_would_not_read_back_is_written_without_comments(tmp_path, monkeypatch):
    """Defence behind the table above: a file Kraft would refuse is never
    written. Both texts are made first, and one whose rewrite would not read
    back is written plainly, comments dropped, and the line says so."""
    from kraft.drafts import preserve

    text, moved, _ = _triggers("above-first", "all-move")
    (tmp_path / "policy.yaml").write_text(text)
    (tmp_path / "intake.yaml").write_text("enabled: false\n")

    def refuse(*_a, **_k):
        raise preserve.RewriteError("it would not parse at line 3")

    monkeypatch.setattr(preserve, "rewrite", refuse)
    lines = cli.admin.carry_moved_keys(tmp_path)

    assert yaml.safe_load((tmp_path / "intake.yaml").read_text())["schedules"] == moved
    assert "triggers" not in yaml.safe_load((tmp_path / "policy.yaml").read_text())
    assert [n for n in ("intake.yaml", "policy.yaml") if f"{n}: written without" in str(lines)] == [
        "intake.yaml",
        "policy.yaml",
    ]
