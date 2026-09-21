"""`kraft.update` - the version check.

Its three callers (admin health, admin doctor, admin start) all sit on paths a
disconnected machine must still walk, so the contract under test is mostly
negative: no failure mode raises, and no failure mode reports an update.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import httpx
import pytest
import yaml

from kraft import update

RELEASE_JSON = [
    {
        "tag_name": "v0.4.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "kraft-0.4.0-py3-none-any.whl",
                "browser_download_url": "https://x/w.whl",
            }
        ],
    }
]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    return tmp_path / "run" / "update-check.json"


def _fetch(payload):
    def fetch(_url, _timeout):
        return payload

    return fetch


def test_latest_reads_the_newest_release(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    release = update.latest()
    assert release.tag == "v0.4.0"
    assert release.wheel_url == "https://x/w.whl"


def test_latest_caches_to_disk(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    assert json.loads(cache.read_text())["tag"] == "v0.4.0"


def test_a_cache_hit_does_not_fetch(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()

    def explode(_url, _timeout):
        raise AssertionError("fetched inside the TTL")

    monkeypatch.setattr(update, "_fetch", explode)
    assert update.latest().tag == "v0.4.0"


def test_an_expired_cache_refetches(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    stale = json.loads(cache.read_text())
    stale["checked_at"] = 0
    cache.write_text(json.dumps(stale))
    newer = [
        {
            "tag_name": "v0.5.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "k.whl", "browser_download_url": "https://x/n.whl"}],
        }
    ]
    monkeypatch.setattr(update, "_fetch", _fetch(newer))
    assert update.latest().tag == "v0.5.0"


@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("no network"),
        httpx.ReadTimeout("too slow"),
        ValueError("not json"),
    ],
)
def test_every_transport_failure_is_none_not_an_exception(cache, monkeypatch, boom):
    def fetch(_url, _timeout):
        raise boom

    monkeypatch.setattr(update, "_fetch", fetch)
    assert update.latest() is None


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [],
        [{"tag_name": "v9.0.0", "draft": False, "prerelease": False, "assets": []}],
    ],
    ids=["garbage-body", "empty-release-list", "no-wheel"],
)
def test_a_feed_with_no_usable_release_is_none(cache, monkeypatch, payload):
    monkeypatch.setattr(update, "_fetch", _fetch(payload))
    assert update.latest() is None


def _release(tag, *, draft=False, prerelease=False, wheel=True):
    assets = [{"name": "k.whl", "browser_download_url": "https://x/k.whl"}] if wheel else []
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets}


@pytest.mark.parametrize(
    "newer",
    [
        # GitHub lists drafts in the same feed; GitLab had no equivalent.
        _release("v0.9.9", draft=True),
        _release("v1.0.0rc1", prerelease=True),
        _release("v0.9.9", wheel=False),
    ],
    ids=["draft", "prerelease", "no-wheel"],
)
def test_a_release_that_is_not_an_update_is_skipped(cache, monkeypatch, newer):
    monkeypatch.setattr(update, "_fetch", _fetch([newer, *RELEASE_JSON]))
    assert update.latest().tag == "v0.4.0"


@pytest.mark.parametrize(
    ("here", "there", "behind"),
    [
        ("0.3.0", "v0.4.0", True),
        ("0.4.0", "v0.4.0", False),
        ("0.5.0", "v0.4.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.3.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.4.0", True),
        ("0.0.0+source", "v0.4.0", True),
        ("0.4.0", "vnonsense", False),
    ],
)
def test_is_behind_compares_versions(monkeypatch, here, there, behind):
    monkeypatch.setattr(update, "installed", lambda: here)
    assert update.is_behind(update.Release(tag=there, wheel_url="u")) is behind


def test_is_behind_of_nothing_is_false():
    assert update.is_behind(None) is False


def test_perform_downloads_the_wheel_and_installs_the_local_copy(monkeypatch):
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"WHEEL BYTES")
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        wheel_path = pathlib.Path(command[5])
        seen["wheel_bytes"] = wheel_path.read_bytes()
        return type("R", (), {"returncode": 0})()

    code = update.perform(update.Release(tag="v0.4.0", wheel_url="https://x/w.whl"), run=run)
    assert code == 0
    command = seen["command"]
    assert command[:5] == ["uv", "tool", "install", "--force", "--from"]
    assert command[6] == "kraft-sdlc"
    assert pathlib.Path(command[5]).name == "w.whl"
    assert seen["wheel_bytes"] == b"WHEEL BYTES"


def test_perform_installs_under_the_pyproject_package_name(monkeypatch):
    """A future PyPI rename that misses this call site should fail loudly, not silently."""
    import tomllib

    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")
    seen = {}

    def run(command, **_kwargs):
        seen["command"] = command
        return type("R", (), {"returncode": 0})()

    update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)

    repo_root = pathlib.Path(__file__).parent.parent
    package_name = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]["name"]
    assert seen["command"][-1] == package_name


def test_just_install_uses_the_pyproject_package_name():
    import tomllib

    repo_root = pathlib.Path(__file__).parent.parent
    package_name = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]["name"]
    lines = (repo_root / "justfile").read_text().splitlines()
    install_line = next(line for line in lines if "uv tool install" in line)
    assert package_name in install_line.split()


def test_perform_downloads_with_the_long_timeout_not_the_check_timeout(monkeypatch):
    seen = {}

    def request(_url, timeout):
        seen["timeout"] = timeout
        return b""

    monkeypatch.setattr(update, "_request", request)
    run = lambda _command, **_kwargs: type("R", (), {"returncode": 0})()  # noqa: E731
    update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)
    assert seen["timeout"] == update.DOWNLOAD_TIMEOUT > update.TIMEOUT


def test_perform_reports_a_failing_installer(monkeypatch):
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")

    def run(_command, **_kwargs):
        return type("R", (), {"returncode": 2})()

    assert update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run) == 2


def test_perform_without_uv_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")

    def run(_command, **_kwargs):
        raise FileNotFoundError("uv")

    with pytest.raises(SystemExit, match="uv"):
        update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)


def test_perform_under_homebrew_calls_brew_upgrade_not_uv(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/opt/homebrew/Cellar/kraft/0.65.0/libexec")

    def request(_url, _timeout):
        raise AssertionError("a homebrew install must not download the wheel itself")

    monkeypatch.setattr(update, "_request", request)
    seen = {}

    def run(command, **_kwargs):
        seen["command"] = command
        return type("R", (), {"returncode": 0})()

    code = update.perform(update.Release(tag="v0.4.0", wheel_url="https://x/w.whl"), run=run)
    assert code == 0
    assert seen["command"] == ["brew", "upgrade", "kraft"]


def test_perform_under_homebrew_without_brew_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/opt/homebrew/Cellar/kraft/0.65.0/libexec")
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")

    def run(_command, **_kwargs):
        raise FileNotFoundError("brew")

    with pytest.raises(SystemExit, match="brew"):
        update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)


def test_is_homebrew_install_is_false_for_a_uv_tool_prefix(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/Users/x/.local/share/uv/tools/kraft-sdlc")
    assert update._is_homebrew_install() is False


# ── a major update replaces an incompatible template configuration
# (major-update-*, migration-helper-is-not-guaranteed) ──

#: What a pre-V1 home holds that V1 has no reader for, and what it holds that
#: belongs to this machine rather than to the template schema.
LEGACY_ONLY = {"registry.yaml": "hooks: {}\n", "my-chain.yaml": "id: my-chain\nnodes: []\n"}
MACHINE = {
    "access.yaml": "bind: 127.0.0.1\n",
    "notify.yaml": "enabled: false\n",
    "repos.yaml": "repos: [{path: /work/mine}]\n",
    "intake.yaml": "enabled: true\n",
    "steering/mine.md": "Ask before deleting anything.\n",
    "theme.yaml": "accent: teal\n",
    "harnesses/mine.yaml": "id: mine\n",
}


def _tree(root: pathlib.Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def legacy_home(tmp_path, monkeypatch):
    """A pre-V1 `$KRAFT_HOME/templates`, a V1 bundle to replace it with (the
    repository's own `templates/`), and a release feed that is already current,
    so `kraft admin update` has only the configuration to do."""
    from kraft import cli

    repo_templates = pathlib.Path(__file__).resolve().parents[1] / "templates"
    bundle = tmp_path / "_bundled"
    shutil.copytree(repo_templates, bundle / "templates")
    monkeypatch.setattr(cli.admin, "BUNDLED", bundle)
    home = tmp_path / "home" / "templates"
    for name, text in {**LEGACY_ONLY, **MACHINE}.items():
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(text)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(home))
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v1.0.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "1.0.0")
    return home


def _backups(home: pathlib.Path) -> list[pathlib.Path]:
    return sorted(home.parent.glob(f"{home.name}.pre-v1-*"))


@pytest.mark.parametrize("accept", ["flag", "prompt"])
def test_major_update_requires_acceptance_and_makes_backup(
    legacy_home, monkeypatch, capsys, accept
):
    from kraft import cli
    from kraft.templates.library import TemplateLibrary

    before = _tree(legacy_home)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "y" if accept == "prompt" else pytest.fail("asked despite -y"),
    )

    cli.main(["admin", "update", *(["-y"] if accept == "flag" else [])])

    # The whole configuration it replaced, kept byte for byte beside it.
    [backup] = _backups(legacy_home)
    assert _tree(backup) == before
    # The V1 configuration is installed, and resolves.
    assert TemplateLibrary.from_yaml_dir(legacy_home).resolve_chain("default").id == "default"
    # What belongs to this machine, not to the template schema, is carried over.
    for name, text in MACHINE.items():
        assert (legacy_home / name).read_text() == text
    # Nothing is migrated: a legacy chain is not converted into the new home
    # (migration-helper-is-not-guaranteed); it is only in the backup.
    assert not (legacy_home / "my-chain.yaml").exists()
    out = capsys.readouterr().out
    assert str(backup) in out


@pytest.mark.parametrize(
    ("answer", "tty"), [("n", True), ("", True), (None, False)], ids=["no", "enter", "no-terminal"]
)
def test_major_update_without_acceptance_changes_nothing(
    legacy_home, monkeypatch, capsys, answer, tty
):
    from kraft import cli

    before = _tree(legacy_home)
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: answer if tty else pytest.fail("prompted with no terminal"),
    )

    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "update"])

    assert caught.value.code == 1
    assert _tree(legacy_home) == before
    assert _backups(legacy_home) == []
    err = capsys.readouterr().err
    assert "-y" in err


def test_the_major_update_warns_before_it_asks(legacy_home, monkeypatch, capsys):
    """`major-update-requires-explicit-acceptance`: the breaking change is
    stated before the question, not after the answer."""
    from kraft import cli

    asked = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(capsys.readouterr()) or "n")

    with pytest.raises(SystemExit):
        cli.main(["admin", "update"])

    [seen] = asked
    assert "not compatible" in seen.err
    assert "backup" in seen.err


def test_an_update_leaves_a_v1_home_alone(legacy_home, monkeypatch):
    """A home that already has the V1 `library.yaml` is not legacy, whatever
    else sits beside it -- no prompt, no backup, no change."""
    from kraft import cli

    (legacy_home / "library.yaml").write_text("tasks: {}\n")
    before = _tree(legacy_home)
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked about a V1 home"))

    cli.main(["admin", "update"])

    assert _tree(legacy_home) == before
    assert _backups(legacy_home) == []


#: A pre-V1 `policy.yaml` an operator tuned: values V1 still has a key for, a
#: legacy fix-loop cap V1 never reads, a knob V1's schema has no key for, and
#: a value V1 refuses.
LEGACY_POLICY = {
    "loops": {
        "verify_fix_loop": {"attempts": 5, "wall_clock_s": 3600},
        "ci_wait": {"attempts": 90, "wall_clock_s": 2700},
    },
    "default": {"attempts": 4, "wall_clock_s": 1800},
    "findings": {"loop_severities": ["critical"]},
    "budget": {"work_item_usd": 25, "daily_usd": 500},
    "rate_limit_retries": 20,
    "forge_cli_timeout_s": 600,
    "max_concurrent": 7,
    "escalate_after": 3,
    "archive": {"after_days": -1},
}


def _update_with_policy(home, capsys) -> tuple[dict, str]:
    from kraft import cli

    (home / "policy.yaml").write_text(yaml.safe_dump(LEGACY_POLICY))
    cli.main(["admin", "update", "-y"])
    captured = capsys.readouterr()
    return yaml.safe_load((home / "policy.yaml").read_text()), captured.out + captured.err


def test_a_major_update_keeps_the_policy_values_v1_still_has(legacy_home, capsys):
    """Ruling 172: a spend cap or a forge timeout an operator set survives the
    update. Every key the V1 policy schema still has keeps its value, and the
    result is a valid V1 policy."""
    from kraft.policy import PolicyInput

    policy, _ = _update_with_policy(legacy_home, capsys)

    assert policy["budget"] == {"work_item_usd": 25, "daily_usd": 500}
    assert policy["rate_limit_retries"] == 20
    assert policy["forge_cli_timeout_s"] == 600
    assert policy["max_concurrent"] == 7
    assert policy["findings"] == {"loop_severities": ["critical"]}
    assert policy["default"] == {"attempts": 4, "wall_clock_s": 1800}
    PolicyInput.from_yaml(legacy_home / "policy.yaml")


def test_a_major_update_reports_each_policy_key_it_drops(legacy_home, capsys):
    """What V1 has no key for is dropped -- and said, with the value it had, so
    the operator can see what the backup holds that the new file does not."""
    policy, output = _update_with_policy(legacy_home, capsys)

    assert "verify_fix_loop" not in policy["loops"]
    assert "escalate_after" not in policy
    assert "loops.verify_fix_loop" in output and "'attempts': 5" in output
    assert "escalate_after = 3" in output
    # A key V1 has, with a value it refuses: dropped too, the seed's kept.
    assert "archive = {'after_days': -1}" in output
    assert policy["archive"] == {"after_days": 30}
    # Task 9 retired `loops.ci_wait`: a wait's timeout is its task's own now.
    assert "ci_wait" not in policy["loops"]
    assert "loops.ci_wait" in output and "'attempts': 90" in output


@pytest.mark.parametrize("next_step", ["start", "update"])
def test_a_crash_between_the_swap_renames_is_finished_not_reseeded(
    legacy_home, monkeypatch, capsys, next_step
):
    """Kraft-cttgx. Killed after the old home moved to its backup and before the
    new one moved into place, the home is missing. The next start -- or the next
    update -- finishes the swap from the staged copy and says so; it never seeds
    vanilla defaults over the operator's configuration."""
    from kraft import cli

    before = _tree(legacy_home)
    real_rename = pathlib.Path.rename
    renames = []

    def rename(self, target):
        renames.append(self)
        if len(renames) == 2:
            raise OSError("power cut")
        return real_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", rename)
    with pytest.raises(OSError, match="power cut"):
        cli.main(["admin", "update", "-y"])
    monkeypatch.setattr(pathlib.Path, "rename", real_rename)
    assert not legacy_home.exists()  # the crash point
    capsys.readouterr()

    if next_step == "start":
        assert cli.seed_home(legacy_home) is False
    else:
        cli.main(["admin", "update"])

    assert (legacy_home / "library.yaml").is_file()
    for name, text in MACHINE.items():
        assert (legacy_home / name).read_text() == text
    [backup] = _backups(legacy_home)
    assert _tree(backup) == before
    assert "interrupted" in capsys.readouterr().err


def test_a_staging_dir_no_update_finished_writing_is_never_installed(legacy_home, capsys):
    """A `templates.seeding` beside a backup is not proof of a staged update: an
    interrupted first seed leaves one too. Only a staging the update marked
    complete is installed; anything else is seeded over as before."""
    from kraft import cli

    cli.main(["admin", "update", "-y"])
    shutil.rmtree(legacy_home)
    partial = legacy_home.with_name(legacy_home.name + ".seeding")
    partial.mkdir()
    (partial / "junk.yaml").write_text("half: written\n")

    assert cli.seed_home(legacy_home) is True

    assert (legacy_home / "library.yaml").is_file()
    assert not (legacy_home / "junk.yaml").exists()
