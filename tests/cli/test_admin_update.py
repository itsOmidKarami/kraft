"""`kraft admin update`: which channel it compares against, and `-y` on a
pre-V1 home, the guts of which is
`replace_pre_v1_config` (`src/kraft/cli/admin.py`) -- a split-off sibling of
`tests/cli/test_admin.py`, the same pattern as `test_admin_harnesses.py` and
`test_admin_templates_library.py`, kept under the repo's line budget."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kraft import cli, update
from kraft import harness as _harness
from kraft.adapters import agent as _agent
from kraft.templates.environment import HarnessProfileTable
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import AgentTask

#: `tests/cli/test_admin_update.py` is one level under `tests/`, so the repo
#: root -- where the real shipped `templates/` lives -- is `parents[2]`, not
#: `parents[1]` (CLAUDE.md: resolve nested-test paths against the filesystem,
#: never trust the arithmetic).
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _pre_v1_home(root: Path) -> tuple[Path, dict[str, bytes]]:
    """A realistic pre-V1 templates home under `root`, and its `MACHINE_CONFIG`
    files' original bytes (what `replace_pre_v1_config` must carry across
    untouched). Shaped after `~/.kraft/config.pre-v1-20260922-192004` on
    this machine (`access.yaml`, `repos.yaml`, `intake.yaml`, `theme.yaml`,
    `policy.yaml`) plus a pre-V1 `harnesses.yaml` (no
    `profiles:` block -- that key did not exist before V1)."""
    home = root / "templates"
    home.mkdir()
    carried = {}

    (home / "harnesses.yaml").write_bytes(
        (_REPO_ROOT / "tests" / "fixtures" / "templates_rc" / "harnesses.yaml").read_bytes()
    )
    assert "profiles" not in yaml.safe_load((home / "harnesses.yaml").read_text())

    (home / "repos.yaml").write_text(
        "repos:\n"
        "- path: /repo\n"
        "  name: demo-repo\n"
        "  default_chain_template: default\n"
        "  test_command: just test\n"
        "  setup_command: uv sync\n"
    )
    (home / "access.yaml").write_text(
        yaml.safe_dump({"password_hash": "not-a-real-hash", "bind": "127.0.0.1:8765"})
    )
    (home / "notify.yaml").write_text(
        yaml.safe_dump({"webhook_url": "https://example.invalid/hook"})
    )
    (home / "theme.yaml").write_text(yaml.safe_dump({"palette": "slate", "mode": "system"}))
    (home / "intake.yaml").write_text(
        yaml.safe_dump({"enabled": False, "interval_s": 300, "repos": []})
    )
    for name in ("repos.yaml", "access.yaml", "notify.yaml", "theme.yaml", "intake.yaml"):
        carried[name] = (home / name).read_bytes()

    # A 0.x `policy.yaml`: one key V1 still has (`budget`), one it dropped
    # (`legacy_only_cap`, never a V1 `PolicyInput` field).
    (home / "policy.yaml").write_text(
        yaml.safe_dump(
            {
                "default": {"attempts": 3, "wall_clock_s": 3600},
                "budget": {"work_item_usd": 100},
                "legacy_only_cap": 8,
            }
        )
    )
    return home, carried


def test_replace_pre_v1_config_carries_machine_config_and_installs_v1_harnesses(
    tmp_path, monkeypatch
):
    """`replace_pre_v1_config` is the guts of `kraft admin update -y` on a
    pre-V1 home (Kraft-sy1ur / review-r21.md F2). `MACHINE_CONFIG` names the
    `harnesses` *overlay directory*, never `harnesses.yaml` -- carrying the
    operator's copy of that file over the bundled V1 one would leave every
    library task selecting `profile: strong` (`implementer`,
    `repair_verification`, `repair_mr_feedback`, `repair_mr_checks`,
    `strict_judge`) unable to resolve a model, because a pre-V1
    `harnesses.yaml` has no `profiles:` block. That mistake passes every test
    that only re-reads the YAML; this one resolves the swapped-in library the
    way a real launch does."""
    from kraft.cli import admin

    home, carried = _pre_v1_home(tmp_path)
    backup = tmp_path / "templates.pre-v1-backup"
    monkeypatch.setattr(admin, "BUNDLED", _REPO_ROOT)

    admin.replace_pre_v1_config(home, backup)

    # The point of the pin, asserted first so a regression here is what the
    # test actually reports failing on: the default chain resolves, and every
    # task selecting `profile: strong` comes back as claude/sonnet/high --
    # through the same production functions a real launch resolves through
    # (`kraft.adapters.agent.select_profile`/`resolve_profile`), never by
    # re-reading `harnesses.yaml`'s raw keys. If `MACHINE_CONFIG` ever carries
    # the operator's pre-V1 `harnesses.yaml` (no `profiles:`) over the bundled
    # one, `resolve_profile` raises `ProfileUnavailable` here, naming
    # `profile 'strong' is not defined in harnesses.yaml`.
    library = TemplateLibrary.from_yaml_dir(home, skills_dir=None)
    resolved = library.resolve_chain("default")
    agent_tasks = [
        t.task
        for node in resolved.nodes
        for t in node.tasks()
        if isinstance(t.task, AgentTask) and t.task.profile == "strong"
    ]
    assert agent_tasks, "expected the shipped default chain to still select profile: strong"

    harnesses_path = home / "harnesses.yaml"
    harnesses_env = _harness.load(None)
    table = HarnessProfileTable.from_yaml(harnesses_path, harnesses=harnesses_env.valid)
    for task in agent_tasks:
        harness_profile = _agent.select_profile(table.profiles, task.harness, harnesses_path)
        model, effort = _agent.resolve_profile(
            task.profile, harness_profile, table, harnesses_env.valid
        )
        assert (harness_profile.provider, model, effort) == ("claude", "sonnet", "high")

    # Every MACHINE_CONFIG file carried across byte-for-byte.
    for name, original in carried.items():
        assert (home / Path(name)).read_bytes() == original, name

    # harnesses.yaml is the bundled V1 one, not the operator's pre-V1 copy: it
    # has the shipped `profiles:` tiers, not just an absence of the key. Not
    # an exact set -- a legitimate new tier shipping later must not fail this
    # pre-V1-swap test on an unrelated line.
    harnesses_data = yaml.safe_load((home / "harnesses.yaml").read_text())
    assert {"deep", "strong", "fast"} <= set(harnesses_data["profiles"])

    # The backup holds the old (pre-V1) home whole.
    assert yaml.safe_load((backup / "harnesses.yaml").read_text()) == yaml.safe_load(
        (_REPO_ROOT / "tests" / "fixtures" / "templates_rc" / "harnesses.yaml").read_text()
    )
    for name, original in carried.items():
        assert (backup / Path(name)).read_bytes() == original, name
    assert (backup / "policy.yaml").is_file()


@pytest.mark.parametrize(
    ("have", "argv", "channel", "said"),
    [
        ("1.4.0", [], "stable", "kraft 1.4.0 is up to date (v1.4.0 is the newest release)"),
        (
            "1.5.0rc12",
            [],
            "rc",
            "kraft 1.5.0rc12 is up to date (v1.5.0rc12 is the newest release on the rc channel)",
        ),
        (
            "1.5.0rc12",
            ["--channel", "stable"],
            "stable",
            "(v1.4.0 is the newest release on the stable channel)",
        ),
    ],
    ids=["stable-install", "rc-install", "rc-install-asks-stable"],
)
def test_update_follows_the_installed_versions_channel_unless_told_otherwise(
    monkeypatch, tmp_path, capsys, have, argv, channel, said
):
    """An rc install used to compare against the stable feed and be told v1.4.0
    was the newest release, while doctor (which uses its own channel) said rc12."""
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path / "templates"))
    asked = []

    def latest(**kwargs):
        asked.append(kwargs["channel"])
        return update.Release("v1.4.0" if kwargs["channel"] == "stable" else "v1.5.0rc12", "u")

    monkeypatch.setattr(update, "latest", latest)
    monkeypatch.setattr(update, "installed", lambda: have)
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "perform", lambda *_a, **_k: pytest.fail("installed"))
    cli.main(["admin", "update", *argv])
    assert asked == [channel]
    assert said in capsys.readouterr().out


def test_a_1_5_candidate_install_updates_to_the_2_0_candidate(monkeypatch, tmp_path, capsys):
    """The 1.5.0 candidates shipped as 2.0. An rc14 install, asked nothing
    about channels, follows its own rc channel across the major to 2.0.0rc1,
    from the real feed shape rather than a stubbed `latest`."""
    feed = [
        {
            "tag_name": tag,
            "prerelease": "rc" in tag,
            "assets": [{"name": "k.whl", "browser_download_url": "u"}],
        }
        for tag in ["v2.0.0rc1", *(f"v1.5.0rc{n}" for n in range(14, 0, -1)), "v1.4.0"]
    ]
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path / "templates"))
    monkeypatch.setattr(update, "_fetch", lambda *_: feed)
    monkeypatch.setattr(update, "installed", lambda: "1.5.0rc14")
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "shadowing_kraft", lambda: None)
    installed = []
    monkeypatch.setattr(
        update, "perform", lambda release, *_a, **_k: installed.append(release.tag) or 0
    )
    cli.main(["admin", "update"])
    assert installed == ["v2.0.0rc1"]
    assert "kraft 1.5.0rc14 -> v2.0.0rc1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "release, argv, installs, says",
    [
        ("v3.0.0", [], False, "release@acme 1.0.0 needs Kraft 2.x"),
        ("v3.0.0", ["-y"], True, "release@acme 1.0.0 needs Kraft 2.x"),
        ("v2.9.0", [], True, None),
    ],
    ids=["next-major-asks", "next-major-accepted", "same-major-says-nothing"],
)
def test_a_major_update_lists_the_addons_it_would_drop(
    monkeypatch, tmp_path, capsys, release, argv, installs, says
):
    """An add-on runs on one Kraft major: the operator hears which ones a new
    major leaves out before it is installed, and nothing is installed on a no."""
    from support.addons import AGENT, chain, home, install, make_collection

    config_dir = home(tmp_path)
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    addon = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
    collection = make_collection(tmp_path, {"release": addon})
    install(config_dir, tmp_path / "run" / "addons", collection, "release")
    installed = []
    monkeypatch.setattr(update, "latest", lambda **_k: update.Release(release, "u"))
    monkeypatch.setattr(update, "installed", lambda: "2.4.0")
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "shadowing_kraft", lambda: None)
    monkeypatch.setattr(update, "perform", lambda *_a, **_k: installed.append(1) or 0)

    try:
        cli.main(["admin", "update", *argv])
        refused = None
    except SystemExit as exc:
        refused = str(exc.code)

    said = capsys.readouterr().err
    assert bool(installed) == installs
    assert (says in said) if says else ("would stop loading" not in said)
    assert (refused is not None) == (not installs)
    if refused:
        assert "nothing installed" in refused


@pytest.mark.parametrize("why", ["dropped-addon", "pre-v1"])
def test_unattended_precheck_refuses_where_the_command_would_ask(
    monkeypatch, tmp_path, capsys, why
):
    """With nobody to ask, `update_prechecks` hands back the reason instead of
    prompting, and asks nothing."""
    from support.addons import AGENT, chain, home, install, make_collection

    config_dir = home(tmp_path)
    if why == "pre-v1":
        config_dir, _ = _pre_v1_home(tmp_path)
        (config_dir / "registry.yaml").write_text("hooks: []\n")
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    if why == "dropped-addon":
        addon = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
        collection = make_collection(tmp_path, {"release": addon})
        install(config_dir, tmp_path / "run" / "addons", collection, "release")
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "_stale_kraft_tool", lambda _run: False)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked"))

    reason = cli.admin.update_prechecks(update.Release("v3.0.0", "u"), interactive=False)

    assert reason is not None
    assert ("release@acme" in reason) if why == "dropped-addon" else ("0.x" in reason)
    assert capsys.readouterr().err == ""


def test_unattended_precheck_passes_a_plain_update(monkeypatch, tmp_path):
    from support.addons import home

    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(home(tmp_path)))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "_stale_kraft_tool", lambda _run: False)
    assert cli.admin.update_prechecks(update.Release("v2.9.0", "u"), interactive=False) is None


def test_unattended_precheck_refuses_a_stale_kraft_tool(monkeypatch, tmp_path):
    from support.addons import home

    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(home(tmp_path)))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "_stale_kraft_tool", lambda _run: True)
    reason = cli.admin.update_prechecks(update.Release("v2.9.0", "u"), interactive=False)
    assert reason is not None and "before the rename" in reason


# ── --when-free ──


@pytest.fixture
def held(monkeypatch):
    """A running server that holds the request: what the client was asked."""
    from kraft import client

    calls = []
    state = {
        "installed": "2.2.0",
        "latest": "v2.3.0",
        "behind": True,
        "blocked": ["waiting for 1 running item"],
    }

    async def update_available(channel=None):
        calls.append(("update_available", channel))
        return state

    async def mark_update(tag, channel=None):
        calls.append(("mark", tag, channel))
        return state

    async def unmark_update():
        calls.append(("unmark",))
        return state

    async def restart_when_free():
        calls.append(("restart",))
        return state

    monkeypatch.setattr(client, "update_available", update_available)
    monkeypatch.setattr(client, "mark_update", mark_update)
    monkeypatch.setattr(client, "unmark_update", unmark_update)
    monkeypatch.setattr(client, "restart_when_free", restart_when_free)
    monkeypatch.setattr(
        cli.admin,
        "_confirm_running_agents",
        lambda *_a, **_k: pytest.fail("asked about running agents"),
    )
    return calls, state


def test_when_free_marks_newest(held, capsys):
    calls, _ = held
    cli.main(["admin", "update", "--when-free"])
    assert calls == [("update_available", None), ("mark", "v2.3.0", None)]
    assert "v2.3.0 will be installed when Kraft is free (waiting: waiting for 1" in (
        capsys.readouterr().out
    )


def test_when_free_with_a_tag(held):
    calls, _ = held
    cli.main(["admin", "update", "--when-free", "v3.0.0", "--channel", "rc"])
    assert calls == [("mark", "v3.0.0", "rc")]


def test_when_free_when_already_current_marks_nothing(held, capsys):
    calls, state = held
    state["behind"] = False
    cli.main(["admin", "update", "--when-free"])
    assert calls == [("update_available", None)]
    assert "up to date" in capsys.readouterr().out


def test_cancel_when_free(held):
    calls, _ = held
    cli.main(["admin", "update", "--cancel-when-free"])
    assert calls == [("unmark",)]


def test_restart_when_free(held, capsys):
    calls, _ = held
    cli.main(["admin", "restart", "--when-free"])
    assert calls == [("restart",)]
    assert "Kraft will restart once nothing is running" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        pytest.param(["update", "--when-free"], "kraft admin update", id="update"),
        pytest.param(["restart", "--when-free"], "kraft admin start", id="restart"),
    ],
)
def test_when_free_with_no_server_exits_1(monkeypatch, capsys, argv, says):
    """Nothing holds the request, so it is refused and what works without a
    server is named."""
    from kraft import client

    async def unreachable(*_a, **_k):
        raise client.transport._unreachable()

    for name in ("update_available", "mark_update", "restart_when_free"):
        monkeypatch.setattr(client, name, unreachable)

    with pytest.raises(SystemExit) as exit_:
        cli.main(["admin", *argv])

    assert exit_.value.code == 1
    assert says in capsys.readouterr().err
