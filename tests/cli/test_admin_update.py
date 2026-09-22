"""`kraft admin update -y` on a pre-V1 home: the guts of it is
`replace_pre_v1_config` (`src/kraft/cli/admin.py`) -- a split-off sibling of
`tests/cli/test_admin.py`, the same pattern as `test_admin_harnesses.py` and
`test_admin_templates_library.py`, kept under the repo's line budget."""

from __future__ import annotations

from pathlib import Path

import yaml

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
    untouched). Shaped after `~/.kraft/templates.pre-v1-20260922-192004` on
    this machine (`access.yaml`, `repos.yaml`, `intake.yaml`, `theme.yaml`,
    `steering/`, `policy.yaml`) plus a pre-V1 `harnesses.yaml` (no
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
    (home / "steering").mkdir()
    (home / "steering" / "note.md").write_text("# never signal a process you did not start\n")
    for name in ("repos.yaml", "access.yaml", "notify.yaml", "theme.yaml", "intake.yaml"):
        carried[name] = (home / name).read_bytes()
    carried["steering/note.md"] = (home / "steering" / "note.md").read_bytes()

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
