"""`kraft.adapters.agent` launching into a docker sandbox: a tool policy is
enforced in the container or the launch is refused, never run unenforced."""

import pytest

from kraft.adapters.agent import LaunchRefused
from kraft.paths import RunDirs

DOCKER = {"kind": "docker", "image": "x"}


@pytest.mark.parametrize("harness", ["codex", "cursor"])
@pytest.mark.parametrize(
    "policy",
    [
        {"deny_tools": ("Bash",)},
        # Web tools never reach either hook, so an allowlist must name them.
        {"allowed_tools": ("Read", "WebFetch", "WebSearch")},
    ],
    ids=["deny", "allowlist"],
)
def test_a_hook_enforced_policy_is_refused_in_a_sandbox(run, tmp_path, harness, policy):
    """The hook is a host command calling Kraft's local API; in a container it
    crashes, and a crashed hook lets the call through."""
    with pytest.raises(LaunchRefused, match="cannot run inside a sandbox"):
        run(
            harness=harness,
            command=harness,
            run_dirs=RunDirs(base=tmp_path),
            sandbox=DOCKER,
            **policy,
        )


def test_a_proxy_unaware_harness_under_network_is_refused_before_launch(run, tmp_path):
    """Its only route out would be a proxy it ignores (spec §4)."""
    networked = {**DOCKER, "network": {"runtime": {"allow": ["x.io"]}}}
    with pytest.raises(LaunchRefused, match=r"'cursor'.*`proxy_aware: false`"):
        run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path), sandbox=networked)
    assert run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)


def test_a_hooked_harness_with_nothing_to_enforce_still_runs_sandboxed(run, tmp_path):
    seen = run(harness="codex", command="codex", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)
    assert seen["sandbox"] == DOCKER


def test_an_amp_rules_file_is_mounted_read_only_in_the_sandbox(run, tmp_path):
    """Amp silently falls back to its built-in rules for a settings file it
    cannot find, and the worker must not rewrite the one it can."""
    seen = run(
        harness="amp",
        command="amp",
        run_dirs=RunDirs(base=tmp_path),
        sandbox=DOCKER,
        deny_tools=("Bash",),
    )
    assert seen["ro_paths"] == (str(tmp_path / "harness-config" / "amp" / "s1.json"),)


def test_a_sandboxed_item_gets_a_cli_config_dir_of_its_own(run, tmp_path):
    """The shared directory would be one item's worker's to rewrite under
    every other item's launches; the item's sandbox home is already mounted."""
    seen = run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)
    own = tmp_path / "sandbox-home" / "w1" / ".kraft-harness-config" / "cursor"
    assert seen["env"]["CURSOR_CONFIG_DIR"] == str(own)
    assert (own / "cli-config.json").is_file()
    unsandboxed = run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path))
    assert unsandboxed["env"]["CURSOR_CONFIG_DIR"] == str(tmp_path / "harness-config" / "cursor")


@pytest.mark.parametrize(
    "sandbox, mode, expected",
    [
        (DOCKER, None, "danger-full-access"),
        (DOCKER, "workspace-write", "danger-full-access"),
        (DOCKER, "read-only", "read-only"),
        (None, None, "workspace-write"),
    ],
    ids=["sandboxed-default", "sandboxed-default-named", "sandboxed-own-mode", "host"],
)
def test_codex_leaves_isolation_to_the_container(run, tmp_path, sandbox, mode, expected):
    """Codex's bubblewrap cannot create a namespace under Docker's seccomp
    profile, so every shell command fails; a mode the launch chose itself is
    kept."""
    seen = run(
        harness="codex",
        command="codex",
        run_dirs=RunDirs(base=tmp_path),
        sandbox=sandbox,
        **({"permission_mode": mode} if mode else {}),
    )
    assert f"sandbox_mode={expected}" in seen["cmd"]
